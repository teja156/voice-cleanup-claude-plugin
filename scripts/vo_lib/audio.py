"""Stage 4 — audio: cuts.json + source.wav → work/clean.wav (+ clean_words.json with every kept word's new time).

Edits only: cuts snapped to the audio's speech blocks, 12 ms crossfades at joins, room-tone pauses. No EQ, noise
reduction or loudness normalisation: the result is the recording itself, cut and joined."""
import json
import numpy as np
import soundfile as sf
from common import log, run, write_json

FRAME = 0.010                 # RMS frame (s)
XFADE = 0.012                 # crossfade at every join (s)
GAPS = dict(sentence=0.45, paragraph=0.85, section=1.10)   # pause inserted between non-adjacent takes
NATURAL_MAX = 0.70            # a natural pause between back-to-back takes longer than this is shortened
LEAD, TAIL = 0.50, 1.00

class Snapper:
    def __init__(self, x, sr):
        self.sr = sr
        n = int(FRAME * sr)
        frames = len(x) // n
        self.rms = np.sqrt(np.mean(x[: frames * n].reshape(frames, n) ** 2, axis=1) + 1e-12)
        self.floor = float(np.percentile(self.rms[self.rms > 1e-6], 10)) if np.any(self.rms > 1e-6) else 1e-4
        self.quiet = self.rms < max(self.floor * 3.0, 10 ** (-50 / 20))

    def _f(self, t):
        return int(np.clip(t / FRAME, 0, len(self.rms) - 1))

    def start(self, t, lo):
        """Cut point before speech at t: the end of the nearest quiet run (≥40 ms) before t, not earlier than lo."""
        i, stop = self._f(t), self._f(max(lo, t - 0.35))
        run_len = 0
        while i > stop:
            run_len = run_len + 1 if self.quiet[i] else 0
            if run_len >= 4:
                return max(lo, (i + run_len) * FRAME - 0.02)
            i -= 1
        return max(lo, t - 0.06)

    def end(self, t, hi):
        """Cut point after speech ending near t: the start of the first quiet run (≥60 ms) after t, not later than hi."""
        i, stop = self._f(t), self._f(min(hi, t + 0.45))
        run_len = 0
        while i < stop:
            run_len = run_len + 1 if self.quiet[i] else 0
            if run_len >= 6:
                return min(hi, (i - run_len + 1) * FRAME + 0.04)
            i += 1
        return min(hi, t + 0.08)

    def bursts(self, t0, t1, min_len=0.15, max_len=0.8, margin=0.20):
        """Isolated sound bursts between t0 and t1 (e.g. an 'uh' the transcript left out).

        Only bursts with at least `margin` seconds of real quiet (measured from the audio, not word timestamps)
        on BOTH sides count. The ASR often times a word as ending early, so its tail looks like a burst right
        after the word; requiring quiet on both sides keeps us from ever cutting real speech."""
        out, i, stop, cur = [], self._f(t0), self._f(t1), None
        loud = ~self.quiet
        while i <= stop:
            if loud[i] and cur is None:
                cur = i
            elif not loud[i] and cur is not None:
                ln = (i - cur) * FRAME
                q_before = 0
                while cur - 1 - q_before >= 0 and self.quiet[cur - 1 - q_before]:
                    q_before += 1
                q_after = 0
                while i + q_after < len(self.quiet) and self.quiet[i + q_after]:
                    q_after += 1
                if min_len <= ln <= max_len and q_before * FRAME >= margin and q_after * FRAME >= margin:
                    out.append((round(cur * FRAME - 0.03, 3), round(i * FRAME + 0.03, 3)))
                cur = None
            i += 1
        return out

def room_tone(x, sr, secs):
    """Loop the quietest 0.6 s of the recording to the requested length (crossfaded loops)."""
    w = int(0.6 * sr); hop = int(0.05 * sr)
    best, bi = None, 0
    for i in range(0, max(1, len(x) - w), hop):
        e = float(np.mean(x[i:i + w] ** 2))
        if best is None or e < best:
            best, bi = e, i
    tone = x[bi:bi + w].copy()
    out = np.zeros(int(secs * sr), np.float32)
    k, pos, fade = len(tone), 0, int(0.05 * sr)
    ramp = np.linspace(0, 1, fade, dtype=np.float32)
    while pos < len(out):
        seg = tone.copy()
        seg[:fade] *= ramp; seg[-fade:] *= ramp[::-1]
        end = min(len(out), pos + k)
        out[pos:end] += seg[: end - pos]
        pos += k - fade
    return out

