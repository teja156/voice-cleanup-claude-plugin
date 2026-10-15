"""Stage 1 — transcribe: input audio/video file(s) → work/source.wav (48 kHz mono) → work/transcript.raw.json + transcript.json.

Several input files are joined end to end, in the order given (work/sources.json records where each one starts).

transcript.raw.json is the first pass and is never changed afterwards. transcript.json is the working copy:
takes.retranscribe() splices re-transcribed ranges into it (and lists them under "retranscribed").

The only backend is NVIDIA Parakeet TDT 0.6B v2 (English) via onnx-asr, int8 on CPU, with Silero VAD. It is a transducer:
it decodes the audio in order, so it keeps the repeated takes and false starts a raw voiceover is full of (Whisper skipped
them). Weights CC-BY-4.0 (NVIDIA), ONNX export by istupakov, onnx-asr MIT.
It takes no prompt. Word ends are estimated (the model gives each token's start frame only).

The model is NOT shipped with this plugin. On first use it is downloaded from Hugging Face (pinned revisions, about
640 MB) into the models folder (models_dir(): $VOICEOVER_CLEANUP_MODELS, else <data dir>/models), and loaded from
there with no network access afterwards.
"""
import os, time
from importlib import metadata
from pathlib import Path
from common import log, run, write_json, ffprobe_duration, read_json

ASR, MODEL = "parakeet", "nemo-parakeet-tdt-0.6b-v2"
PARAKEET = dict(repo="istupakov/parakeet-tdt-0.6b-v2-onnx", revision="0bbb45a3365852604aef28b538a8f066f4ccaa85", quantization="int8",
                vad_repo="istupakov/silero-vad-onnx", vad_revision="b3e3ee3cce4c11ceb63b1a0b229d916069c1ddf6")
FRAME_PK = 0.08           # Parakeet output frame (10 ms window step × subsampling 8)
STRETCHED = 1.5           # a word longer than this (s) is the best sign of speech the ASR dropped

def data_dir():
    """Persistent folder for the plugin's venv and models (outside the plugin, so updates keep them)."""
    d = os.environ.get("VOICEOVER_CLEANUP_HOME") or os.environ.get("CLAUDE_PLUGIN_DATA")
    return Path(d).expanduser() if d else Path.home() / ".local" / "share" / "voiceover-cleanup"

def models_dir():
    d = os.environ.get("VOICEOVER_CLEANUP_MODELS")
    return Path(d).expanduser() if d else data_dir() / "models"

def to_source_wav(srcs, dst):
    """Convert one or more audio/video files to one 48 kHz mono 16-bit WAV, joined end to end in the given order.
    Returns (duration, [{file, start, end}])."""
    parts, t = [], 0.0
    if len(srcs) == 1:
        run(["ffmpeg", "-y", "-v", "error", "-i", str(srcs[0]), "-vn", "-ac", "1", "-ar", "48000", "-c:a", "pcm_s16le", str(dst)])
    else:
        cmd = ["ffmpeg", "-y", "-v", "error"]
        for s in srcs:
            cmd += ["-i", str(s)]
        chain = "".join(f"[{i}:a:0]aresample=48000,aformat=channel_layouts=mono[a{i}];" for i in range(len(srcs)))
        chain += "".join(f"[a{i}]" for i in range(len(srcs))) + f"concat=n={len(srcs)}:v=0:a=1[out]"
        run(cmd + ["-filter_complex", chain, "-map", "[out]", "-ac", "1", "-ar", "48000", "-c:a", "pcm_s16le", str(dst)])
    for s in srcs:
        d = ffprobe_duration(s)
        parts.append(dict(file=str(s), start=round(t, 3), end=round(t + d, 3))); t += d
    return ffprobe_duration(dst), parts

def stretched_words(words, min_len=STRETCHED):
    return [w for w in words if w["end"] - w["start"] > min_len]

def _version(pkg):
    try:
        return metadata.version(pkg)
    except metadata.PackageNotFoundError:
        return None

def _snapshot_revision(path):
    """Hugging Face commit hash from a cache path like …/models--org--name/snapshots/<hash>, else None."""
    p = Path(path)
    return p.name if p.parent.name == "snapshots" else None

# ---------------------------------------------------------------- model (cached per process)
_MODELS = {}

def _fetch(repo, revision, patterns=None):
    """Local copy of a pinned model snapshot in models_dir(); downloaded once, the first time it is needed."""
    from huggingface_hub import snapshot_download
    kw = dict(revision=revision, cache_dir=str(models_dir()), allow_patterns=patterns)
    try:
        return snapshot_download(repo, local_files_only=True, **kw)
    except Exception:
        log.info("model %s@%s not in %s yet: downloading it once (needs internet)", repo, revision[:8], models_dir())
        models_dir().mkdir(parents=True, exist_ok=True)
        return snapshot_download(repo, **kw)

