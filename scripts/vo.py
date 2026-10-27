#!/usr/bin/env python3
"""voiceover-cleanup: turn a raw voiceover (with retakes, false starts, fillers) into a clean one that follows a script.

Scripts do the mechanical work; Claude (the voiceover-editor agent) makes every editing decision in work/cuts.json.

  vo setup                                          check ffmpeg, download the speech model if missing
  vo prepare  PROJECT --script FILE --audio FILE [FILE …] [--alias heard=script …]
                                                    transcribe + align → PROJECT/work/takes.md (the prepared view)
  vo probe    PROJECT START END                     speech blocks and words in a time range of the raw audio
  vo retranscribe PROJECT START END                 re-transcribe a range on its own, rebuild takes.md
  vo draft    PROJECT                               work/cuts.draft.json from the rule "last complete take wins"
  vo validate PROJECT                               check work/cuts.json (errors block apply)
  vo apply    PROJECT                               work/cuts.json → PROJECT/clean.wav + PROJECT/review.html
  vo serve    PROJECT [--port N]                    serve the review page on 127.0.0.1 (seeking works in long WAVs)
  vo edit     PROJECT [--port N]                    voiceover editor on 127.0.0.1: drag pieces of the original into
                                                    the clean timeline → PROJECT/clean_edited.wav

PROJECT is a folder (created by prepare) holding everything for one voiceover: script.md (a copy of the script),
work/ (source.wav, transcript, alignment, takes.md, cuts.json, …), clean.wav (the result), and review.html.
Times are seconds in work/source.wav (all input files joined end to end, in the order given).
"""
import argparse, shutil, sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent / "vo_lib"))
from common import setup_logging, log, project_paths, Script, read_json, write_json, fmt_time

AUDIO_EXT = {".wav", ".mp3", ".m4a", ".mp4", ".mov", ".flac", ".aac", ".ogg", ".opus", ".webm", ".mkv", ".aif", ".aiff"}

# ---------------------------------------------------------------- prepare
def prepare_view(script, W, aliases=None):
    from align import run_align
    from takes import audio_facts, build_view
    if aliases is None:
        st = W / "state.json"
        aliases = read_json(st).get("aliases", {}) if st.exists() else {}
    al = run_align(read_json(W / "transcript.json"), script, W, aliases)
    facts = audio_facts(W)
    view = build_view(script, al, facts, W)
    return al, facts, view

def _snapper(W):
    import soundfile as sf
    from audio import Snapper
    x, sr = sf.read(W / "source.wav", dtype="float32")
    return Snapper(x if x.ndim == 1 else x.mean(axis=1), sr), len(x) / sr

def stretched_ranges(W, pad=3.0):
    """Ranges around each stretched word, widened to the edges of the speech blocks they cut into, merged."""
    from plan_blocks import blocks
    from transcribe import stretched_words
    words = read_json(W / "transcript.json")["words"]
    sn, dur = _snapper(W)
    out = []
    for w in stretched_words(words):
        t0, t1 = max(0.0, w["start"] - pad), min(dur, w["end"] + pad)
        bl = blocks(sn, max(0.0, t0 - 3), min(dur, t1 + 3), 0.30)
        t0 = next((b0 for b0, b1 in bl if b0 <= t0 <= b1), t0)        # don't start or end inside speech
        t1 = next((b1 for b0, b1 in bl if b0 <= t1 <= b1), t1)
        if out and t0 <= out[-1][1]:
            out[-1][1] = max(out[-1][1], t1)
        else:
            out.append([max(0.0, t0 - 0.1), min(dur, t1 + 0.1)])
    return out

def silent_blocks(W, min_len=0.25):
    """Speech blocks in the audio (split at ≥0.30 s quiet) that no transcribed word overlaps: speech the ASR dropped."""
    from plan_blocks import blocks
    words = read_json(W / "transcript.json")["words"]
    sn, dur = _snapper(W)
    return [(max(0.0, a - 0.15), min(dur, b + 0.15)) for a, b in blocks(sn, 0, dur, 0.30)
            if b - a >= min_len and not any(w["start"] < b and w["end"] > a for w in words)]