def join(pieces, sr):
    """Concatenate with equal-power crossfades. Returns audio and each piece's start offset in the output."""
    f = int(XFADE * sr)
    out = pieces[0]["audio"].copy(); offsets = [0]
    for p in pieces[1:]:
        a = p["audio"]
        if len(out) > f and len(a) > f:
            t = np.linspace(0, np.pi / 2, f, dtype=np.float32)
            mix = out[-f:] * np.cos(t) + a[:f] * np.sin(t)
            offsets.append(len(out) - f)
            out = np.concatenate([out[:-f], mix, a[f:]])
        else:
            offsets.append(len(out)); out = np.concatenate([out, a])
    return out, offsets

def validate(cuts, script, duration):
    """Check a Claude-written cuts.json. Returns (errors, warnings)."""
    errs, warns = [], []
    ids = {s.id for s in script.sentences}
    seen = set()
    segs = cuts.get("segments", [])
    if not segs:
        errs.append("no segments")
    for i, g in enumerate(segs):
        tag = f"segment {i + 1} ({g.get('sentence')})"
        if g.get("sentence") not in ids:
            errs.append(f"{tag}: unknown sentence id")
        elif g["sentence"] in seen:
            errs.append(f"{tag}: sentence used twice")
        seen.add(g.get("sentence"))
        if not (0 <= g.get("start", -1) < g.get("end", -1) <= duration + 0.5):
            errs.append(f"{tag}: start/end must satisfy 0 ≤ start < end ≤ {duration:.1f}")
        for r in g.get("remove", []):
            if not (g["start"] < r[0] < r[1] < g["end"]):
                errs.append(f"{tag}: remove range {r} must lie inside the segment")
        pa = g.get("pause_after")
        if pa is not None and pa != "natural" and not (isinstance(pa, (int, float)) and 0 <= pa <= 5):
            errs.append(f"{tag}: pause_after must be seconds (0–5), \"natural\", or omitted")
        if not g.get("reason"):
            warns.append(f"{tag}: no reason given")
    order = [s.id for s in script.sentences]
    got = [g.get("sentence") for g in segs if g.get("sentence") in ids]
    if got != sorted(got, key=order.index):
        warns.append("segments are not in script order (allowed, but check that this is intended)")
    missing = [x for x in order if x not in seen]
    if missing:
        warns.append(f"{len(missing)} script sentence(s) not in the cut: {', '.join(missing[:8])}{'…' if len(missing) > 8 else ''}")
    return errs, warns

