"""The PREPARED VIEW for Claude: evidence only, no editing decisions.

From transcript.json + alignment.json + the audio it builds, per script sentence:
  - every candidate take (a run of spoken words moving forward through the sentence): times, coverage, whether it
    covers the start and end, the ASR's mean confidence (if it gives one), and the words heard
  - the take the default rule would pick ("last complete take wins") — a hint, Claude decides
  - nearby evidence: asides/extra words, fillers the ASR kept, isolated sounds it dropped, stretched words, long pauses inside takes
Writes work/takes.json (full) and work/takes.md (compact, for reading), plus work/audio_facts.json.
"""
import numpy as np
import soundfile as sf
from common import log, write_json, read_json, fmt_time, run
from audio import Snapper

MAX_RUN_GAP, MAX_JUMP, COMPLETE, EDGE = 2.0, 4, 0.70, 2

def _conf(ws):
    """Mean ASR confidence of a take, or None when the backend gives none (never invent a number)."""
    ps = [w["p"] for w in ws if w.get("p") is not None]
    return round(float(np.mean(ps)), 2) if ps else None

def find_takes(sent, words):
    a, b = sent.tok_start, sent.tok_end
    runs, cur = [], []
    for w in (w for w in words if w["kind"] == "aligned"):
        if not (a <= w["pos"] < b):
            if cur:
                runs.append(cur); cur = []
            continue
        if cur and (w["pos"] < cur[-1]["pos"] or w["pos"] - cur[-1]["pos"] > MAX_JUMP or w["start"] - cur[-1]["end"] > MAX_RUN_GAP):
            runs.append(cur); cur = []
        cur.append(w)
    if cur:
        runs.append(cur)
    out, n = [], b - a
    for r in runs:
        cov = {w["pos"] for w in r}
        edge = 0 if n <= 4 else EDGE
        starts, ends = min(cov) <= a + edge, max(cov) >= b - 1 - edge
        out.append(dict(start=r[0]["start"], end=r[-1]["end"], first_word=r[0]["i"], last_word=r[-1]["i"],
                        coverage=round(len(cov) / max(1, n), 2), has_start=starts, has_end=ends,
                        complete=bool(len(cov) / max(1, n) >= COMPLETE and starts and ends),
                        conf=_conf(r), text=" ".join(w["w"] for w in r)))
    return out

def audio_facts(work):
    x, sr = sf.read(work / "source.wav", dtype="float32")
    x = x if x.ndim == 1 else x.mean(axis=1)
    sn = Snapper(x, sr)
    sil, i, q = [], 0, sn.quiet
    while i < len(q):
        if q[i]:
            j = i
            while j < len(q) and q[j]:
                j += 1
            if (j - i) * 0.01 >= 0.30:
                sil.append([round(i * 0.01, 2), round(j * 0.01, 2)])
            i = j
        else:
            i += 1
    bursts = sn.bursts(0, len(x) / sr)
    p = run(["ffmpeg", "-hide_banner", "-nostats", "-i", str(work / "source.wav"), "-af", "loudnorm=print_format=json", "-f", "null", "-"])
    import json
    ln = json.loads(p.stderr[p.stderr.rindex("{"): p.stderr.rindex("}") + 1])
    facts = dict(duration=round(len(x) / sr, 2), noise_floor_db=round(20 * np.log10(sn.floor + 1e-12), 1),
                 loudness_lufs=float(ln["input_i"]), true_peak_db=float(ln["input_tp"]),
                 silences=sil, isolated_sounds=[[a, b] for a, b in bursts])
    write_json(work / "audio_facts.json", facts)
    return facts

def audio_start(take, words, blocks):
    """Where speech for this take really starts, from the audio. The ASR often stamps the first word after a pause at the
    END of the previous speech block (so it sits in silence), or gives the take's first word to the previous take.
    Returns (onset, note) or (None, None) when the word timing already agrees with the audio."""
    t0 = take["start"]
    second = next((w["start"] for w in words[take["first_word"] + 1: take["last_word"] + 1] if w["kind"] == "aligned"), take["end"])
    cont = next((b for b in blocks if b[0] <= t0 <= b[1]), None)
    if cont and cont[0] < t0 - 0.30 and cont[1] < second:          # first word sits at the tail of earlier speech
        nxt = next((b for b in blocks if b[0] > cont[1]), None)
        if nxt and nxt[0] <= second + 0.10:
            return round(nxt[0], 2), f"words say {t0:.2f}, audio says speech starts {nxt[0]:.2f}"
    if cont is None:                                                # first word timed in silence
        nxt = next((b for b in blocks if b[0] > t0), None)
        if nxt and nxt[0] - t0 >= 0.25 and nxt[0] <= second + 0.10:   # small offsets are handled by edge snapping
            return round(nxt[0], 2), f"words say {t0:.2f} (in silence), audio says speech starts {nxt[0]:.2f}"
    if cont and t0 - cont[0] > 0.08 and cont[0] >= t0 - 0.30:       # speech starts a little before the first matched word
        return round(cont[0], 2), f"speech starts {cont[0]:.2f}, before the first matched word ({t0:.2f})"
    return None, None