def stretched_summary(words):
    from transcribe import stretched_words
    st = stretched_words(words)
    return len(st), sum(w["end"] - w["start"] for w in st)

def parse_aliases(items):
    out = {}
    for it in items or []:
        if "=" not in it:
            raise SystemExit(f"--alias needs heard=script, got '{it}'")
        k, v = (x.strip().lower() for x in it.split("=", 1))
        out[k] = v
    return out

def cmd_prepare(a, P):
    W = P["work"]
    srcs = [Path(x).expanduser().resolve() for x in a.audio]
    for s in srcs:
        if not s.is_file():
            raise SystemExit(f"audio file not found: {s}")
        if s.suffix.lower() not in AUDIO_EXT:
            log.warning("%s: unusual extension, trying it anyway (ffmpeg decides)", s.name)
    src_script = Path(a.script).expanduser().resolve()
    if not src_script.is_file():
        raise SystemExit(f"script not found: {src_script}")
    P["root"].mkdir(parents=True, exist_ok=True); W.mkdir(parents=True, exist_ok=True)
    if src_script != P["script"]:
        if (W / "cuts.json").exists() and P["script"].exists() and P["script"].read_bytes() != src_script.read_bytes():
            log.warning("the script changed since work/cuts.json was written: its sentence ids may no longer match")
        shutil.copyfile(src_script, P["script"])
    script = Script(P["script"])
    log.info("script: %d sections, %d sentences, %d words", len(script.sections), len(script.sentences), len(script.tokens))
    aliases = parse_aliases(a.alias)
    from transcribe import transcribe
    tr = transcribe(srcs, W)
    state = read_json(W / "state.json") if (W / "state.json").exists() else {}
    state.update(script_source=str(src_script), audio=[str(s) for s in srcs], asr=tr["asr"], model=tr["model"],
                 aliases=aliases, stage="transcribed", review="none")
    write_json(W / "state.json", state)
    from takes import retranscribe
    if a.fill_gaps:
        # A false start right before its retake is often dropped in context but transcribed fine on its own.
        # Pure noise comes back empty.
        gaps = silent_blocks(W)
        filled = sum(bool(retranscribe(W, t0, t1)) for t0, t1 in gaps)
        log.info("filled gaps: %d of %d untranscribed speech blocks now have words", filled, len(gaps))
    if a.fix_stretched:
        for t0, t1 in stretched_ranges(W):
            new = retranscribe(W, t0, t1)
            log.info("re-transcribed stretched range %.2f–%.2f: %s", t0, t1, " ".join(w["w"] for w in new)[:90])
    al, facts, view = prepare_view(script, W, aliases)
    # sentences with NO take at all: re-transcribe the stretch between the neighbours' rule picks, once
    redo = []
    for i, v in enumerate(view):
        if v["takes"]:
            continue
        prev = next((view[j] for j in range(i - 1, -1, -1) if view[j]["rule_pick"] is not None), None)
        nxt = next((view[j] for j in range(i + 1, len(view)) if view[j]["rule_pick"] is not None), None)
        t0 = prev["takes"][prev["rule_pick"]]["end"] if prev else 0.0
        t1 = nxt["takes"][nxt["rule_pick"]]["start"] if nxt else facts["duration"]
        if 0.5 < t1 - t0 < 60:
            rng = (round(max(0, t0 - 0.15), 2), round(t1 + 0.15, 2))
            if redo and redo[-1][0] == rng:           # consecutive missing sentences share one range: transcribe it once
                redo[-1][1].append(v["id"])
            else:
                redo.append((rng, [v["id"]]))
    for (t0, t1), sids in redo:
        new = retranscribe(W, t0, t1)
        log.info("re-transcribed %s (%.2f–%.2f): %s", ", ".join(sids), t0, t1, " ".join(w["w"] for w in new)[:90])
    if redo:
        al, facts, view = prepare_view(script, W, aliases)
    state.update(stage="prepared"); write_json(W / "state.json", state)
    no_take = [v["id"] for v in view if not v["takes"]]
    no_complete = [v["id"] for v in view if v["takes"] and v["rule_pick"] is None]
    n_raw, s_raw = stretched_summary(read_json(W / "transcript.raw.json")["words"])
    n_now, s_now = stretched_summary(read_json(W / "transcript.json")["words"])
    gaps_left = silent_blocks(W)
    print(f"\nPREPARED: {len(view)} sentences · {len(view) - len(no_take) - len(no_complete)} with a complete take · "
          f"{len(no_complete)} only partial · {len(no_take)} not found")
    print(f"  transcribed with {tr['asr']} {tr['model']} · RTF {tr['realtime_factor']} · model load {tr['load_seconds']}s")
    print(f"  speech blocks with no words (dropped speech): {len(gaps_left)} ({sum(b - a for a, b in gaps_left):.1f}s incl. margins)")
    print(f"  stretched words (>1.5 s, likely dropped speech): {n_now} ({s_now:.1f}s)"
          + (f" · first pass {n_raw} ({s_raw:.1f}s)" if (n_raw, round(s_raw, 1)) != (n_now, round(s_now, 1)) else ""))
    print(f"  raw audio {facts['duration'] / 60:.1f} min · loudness {facts['loudness_lufs']} LUFS · noise floor {facts['noise_floor_db']} dB")
    print(f"  read: {W / 'takes.md'}")

