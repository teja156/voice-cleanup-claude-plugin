"""Stage 2 — align: transcript words → script token positions (work/alignment.json).

Speech is split into utterances at pauses. Each utterance is locally aligned (Smith-Waterman) against a window
of the script around the current reading position (a cursor), falling back to the whole script when the match is
weak. Every word ends up as: aligned (with a script token position), filler (um/uh…), or extra (asides, noise).
"""
from difflib import SequenceMatcher
from functools import lru_cache
import numpy as np
from common import log, tokens, FILLERS, write_json

MATCH, SIMILAR, MISMATCH, GAP = 3.0, 1.5, -1.5, -1.0
UTTER_GAP = 0.30          # seconds of silence that end an utterance
BACK, AHEAD = 350, 500    # search window around the cursor (script tokens)

# How the ASR hears names it doesn't know (e.g. "zentra" for "Zyntra"): heard token → script token. Parakeet takes no
# vocabulary prompt, so product names are often misheard. Set per project with `vo prepare --alias heard=script`.
SOUNDS_LIKE = {}

def set_aliases(aliases):
    SOUNDS_LIKE.clear(); SOUNDS_LIKE.update({k.lower(): v.lower() for k, v in (aliases or {}).items()})
    tok_score.cache_clear()

@lru_cache(maxsize=None)
def tok_score(a, b):
    a = SOUNDS_LIKE.get(a, a)
    if a == b:
        return MATCH
    if len(a) >= 3 and len(b) >= 3 and (a.startswith(b) or b.startswith(a)):
        return SIMILAR
    if len(a) >= 4 and len(b) >= 4 and SequenceMatcher(None, a, b).ratio() >= 0.75:
        return SIMILAR
    return MISMATCH

def smith_waterman(u, s, expect=None):
    """Local alignment of utterance tokens u against script tokens s. Returns (score, [(ui, si), …]).

    When several places score (almost) the same — repeated phrases like "every redirect" — the end cell nearest
    `expect` (where the reading should be, in s's coordinates) wins."""
    m, n = len(u), len(s)
    H = np.zeros((m + 1, n + 1), np.float32)
    T = np.zeros((m + 1, n + 1), np.int8)    # 0 stop, 1 diag, 2 up (skip u), 3 left (skip s)
    sub = np.array([[tok_score(a, b) for b in s] for a in u], np.float32) if m and n else np.zeros((m, n), np.float32)
    for i in range(1, m + 1):
        diag = H[i - 1, :-1] + sub[i - 1]
        up = H[i - 1, 1:] + GAP
        row = np.maximum(0, np.maximum(diag, up))
        t = np.where(row == 0, 0, np.where(row == diag, 1, 2)).astype(np.int8)
        # left moves need a sequential pass
        for j in range(1, n + 1):
            v = row[j - 1]; tt = t[j - 1]
            left = H[i, j - 1] + GAP
            if left > v:
                v, tt = left, 3
            H[i, j] = v; T[i, j] = tt
    best = float(H.max())
    if expect is not None and best > 0:
        cand = np.argwhere(H >= best - 0.5)
        i, j = min(cand, key=lambda c: (abs(int(c[1]) - expect), -H[c[0], c[1]]))
        i, j = int(i), int(j); best = float(H[i, j])
    else:
        i, j = np.unravel_index(int(np.argmax(H)), H.shape)
    pairs = []
    while i > 0 and j > 0 and T[i, j] != 0:
        d = T[i, j]
        if d == 1:
            if sub[i - 1, j - 1] > 0:
                pairs.append((i - 1, j - 1))
            i, j = i - 1, j - 1
        elif d == 2:
            i -= 1
        else:
            j -= 1
    return best, pairs[::-1]

def align(transcript, script):
    words = transcript["words"]
    # transcript tokens with a back-reference to their word
    wtoks = [[] if w.get("kind") == "event" else tokens(w["w"]) for w in words]   # events ([laughter]) never match the script
    # utterances
    utts, cur = [], []
    for i, w in enumerate(words):
        if cur and w["start"] - words[cur[-1]]["end"] > UTTER_GAP:
            utts.append(cur); cur = []
        cur.append(i)
    if cur:
        utts.append(cur)
    S = script.tokens
    pos = [None] * len(words); kind = ["extra"] * len(words)
    cursor, out_utts = 0, []
    for ui, idxs in enumerate(utts):
        u, u_word = [], []
        for wi in idxs:
            for t in wtoks[wi]:
                u.append(t); u_word.append(wi)
        content = [t for t in u if t not in FILLERS]
        rec = dict(id=ui, start=words[idxs[0]]["start"], end=words[idxs[-1]]["end"],
                   text=" ".join(words[i]["w"] for i in idxs), score=0.0, script_range=None)
        if not content:
            for wi in idxs:
                kind[wi] = "filler" if wtoks[wi] and all(t in FILLERS for t in wtoks[wi]) else "extra"
            out_utts.append(rec); continue
        lo, hi = max(0, cursor - BACK), min(len(S), cursor + AHEAD)
        best, pairs = smith_waterman(u, S[lo:hi], expect=cursor - lo + len(content)); off = lo
        if best < 0.6 * MATCH * len(content):           # weak: try the whole script
            b2, p2 = smith_waterman(u, S, expect=cursor + len(content))
            if b2 > best * 1.15:
                best, pairs, off = b2, p2, 0
        matched = len(pairs)
        confident = matched >= 2 or (matched == 1 and len(content) == 1 and best >= MATCH)
        if confident:
            for (ti, si) in pairs:
                wi = u_word[ti]; sp = si + off
                if pos[wi] is None or sp < pos[wi]:
                    pos[wi] = sp
                kind[wi] = "aligned"
            rec.update(score=round(best, 1), script_range=[pairs[0][1] + off, pairs[-1][1] + off + 1])
            if best >= 0.5 * MATCH * len(content):     # only a solid match moves the reading position
                cursor = pairs[-1][1] + off + 1
        for wi in idxs:
            if kind[wi] != "aligned":
                kind[wi] = "filler" if wtoks[wi] and all(t in FILLERS for t in wtoks[wi]) else "extra"
        out_utts.append(rec)
    aw = [dict(i=i, w=w["w"], start=w["start"], end=w["end"], p=w.get("p"),
               pos=pos[i], sent=(script.tok_sent[pos[i]] if pos[i] is not None else None),
               kind="event" if w.get("kind") == "event" else kind[i])
          for i, w in enumerate(words)]
    n_al = sum(k == "aligned" for k in kind)
    log.info("aligned %d/%d words (%d fillers, %d extra) across %d utterances",
             n_al, len(words), kind.count("filler"), kind.count("extra"), len(utts))
    return dict(words=aw, utterances=out_utts)

def run_align(transcript, script, work, aliases=None):
    set_aliases(aliases)
    res = align(transcript, script)
    write_json(work / "alignment.json", res)
    return res