def build_view(script, alignment, facts, work):
    titles = {s.n: s.title for s in script.sections}
    sources = read_json(work / "sources.json") if (work / "sources.json").exists() else []
    words = alignment["words"]
    iso = facts["isolated_sounds"]
    import soundfile as sf
    from plan_blocks import blocks as speech_blocks
    x, sr = sf.read(work / "source.wav", dtype="float32")
    sn = Snapper(x if x.ndim == 1 else x.mean(axis=1), sr)
    blocks = speech_blocks(sn, 0, len(x) / sr, 0.30)
    from transcribe import STRETCHED
    view = []
    for s in script.sentences:
        takes = find_takes(s, words)
        for t in takes:
            t["audio_start"], t["audio_note"] = audio_start(t, words, blocks)
            # speech blocks (split at ≥0.45 s quiet) between the audio start and the take end: >1 block can mean a merged
            # false start (a short block, then one long enough to be the whole sentence) or just a natural pause
            s0 = t["audio_start"] if t["audio_start"] is not None else t["start"]
            inside = [b for b in speech_blocks(sn, s0 - 0.05, t["end"] + 0.3, 0.45)]
            t["blocks"] = inside if len(inside) > 1 else []
        complete = [i for i, t in enumerate(takes) if t["complete"]]
        rule = complete[-1] if complete else None
        lo = min([t["start"] for t in takes], default=None); hi = max([t["end"] for t in takes], default=None)
        near = []
        if lo is not None:
            for w in words:
                if lo - 3 <= w["start"] <= hi + 1 and w["kind"] in ("extra", "filler", "event"):
                    near.append(dict(t=w["start"], kind=w["kind"], w=w["w"]))
            for a, b in iso:
                # only sounds no transcribed word explains (the ASR drops many "um"/"uh")
                if lo - 1 <= a <= hi + 0.5 and not any(w["start"] < b and w["end"] > a for w in words):
                    near.append(dict(t=a, kind="sound", w=f"{b - a:.2f}s sound, no word"))
            for t in takes:      # long pauses inside a take (possible restart or a merged false start)
                span = [w for w in words[t["first_word"]: t["last_word"] + 1]]
                for w1, w2 in zip(span, span[1:]):
                    if w2["start"] - w1["end"] >= 0.6:
                        near.append(dict(t=w1["end"], kind="pause", w=f"{w2['start'] - w1['end']:.2f}s pause inside take"))
        near.sort(key=lambda d: d["t"])
        view.append(dict(id=s.id, section=s.section, para=s.para, text=s.text, takes=takes, rule_pick=rule, near=near, stretched=[]))
    # stretched words (longer than STRETCHED s): the ASR very likely dropped speech there (a retake, a false start).
    # Filed under the sentence they align to; else the sentence whose takes span them; else the last one before them.
    spans = [(min(t["start"] for t in v["takes"]), max(t["end"] for t in v["takes"])) if v["takes"] else None for v in view]
    stretched = [w for w in words if w["end"] - w["start"] > STRETCHED]
    for w in stretched:
        k = w["sent"]
        if k is None:
            k = next((i for i, sp in enumerate(spans) if sp and sp[0] <= w["start"] <= sp[1]), None)
        if k is None:
            k = max((i for i, sp in enumerate(spans) if sp and sp[1] <= w["start"]), default=0)
        view[k]["stretched"].append(dict(w=w["w"], start=w["start"], end=w["end"], dur=round(w["end"] - w["start"], 2)))
    tr = read_json(work / "transcript.json")
    has_conf = tr.get("confidence", True)
    summary = dict(asr=tr.get("asr"), model=tr.get("model"), stretched_words=len(stretched),
                   stretched_seconds=round(sum(w["end"] - w["start"] for w in stretched), 1), confidence=has_conf)
    write_json(work / "takes.json", dict(rule="last complete take wins (hint only — Claude decides)", summary=summary, sentences=view))
    # compact markdown
    lines = ["# Prepared view — candidate takes per sentence",
             "Times are seconds in work/source.wav. ✓ = complete take (covers start and end, ≥70% of words). "
             "← rule = last complete take (the default rule; a hint). cov = share of the sentence's words heard. "
             + (f"conf = ASR confidence ({summary['asr']} {summary['model']})." if has_conf
                else f"conf = – (not available: {summary['asr']} gives no word confidence)."),
             f"stretched = a word lasting over {STRETCHED:.1f}s: the transcript very likely dropped speech there (a retake or a "
             f"false start). Probe that range. This transcript has {summary['stretched_words']} ({summary['stretched_seconds']}s in total).",
             ""]
    if len(sources) > 1:
        lines += ["Input files, joined in this order (a take never spans two files):"] + [
            f"  {fmt_time(s['start'])}–{fmt_time(s['end'])}  ({s['start']:.2f}–{s['end']:.2f})  {s['file']}" for s in sources] + [""]
    cur = None
    for v in view:
        if v["section"] != cur:
            cur = v["section"]; lines += ["", f"## Section {cur}" + (f" — {titles[cur]}" if titles.get(cur) else "")]
        lines.append(f'{v["id"]} "{v["text"]}"')
        if not v["takes"]:
            lines.append("    NO TAKE FOUND (consider: vo_tools.py retranscribe around the neighbours)")
        for i, t in enumerate(v["takes"]):
            mark = "✓" if t["complete"] else ("partial" + ("" if t["has_start"] else ", no start") + ("" if t["has_end"] else ", no end"))
            conf = "–" if t["conf"] is None else t["conf"]
            lines.append(f'    t{i} {t["start"]:.2f}–{t["end"]:.2f} cov {int(t["coverage"] * 100)}% conf {conf} {mark}'
                         f'{"  ← rule" if i == v["rule_pick"] else ""} | {t["text"][:110]}')
            if t.get("audio_note"):
                lines.append(f'        audio: {t["audio_note"]}')
            if t.get("blocks") and i == v["rule_pick"]:
                lines.append("        speech blocks: " + " | ".join(f"{b0:.2f}–{b1:.2f} ({b1 - b0:.2f}s)" for b0, b1 in t["blocks"]))
        for w in v["stretched"]:
            lines.append(f'    stretched: "{w["w"]}" {w["start"]:.2f}–{w["end"]:.2f} ({w["dur"]:.2f}s) — speech may be missing; '
                         f'probe {max(0, w["start"] - 3):.1f} {w["end"] + 3:.1f}')
        if v["near"]:
            lines.append("    near: " + "; ".join(f'{d["t"]:.2f} {d["kind"]} "{d["w"]}"' for d in v["near"][:8]))
    (work / "takes.md").write_text("\n".join(lines) + "\n")
    n_rule = sum(1 for v in view if v["rule_pick"] is not None)
    log.info("prepared view: %d sentences, %d with a complete take, %d without — %s", len(view), n_rule, len(view) - n_rule, work / "takes.md")
    return view