# ---------------------------------------------------------------- helpers for deciding
def cmd_probe(a, P):
    from plan_blocks import blocks
    W = P["work"]; script = Script(P["script"])
    sn, _ = _snapper(W)
    print(f"range {fmt_time(a.start)}–{fmt_time(a.end)}")
    print("speech blocks (split at ≥0.30 s quiet):")
    for b0, b1 in blocks(sn, a.start, a.end, 0.30):
        print(f"  {b0:8.2f}–{b1:8.2f}  ({b1 - b0:.2f}s)")
    print("words:")
    for w in read_json(W / "alignment.json")["words"]:
        if a.start <= w["start"] <= a.end:
            sid = script.sentences[w["sent"]].id if w["sent"] is not None else "-"
            print(f"  {w['start']:8.2f}–{w['end']:8.2f}  {w['kind']:7}  {sid:9}  {w['w']}")

def cmd_retranscribe(a, P):
    from takes import retranscribe
    W = P["work"]
    new = retranscribe(W, a.start, a.end)
    print(" ".join(w["w"] for w in new))
    prepare_view(Script(P["script"]), W)
    print(f"view rebuilt: {W / 'takes.md'}")

def cmd_draft(a, P):
    W = P["work"]
    view = read_json(W / "takes.json")["sentences"]
    segs, dropped = [], []
    for vv in view:
        for i, t in enumerate(vv["takes"]):
            if i != vv["rule_pick"]:
                dropped.append(dict(start=t["start"], end=t["end"], what="other take" if t["complete"] else "partial take / false start",
                                    reason=f"{vv['id']}: not the last complete take"))
        if vv["rule_pick"] is None:
            continue
        t = vv["takes"][vv["rule_pick"]]
        start = t["audio_start"] if t.get("audio_start") is not None else t["start"]
        segs.append(dict(sentence=vv["id"], start=start, end=t["end"], remove=[], pause_after=None,
                         reason=f"last complete take (t{vv['rule_pick']} of {len(vv['takes'])})"
                                + (f"; start from audio ({t['audio_note']})" if t.get("audio_note") else "")))
    write_json(W / "cuts.draft.json", dict(project=P["root"].name, author="rule draft — Claude must review", segments=segs,
                                           dropped=dropped, notes=""))
    print(f"draft: {len(segs)} segments, {len(dropped)} dropped → {W / 'cuts.draft.json'} (review it, then save as cuts.json)")