def build(cuts, transcript, script, work):
    """Apply cuts.json exactly (Claude decides, this only executes)."""
    x, sr = sf.read(work / "source.wav", dtype="float32")
    if x.ndim > 1:
        x = x.mean(axis=1)
    dur_src = len(x) / sr
    errs, warns = validate(cuts, script, dur_src)
    if errs:
        raise SystemExit("cuts.json has errors:\n  - " + "\n  - ".join(errs))
    for w in warns:
        log.warning(w)
    snap = Snapper(x, sr)
    words = transcript["words"]
    sents = {s.id: s for s in script.sentences}
    sec = lambda t: int(round(t * sr))
    segs = cuts["segments"]

    # Edges come from the AUDIO's speech blocks, not word timestamps: the ASR often misplaces the words next to a
    # pause by up to a second, which would clip first/last words. Guards stop an edge from running into a
    # neighbouring sentence that was spoken back-to-back (no pause between them).
    from plan_blocks import blocks as speech_blocks
    blocks = speech_blocks(snap, 0, dur_src, 0.25)
    def block_at(t):
        return next((b for b in blocks if b[0] <= t <= b[1]), None)
    def edge_start(t):
        b = block_at(t + 0.03)
        if b and b[0] >= t - 0.5:
            return max(0.0, b[0] - 0.03)
        if b is None:
            nb = next((b for b in blocks if t < b[0] <= t + 0.6), None)
            if nb:
                return nb[0] - 0.03
        return snap.start(t, max(0.0, t - 0.06))
    def edge_end(t):
        b = block_at(t - 0.03)
        if b and b[1] <= t + 0.7:
            return min(dur_src, b[1] + 0.05)
        if b is None:
            pb = next((b for b in reversed(blocks) if t - 0.6 <= b[1] < t), None)
            if pb:
                return pb[1] + 0.05
        return snap.end(t, min(dur_src, t + 0.08))
    def bounds(g):
        return edge_start(g["start"]), edge_end(g["end"])

    snapped = [bounds(g) for g in segs]
    pieces = [dict(kind="gap", audio=room_tone(x, sr, LEAD))]
    placement = []
    for i, (g, (a, b)) in enumerate(zip(segs, snapped)):
        # speech, minus removed ranges (snapped to quiet inside the segment)
        cuts_in = [(max(a, r[0] - 0.02), min(b, r[1] + 0.02)) for r in sorted(g.get("remove", []))]
        sub, s0 = [], a
        for cs, ce in cuts_in:
            if cs > s0:
                sub.append((s0, cs))
            s0 = max(s0, ce)
        if b > s0:
            sub.append((s0, b))
        first_piece = len(pieces)
        for (u, v) in sub:
            pieces.append(dict(kind="speech", audio=x[sec(u): sec(v)], src=(u, v), sent=g["sentence"]))
        placement.append(dict(sentence=g["sentence"], src=[round(a, 3), round(b, 3)], removed=[list(c) for c in cuts_in],
                              first_piece=first_piece, last_piece=len(pieces) - 1, reason=g.get("reason", "")))
        # pause after
        if i + 1 < len(segs):
            nxt, (na, nb) = segs[i + 1], snapped[i + 1]
            pa = g.get("pause_after")
            s, n = sents[g["sentence"]], sents[nxt["sentence"]]
            default = GAPS["section"] if s.section != n.section else GAPS["paragraph"] if s.para != n.para else GAPS["sentence"]
            if pa == "natural" and 0 <= na - b <= 1.5:
                pieces.append(dict(kind="gap", audio=x[sec(b): sec(na)]))
            else:
                pieces.append(dict(kind="gap", audio=room_tone(x, sr, default if pa in (None, "natural") else float(pa))))
    pieces.append(dict(kind="gap", audio=room_tone(x, sr, TAIL)))
    keep = [k for k, p in enumerate(pieces) if len(p["audio"]) > 0]
    remap = {k: n for n, k in enumerate(keep)}
    pieces = [pieces[k] for k in keep]
    audio, offsets = join(pieces, sr)

    # where every sentence and word landed in the clean audio
    sent_span, kept_words = {}, []
    for pl in placement:
        idx = [remap[k] for k in range(pl["first_piece"], pl["last_piece"] + 1) if k in remap]
        if not idx:
            continue
        o0 = offsets[idx[0]] / sr; o1 = offsets[idx[-1]] / sr + len(pieces[idx[-1]]["audio"]) / sr
        sent_span[pl["sentence"]] = [round(o0, 3), round(o1, 3)]
        pl["out"] = [round(o0, 3), round(o1, 3)]
        for k in idx:
            u, v = pieces[k]["src"]; off = offsets[k] / sr
            for w in words:
                if u - 0.01 <= w["start"] and w["end"] <= v + 0.05 and w.get("kind") != "event":   # no [laughter] in subtitles
                    kept_words.append(dict(w=w["w"], start=round(off + w["start"] - u, 3), end=round(off + w["end"] - u, 3), sent=pl["sentence"]))
    kept_words.sort(key=lambda w: w["start"])
    # No processing after the edit: no EQ, denoise or loudness change. Finishing is left to the editor.
    sf.write(work / "clean.wav", np.clip(audio, -1, 1), sr, subtype="PCM_24")
    dur = len(audio) / sr
    write_json(work / "clean_words.json", dict(duration=round(dur, 3), sentences=sent_span, words=kept_words, placement=placement,
                                               warnings=warns))
    log.info("clean voiceover: %.1f min (source %.1f min, %.1f min removed) → %s", dur / 60, dur_src / 60, (dur_src - dur) / 60, work / "clean.wav")
    return dict(duration=dur, source_duration=dur_src, warnings=warns, placement=placement)
