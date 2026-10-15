"""Speech blocks from the audio itself (used by vo_tools probe): [onset, offset] split at quiet runs ≥ pause seconds."""
def blocks(snap, t0, t1, pause=0.30):
    i0, i1 = snap._f(t0), snap._f(t1)
    out, cur, quiet_run = [], None, 0
    for i in range(i0, i1 + 1):
        if not snap.quiet[i]:
            if cur is None:
                cur = [i, i]
            elif quiet_run * 0.01 >= pause:
                out.append(cur); cur = [i, i]
            cur[1] = i; quiet_run = 0
        else:
            quiet_run += 1
    if cur:
        out.append(cur)
    return [(round(b[0] * 0.01, 2), round((b[1] + 1) * 0.01, 2)) for b in out]