def cmd_validate(a, P):
    from audio import validate
    W = P["work"]
    if not (W / "cuts.json").exists():
        raise SystemExit(f"no {W / 'cuts.json'} yet")
    errs, warns = validate(read_json(W / "cuts.json"), Script(P["script"]), read_json(W / "audio_facts.json")["duration"])
    for e in errs:
        print("ERROR  ", e)
    for w in warns:
        print("warning", w)
    print("OK" if not errs else f"{len(errs)} error(s)")
    sys.exit(1 if errs else 0)

# ---------------------------------------------------------------- apply
def cmd_apply(a, P):
    W = P["work"]
    if not (W / "cuts.json").exists():
        raise SystemExit(f"no {W / 'cuts.json'} yet — Claude writes it after reading takes.md")
    from audio import build
    from review import write
    cuts = read_json(W / "cuts.json")
    res = build(cuts, read_json(W / "transcript.json"), Script(P["script"]), W)
    (W / "clean.wav").replace(P["clean"])
    clean = read_json(W / "clean_words.json"); clean["source_duration"] = res["source_duration"]
    page = write(P["root"].name, P, cuts, clean, status="awaiting review")
    st = read_json(W / "state.json"); st.update(stage="applied", review="awaiting review"); write_json(W / "state.json", st)
    print(f"\nCLEAN VOICEOVER: {P['clean']} ({res['duration'] / 60:.1f} min, from {res['source_duration'] / 60:.1f} min raw)")
    for w in res["warnings"]:
        print(f"  ⚠ {w}")
    print(f"REVIEW PAGE: {page}  (vo serve {P['root']} → http://127.0.0.1:{a.port if hasattr(a, 'port') else 8801}/review.html)")

def cmd_serve(a, P):
    from serve import serve
    serve(P["root"], a.port)

def cmd_edit(a, P):
    from editor import edit
    edit(P, a.port)

def cmd_setup(a, P):
    from transcribe import ensure_models, models_dir
    for tool in ("ffmpeg", "ffprobe"):
        if not shutil.which(tool):
            raise SystemExit(f"{tool} not found on PATH: install ffmpeg (e.g. apt install ffmpeg / brew install ffmpeg)")
    paths = ensure_models()
    print(f"OK: ffmpeg found; models in {models_dir()}")
    for p in paths:
        print(f"  {p}")

def main():
    ap = argparse.ArgumentParser(prog="vo", description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("-v", "--verbose", action="store_true")
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("setup", help="check ffmpeg and download the speech model if missing")
    p = sub.add_parser("prepare", help="transcribe and build the prepared view")
    p.add_argument("project"); p.add_argument("--script", required=True); p.add_argument("--audio", required=True, nargs="+")
    p.add_argument("--alias", action="append", metavar="HEARD=SCRIPT",
                   help="a word the speech model mishears, e.g. --alias zentra=zyntra (repeatable)")
    p.add_argument("--fill-gaps", action=argparse.BooleanOptionalAction, default=True,
                   help="re-transcribe, on its own, every speech block that got no words (default: on)")
    p.add_argument("--fix-stretched", action="store_true", help="also re-transcribe around every stretched word (>1.5 s)")
    for name in ("probe", "retranscribe"):
        q = sub.add_parser(name); q.add_argument("project"); q.add_argument("start", type=float); q.add_argument("end", type=float)
    for name in ("draft", "validate"):
        sub.add_parser(name).add_argument("project")
    q = sub.add_parser("apply"); q.add_argument("project"); q.add_argument("--port", type=int, default=8801, help=argparse.SUPPRESS)
    q = sub.add_parser("serve"); q.add_argument("project"); q.add_argument("--port", type=int, default=8801)
    q = sub.add_parser("edit", help="open the voiceover editor (original + clean timelines)")
    q.add_argument("project"); q.add_argument("--port", type=int, default=8802)
    a = ap.parse_args(); setup_logging(a.verbose)
    P = project_paths(a.project) if hasattr(a, "project") else None
    if a.cmd not in ("setup", "prepare") and not P["script"].exists():
        raise SystemExit(f"{P['root']} is not a prepared project (no script.md): run `vo prepare` first")
    globals()["cmd_" + a.cmd](a, P)

if __name__ == "__main__":
    main()