def retranscribe(work, start, end):
    """Re-transcribe [start, end] of source.wav on its own and splice the words into transcript.json.
    transcript.raw.json (the first pass) is never touched."""
    from transcribe import asr_words, ASR, MODEL
    tr = read_json(work / "transcript.json")
    if (tr.get("asr"), tr.get("model")) != (ASR, MODEL):
        raise SystemExit(f"this transcript was made with {tr.get('asr')} {tr.get('model')}, which is no longer supported: "
                         f"run vo_prepare.py again (it uses {ASR})")
    clip = work / f"_retx_{start:.2f}.wav"
    run(["ffmpeg", "-y", "-v", "error", "-ss", f"{start:.3f}", "-to", f"{end:.3f}", "-i", str(work / "source.wav"), str(clip)])
    try:
        words, _, meta = asr_words(clip)
    finally:
        clip.unlink(missing_ok=True)
    new = [dict(w, start=round(start + w["start"], 3), end=round(start + w["end"], 3)) for w in words]
    tr["words"] = [w for w in tr["words"] if w["end"] <= start + 0.05] + new + [w for w in tr["words"] if w["start"] >= end - 0.05]
    tr.setdefault("retranscribed", []).append(dict(start=start, end=end, asr=meta["asr"], model=meta["model"], words=len(new)))
    write_json(work / "transcript.json", tr)
    return new