def ensure_models():
    """Download the models now if they are missing (used by `vo setup`). Returns their local paths."""
    q = PARAKEET["quantization"]
    return (_fetch(PARAKEET["repo"], PARAKEET["revision"], [f"*.{q}.onnx", "config.json", "nemo128.onnx", "vocab.txt"]),
            _fetch(PARAKEET["vad_repo"], PARAKEET["vad_revision"]))

def _parakeet(model):
    key = ("parakeet", model)
    if key not in _MODELS:
        import onnx_asr
        t0 = time.time()
        q = PARAKEET["quantization"]
        path, vad_path = ensure_models()
        asr = onnx_asr.load_model(model, path, quantization=q)
        vad = onnx_asr.load_vad("silero", vad_path)
        # Silero VAD splits the file into ≤20 s pieces at pauses (the model takes about 20–30 s at a time). Short pieces
        # matter: with 300 ms splits a false start merged into the next take was dropped; 200 ms kept it.
        m = asr.with_vad(vad, min_silence_duration_ms=200, speech_pad_ms=100, max_speech_duration_s=20).with_timestamps()
        _MODELS[key] = (m, dict(package="onnx-asr", version=_version("onnx-asr"), model_repo=PARAKEET["repo"],
                                model_revision=_snapshot_revision(path), quantization=q,
                                vad_revision=_snapshot_revision(vad_path), load_seconds=round(time.time() - t0, 1)))
    return _MODELS[key]

def _parakeet_words(seg):
    """Join one VAD piece's tokens into words. A token starting with a space starts a new word. Start = first token's
    frame; end = last token's frame + 1.5 frames, capped at the next word and the piece end (estimated: the model
    gives start frames only). p = exp(mean token log-probability), the model's own confidence."""
    import math
    toks, ts, lps = seg.tokens or [], seg.timestamps or [], seg.logprobs or [None] * len(seg.tokens or [])
    groups = []
    for tok, t, lp in zip(toks, ts, lps):
        if not groups or tok.startswith(" "):
            groups.append([tok.strip(), seg.start + t, seg.start + t, [lp]])
        else:
            groups[-1][0] += tok; groups[-1][2] = seg.start + t; groups[-1][3].append(lp)
    out = []
    for i, (w, a, last, lp) in enumerate(groups):
        nxt = groups[i + 1][1] if i + 1 < len(groups) else seg.end
        b = max(a + FRAME_PK, min(last + 1.5 * FRAME_PK, nxt, seg.end))
        lp = [x for x in lp if x is not None]
        out.append(dict(w=w, start=round(a, 3), end=round(b, 3), p=round(math.exp(sum(lp) / len(lp)), 3) if lp else None))
    return [w for w in out if w["w"]]

def asr_words(wav):
    """Transcribe one audio file with Parakeet. Returns (words, segments, meta). Word times are seconds from the start of
    `wav`. words: [{w, start, end, p}], p = the model's confidence. segments: [{start, end, text}], one per VAD piece."""
    m, meta = _parakeet(MODEL)
    words, segs = [], []
    for seg in m.recognize(str(wav)):
        words += _parakeet_words(seg)
        segs.append(dict(start=round(seg.start, 3), end=round(seg.end, 3), text=seg.text.strip()))
    return words, segs, dict(meta, asr=ASR, model=MODEL, word_ends="estimated")

def transcribe(srcs, work):
    work.mkdir(parents=True, exist_ok=True)
    wav = work / "source.wav"
    dur, parts = to_source_wav(srcs, wav)
    write_json(work / "sources.json", parts)
    log.info("source: %s (%.1f min) → %s", ", ".join(Path(s).name for s in srcs), dur / 60, wav)
    t0 = time.time()
    words, segs, meta = asr_words(wav)
    took = time.time() - t0
    out = dict(source=[str(s) for s in srcs], wav=str(wav), duration=dur, asr=meta["asr"], model=meta["model"],
               package=meta["package"], package_version=meta["version"], model_revision=meta.get("model_revision"),
               model_repo=meta.get("model_repo"), load_seconds=meta["load_seconds"], seconds=round(took, 1),
               realtime_factor=round(took / max(dur, 1e-6), 3), quantization=meta["quantization"], vad_revision=meta["vad_revision"],
               word_ends=meta["word_ends"], confidence=any(w.get("p") is not None for w in words), words=words, segments=segs)
    write_json(work / "transcript.raw.json", out)       # first pass, never changed afterwards
    write_json(work / "transcript.json", out)           # working copy (retranscribe splices into this one)
    st = stretched_words(words)
    log.info("transcribed %d words in %.1f min (RTF %.2f, i.e. %.1f× faster than real time; model load %.1fs) with %s %s · "
             "%d stretched words (>%.1fs, %.1fs total)",
             len(words), took / 60, took / max(dur, 1e-6), dur / max(took, 1e-6), meta["load_seconds"], meta["asr"], meta["model"],
             len(st), STRETCHED, sum(w["end"] - w["start"] for w in st))
    return out
