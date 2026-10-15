"""Voiceover review page: <project>/review.html. Shows the clean voiceover, every kept sentence with Claude's reason
(play it in the clean cut or in the original), and every dropped piece with its reason. Open it with `vo serve`
(range requests make seeking in long WAVs work) or directly from disk.
"""
import datetime, html
from common import fmt_time, Script

def esc(x):
    return html.escape(str(x))

def write(name, P, cuts, clean, status="awaiting review"):
    texts = {s.id: s.text for s in Script(P["script"]).sentences}
    rows = []
    for i, pl in enumerate(clean.get("placement", []), 1):
        o = pl.get("out", [0, 0]); a, b = pl["src"]
        rem = f'<div class="rm">removed inside: {", ".join(f"{fmt_time(r[0])}–{fmt_time(r[1])}" for r in pl["removed"])}</div>' if pl["removed"] else ""
        rows.append(f'''<tr><td class="n">{i}</td><td class="id">{esc(pl["sentence"])}<br><span class="t">{fmt_time(o[0])}</span></td>
<td><div class="st">{esc(texts.get(pl["sentence"], ""))}</div><div class="why">{esc(pl.get("reason", ""))}</div>{rem}</td>
<td class="pl"><button data-src="clean.wav" data-a="{o[0]:.2f}" data-b="{o[1]:.2f}">▶ clean</button>
<button class="ghost" data-src="work/source.wav" data-a="{a:.2f}" data-b="{b:.2f}">▶ original</button></td></tr>''')
    dropped = []
    for d in cuts.get("dropped", []):
        dropped.append(f'''<tr><td class="id">{fmt_time(d["start"])}</td><td><b>{esc(d.get("what", ""))}</b> — {esc(d.get("reason", ""))}</td>
<td class="pl"><button class="ghost" data-src="work/source.wav" data-a="{d["start"]:.2f}" data-b="{d["end"]:.2f}">▶ original</button></td></tr>''')
    warns = "".join(f"<li>{esc(w)}</li>" for w in clean.get("warnings", []))
    src_d, cl_d = clean.get("source_duration"), clean.get("duration")
    stats = f"{len(rows)} sentences kept · {len(dropped)} pieces dropped" + (f" · {cl_d / 60:.1f} min clean (from {src_d / 60:.1f} min raw)" if src_d else f" · {cl_d / 60:.1f} min")
    P["review"].write_text(f'''<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Voiceover review — {esc(name)}</title>
<style>
body {{ margin: 0; background: #E8F4FB; color: #2A2D31; font: 15px/1.5 system-ui, sans-serif; }}
header {{ background: linear-gradient(120deg,#185F85,#0C2D40); color: #fff; padding: 22px 40px; }}
header h1 {{ font: 700 24px system-ui, sans-serif; margin: 0; }} header a {{ color: #7DD3FC; }}
main {{ max-width: 1300px; margin: 0 auto; padding: 20px 40px 60px; }}
h2 {{ font: 700 20px system-ui, sans-serif; margin: 28px 0 10px; }}
.status {{ display: inline-block; background: #E17100; color: #fff; font-weight: 800; font-size: 12px; letter-spacing: .12em; text-transform: uppercase; padding: 3px 10px; border-radius: 999px; margin-left: 10px; vertical-align: middle; }}
.status.ok {{ background: #2FAA68; }}
.ask {{ background: #fff; border-left: 6px solid #1A9BD7; border-radius: 10px; padding: 14px 18px; margin: 16px 0; }}
table {{ width: 100%; border-collapse: collapse; background: #fff; border-radius: 10px; overflow: hidden; }}
td {{ border-top: 1px solid #DDE2ED; padding: 8px 10px; vertical-align: top; }}
td.n {{ color: #8A95A3; width: 32px; }} td.id {{ font: 12px monospace; color: #556070; white-space: nowrap; width: 90px; }} .t {{ color: #0D84BE; }}
.st {{ font-weight: 600; }} .why {{ color: #556070; font-size: 13px; }} .rm {{ color: #E17100; font-size: 13px; }}
td.pl {{ white-space: nowrap; width: 190px; }}
button {{ font: 700 13px system-ui, sans-serif; border: 1px solid #1A9BD7; background: #1A9BD7; color: #fff; border-radius: 999px; padding: 5px 12px; cursor: pointer; }}
button.ghost {{ background: #fff; color: #185F85; border-color: #BFD3E0; }} button.playing {{ background: #E17100; border-color: #E17100; color: #fff; }}
.warn {{ background: #FFF4E5; border-left: 6px solid #E17100; border-radius: 10px; padding: 10px 18px; }}
.small {{ font-size: 13px; color: #556070; }}
audio.main {{ width: 100%; }}
</style></head><body>
<header><h1>Voiceover review — {esc(name)} <span class="status{" ok" if status == "approved" else ""}">{esc(status)}</span></h1>
<p>{esc(stats)} · updated {datetime.datetime.now():%Y-%m-%d %H:%M}</p></header>
<main>
<div class="ask"><b>Next step:</b> listen, then tell Claude <b>"approved"</b>, or describe changes — for example
<i>"S4.P2.1: use the earlier take"</i>, <i>"the pause after S6.P1.3 is too long"</i>, <i>"keep the aside at 03:12"</i>.</div>
<h2>Clean voiceover</h2><audio class="main" controls preload="metadata" src="clean.wav"></audio>
{f'<h2>Warnings</h2><ul class="warn">{warns}</ul>' if warns else ""}
<h2>Kept, in order</h2><table>{"".join(rows)}</table>
<h2>Dropped</h2><table>{"".join(dropped) or "<tr><td>none</td></tr>"}</table>
</main>
<script>
// play a range of a file; one shared player so only one clip plays at a time
const p = new Audio(); let btn = null, stopAt = 0;
p.addEventListener("timeupdate", () => {{ if (p.currentTime >= stopAt) {{ p.pause(); btn && btn.classList.remove("playing"); }} }});
document.addEventListener("click", e => {{
  const b = e.target.closest("button[data-src]"); if (!b) return;
  if (btn === b && !p.paused) {{ p.pause(); b.classList.remove("playing"); return; }}
  btn && btn.classList.remove("playing"); btn = b; b.classList.add("playing");
  const go = () => {{ p.currentTime = +b.dataset.a; stopAt = +b.dataset.b; p.play(); }};
  if (!p.src.endsWith(b.dataset.src)) {{ p.src = b.dataset.src; p.addEventListener("loadedmetadata", go, {{ once: true }}); }} else go();
}});
</script></body></html>''')
    return P["review"]
