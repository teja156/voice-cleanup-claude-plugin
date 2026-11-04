"""Breaths inside kept speech: find them from the audio alone, and (only when cuts.json asks for it) cut them.

The ASR's word gaps can't be used: Parakeet times words on an 80 ms grid, so many "gaps" are really speech. A breath
is a sustained (≥ 0.18 s) stretch that is clearly above the room noise but well below speech, with a mid-band
spectral centroid (sibilants sit at 4–8 kHz) and no pitch. Thresholds were measured on a real recording; see
FEATURE-REPORT-breath-removal.md in the prototype.

cuts.json: "breaths": "cut" | "keep" (default "cut"; "keep" only detects and reports them). A segment with
"breaths": "keep" is never touched (e.g. playback of a raw recording kept on purpose)."""
import numpy as np

HOP, WIN = 0.010, 0.030       # frame hop and analysis window (s)
MIN_LEN = 0.18                # shorter runs are left alone
HI = -46.0                    # louder than this is speech or a sibilant
ABOVE_FLOOR = 9.0             # a breath is at least this many dB above the piece's own noise floor
CEN = (500.0, 4200.0)         # breath spectral centroid band (Hz)
MAX_VOICED = 0.25             # a run with more voiced hops than this is quiet speech, not a breath
QUIET_SEG = 15.0              # a segment this many dB below the project's speech level is skipped (playback, aside)
KEEP = 0.04                   # breath left on each side of a cut (s); the 12 ms crossfade closes the rest
EDGE = 0.08                   # a breath this close to a piece edge is trimmed off the edge instead of cut out
PAD = 0.02                    # build() widens every remove range by this much on each side
def features(x, sr, t0, t1):
    """dB and spectral centroid per 10 ms hop for [t0, t1], on the file's global frame grid. Chunked: memory stays
    small however long the range is."""
    h, w = int(HOP * sr), int(WIN * sr)
    f0 = int(t0 / HOP); s0 = f0 * h
    n = max(0, (min(len(x), int(t1 * sr)) - s0 - w) // h + 1)
    db, cen = np.full(n, -120.0), np.zeros(n)
    win = np.hanning(w).astype(np.float32)
    freqs = np.fft.rfftfreq(2048, 1 / sr)
    for c in range(0, n, 2000):
        k = min(n, c + 2000)
        idx = s0 + np.arange(w)[None, :] + h * np.arange(c, k)[:, None]
        fr = x[idx] * win
        db[c:k] = 20 * np.log10(np.sqrt(np.mean(fr ** 2, axis=1) + 1e-12))
        spec = np.abs(np.fft.rfft(fr, n=2048, axis=1)) ** 2
        cen[c:k] = (spec * freqs).sum(1) / (spec.sum(1) + 1e-12)
    return f0, db, cen

def _median5(v):
    if len(v) < 5:
        return v
    p = np.pad(v, 2, mode="edge")
    return np.median(np.lib.stride_tricks.sliding_window_view(p, 5), axis=1)

def voiced_frac(x, sr, t0, t1, thr=0.45):
    """Share of 10 ms hops (40 ms windows) in [t0, t1] with a clear pitch (80–400 Hz, unbiased autocorrelation).
    The 80 Hz floor keeps 60 Hz mains hum from counting as voiced."""
    w, h = int(0.04 * sr), int(0.01 * sr)
    lo, hi = int(sr / 400), int(sr / 80)
    n = v = 0
    for s in range(int(t0 * sr), int(t1 * sr) - w, h):
        f = x[s:s + w] - x[s:s + w].mean()
        e = float(np.dot(f, f)); n += 1
        if e < 1e-9:
            continue
        ac = np.correlate(f, f, "full")[w - 1:w + hi]
        ac = ac / (e * (w - np.arange(len(ac))) / w)
        v += ac[lo:hi].max() > thr
    return v / max(n, 1)

def runs(x, sr, t0, t1):
    """Breath ranges in [t0, t1]: [(start, end, peak_db)]. The noise floor is the piece's own (its 10th percentile):
    measuring it with context around the piece lowers it and flags far more stretches than are breaths."""
    f0, db, cen = features(x, sr, t0, t1)
    if not len(db):
        return []
    d, c = _median5(db), _median5(cen)
    live = d[d > -100]
    lo = max((np.percentile(live, 10) if len(live) else -90.0) + ABOVE_FLOOR, -74.0)
    m = (d > lo) & (d < HI) & (c > CEN[0]) & (c < CEN[1])
    out, i = [], 0
    while i < len(m):
        if not m[i]:
            i += 1; continue
        j = i
        while j < len(m) and m[j]:
            j += 1
        s, e = round((f0 + i) * HOP, 3), round((f0 + j) * HOP, 3)
        if (j - i) * HOP >= MIN_LEN and voiced_frac(x, sr, s, e) <= MAX_VOICED:
            out.append((s, e, round(float(d[i:j].max()), 1)))
        i = j
    return out

def level(x, sr, t0, t1):
    """A segment's speech level: the 90th percentile of its frame dB."""
    _, db, _ = features(x, sr, t0, t1)
    return float(np.percentile(db, 90)) if len(db) else -120.0

def apply(cuts, segs, snapped, x, sr):
    """Find the breaths in every kept segment; when cuts.json says "breaths": "cut", cut them. Returns the snapped
    edges (tail and head breaths move an edge), the segments (inside breaths become extra remove ranges; the input
    dicts are not changed), and a record of every breath found and what happened to it."""
    cut = cuts.get("breaths", "cut") == "cut"
    out, new, found = [list(s) for s in snapped], [], []
    levels = [level(x, sr, a, b) for a, b in snapped]
    project = float(np.median(levels)) if levels else -120.0
    for k, (g, lv) in enumerate(zip(segs, levels)):
        g = dict(g, remove=[list(r) for r in g.get("remove", [])])
        a, b = out[k]
        parts, s0 = [], a                          # the kept parts: the segment minus its remove ranges
        for r0, r1 in sorted(g["remove"]):
            if r0 - PAD > s0:
                parts.append((s0, r0 - PAD))
            s0 = max(s0, r1 + PAD)
        if b > s0:
            parts.append((s0, b))
        why = ("segment marked \"breaths\": \"keep\"" if g.get("breaths") == "keep"
               else f"quiet segment ({lv:.0f} dB, {project - lv:.0f} dB below the voiceover)" if lv < project - QUIET_SEG
               else None if cut else "breath removal is off")
        for u, v in parts:
            for s, e, pk in runs(x, sr, u, v):
                rec = dict(sentence=g["sentence"], start=s, end=e, peak_db=pk, action="kept", note=why or "")
                if why is None:
                    if b - e < EDGE:               # at the tail: end the piece before the breath
                        out[k][1] = b = max(a + 0.1, s + KEEP); rec["action"] = "cut (tail)"
                    elif s - a < EDGE:             # at the head: start the piece after it
                        out[k][0] = a = min(b - 0.1, e - KEEP); rec["action"] = "cut (head)"
                    elif e - s > 2 * (KEEP + PAD):  # inside: remove it, leaving KEEP of breath on each side
                        g["remove"].append([round(s + KEEP + PAD, 3), round(e - KEEP - PAD, 3)]); rec["action"] = "cut (inside)"
                found.append(rec)
        g["remove"].sort()
        new.append(g)
    return [tuple(s) for s in out], new, found
