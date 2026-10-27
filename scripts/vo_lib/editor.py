"""Voiceover editor: a local web page with two timelines, the original recording (work/source.wav) and the clean
voiceover (clean.wav). Select a stretch of the original (for example words the speech model dropped, so the cut left
them out) and drag it onto the clean timeline to insert it there. Edits are a list of clips, each a range of either
file, saved to work/edits.json. Rendering writes clean_edited.wav (and work/clean_edited_words.json); clean.wav is
never changed.

Routes, on top of serving the project folder with Range support (serve.RangeHandler):
  GET  /                    the editor page
  GET  /api/project         durations, waveform peaks, quiet frames, words, placement, saved edits
  GET  /api/slice?src=clean|source&a=&b=   a range of either file as 16-bit WAV (for gapless preview)
  POST /api/save            {clips} → work/edits.json
  POST /api/render          {clips} → clean_edited.wav + work/clean_edited_words.json
"""
import base64, functools, io, json, os, threading, time
from pathlib import Path
from urllib.parse import urlparse, parse_qs
import numpy as np
import soundfile as sf
from common import log, read_json, write_json
from serve import RangeHandler, Server
from audio import Snapper, FRAME, join

PAGE = Path(__file__).resolve().parent / "editor.html"

class Stale(Exception):
    """The page's edits are older than work/edits.json (another tab saved since): it must reload, not overwrite."""

class Project:
    """The two audio files, loaded once, plus everything the page needs to draw them."""
    def __init__(self, P):
        self.P, self.W = P, P["work"]
        self.files = dict(clean=P["clean"], source=self.W / "source.wav")
        self.audio, self._info = {}, None
        for k, path in self.files.items():
            if not path.exists():
                raise SystemExit(f"missing {path}: run `vo apply` first")

    def load(self, key):
        if key not in self.audio:
            x, sr = sf.read(self.files[key], dtype="float32")
            self.audio[key] = (x if x.ndim == 1 else x.mean(axis=1), sr)
        return self.audio[key]

    def stamp(self):
        st = self.files["clean"].stat()
        return dict(clean_size=st.st_size, clean_mtime=round(st.st_mtime, 3))

    def info(self):
        if self._info is None:
            t0 = time.time()
            tracks = {}
            for key in ("source", "clean"):
                x, sr = self.load(key)
                n = int(FRAME * sr); frames = len(x) // n
                peak = np.abs(x[: frames * n]).reshape(frames, n).max(axis=1)
                peak = np.sqrt(peak / max(float(peak.max()), 1e-6))        # sqrt: quiet speech stays visible
                quiet = Snapper(x, sr).quiet[:frames]
                tracks[key] = dict(duration=round(len(x) / sr, 3), sr=sr,
                                   peaks=base64.b64encode((peak * 255).astype(np.uint8).tobytes()).decode(),
                                   quiet=base64.b64encode(quiet.astype(np.uint8).tobytes()).decode())
            clean = read_json(self.W / "clean_words.json")
            al = read_json(self.W / "alignment.json")["words"]
            tracks["source"]["words"] = [dict(w=w["w"], start=w["start"], end=w["end"], kind=w.get("kind", "")) for w in al
                                         if w.get("kind") != "event"]
            tracks["clean"]["words"] = [dict(w=w["w"], start=w["start"], end=w["end"], sent=w.get("sent")) for w in clean["words"]]
            tracks["clean"]["sentences"] = clean.get("sentences", {})
            self._info = dict(name=self.P["root"].name, frame=FRAME, tracks=tracks,
                              placement=[dict(sentence=p["sentence"], src=p["src"], removed=p["removed"], out=p.get("out"))
                                         for p in clean.get("placement", [])])
            log.info("editor data ready in %.1fs", time.time() - t0)
        out = dict(self._info)
        ed = self.W / "edits.json"
        out["edits"], out["edits_note"], out["rev"] = None, "", self.rev()
        if ed.exists():
            saved = read_json(ed)
            if {k: saved.get(k) for k in ("clean_size", "clean_mtime")} == self.stamp():
                out["edits"] = saved["clips"]
            else:
                out["edits_note"] = "clean.wav changed since work/edits.json was saved: started from the new clean.wav"
        out["rendered"] = (self.P["root"] / "clean_edited.wav").exists()
        return out

    def check(self, clips):
        if not isinstance(clips, list) or not clips:
            raise ValueError("no clips")
        out = []
        for i, c in enumerate(clips):
            src, a, b = c.get("src"), float(c.get("a", -1)), float(c.get("b", -1))
            if src not in self.files:
                raise ValueError(f"clip {i + 1}: src must be clean or source")
            x, sr = self.load(src)
            if not (0 <= a < b <= len(x) / sr + 0.01):
                raise ValueError(f"clip {i + 1}: bad range {a}–{b}")
            out.append(dict(src=src, a=round(a, 4), b=round(b, 4)))
        return out

    def slice_wav(self, src, a, b):
        x, sr = self.load(src)
        y = x[int(max(0, a) * sr): int(b * sr)]
        buf = io.BytesIO(); sf.write(buf, y, sr, format="WAV", subtype="PCM_16")
        return buf.getvalue()

    def rev(self):
        ed = self.W / "edits.json"
        return read_json(ed).get("rev", 0) if ed.exists() else 0

    _lock = threading.Lock()

    def save(self, clips, rev):
        """Write the edits if the page saw the latest revision; returns the new revision."""
        clips = self.check(clips)
        with self._lock:
            cur = self.rev()
            if rev != cur:
                raise Stale(f"work/edits.json changed since this page loaded it (revision {cur}, page has {rev})")
            write_json(self.W / "edits.json", dict(self.stamp(), rev=cur + 1, clips=clips))
            return cur + 1

    def render(self, clips, rev):
        clips = self.check(clips)
        rev = self.save(clips, rev)
        sr = self.load("clean")[1]
        if self.load("source")[1] != sr:
            raise ValueError("clean.wav and source.wav have different sample rates")
        pieces = [dict(audio=self.load(c["src"])[0][int(c["a"] * sr): int(c["b"] * sr)]) for c in clips]
        audio, offsets = join(pieces, sr)
        words_of = dict(clean=self.info()["tracks"]["clean"]["words"], source=self.info()["tracks"]["source"]["words"])
        words = []
        for c, off in zip(clips, offsets):
            for w in words_of[c["src"]]:
                if c["a"] - 0.01 <= w["start"] and w["end"] <= c["b"] + 0.05:
                    words.append(dict(w=w["w"], start=round(off / sr + w["start"] - c["a"], 3), end=round(off / sr + w["end"] - c["a"], 3),
                                      inserted=c["src"] == "source"))
        out = self.P["root"] / "clean_edited.wav"
        tmp = out.with_suffix(".tmp.wav")
        sf.write(tmp, np.clip(audio, -1, 1), sr, subtype="PCM_24")
        os.replace(tmp, out)
        write_json(self.W / "clean_edited_words.json", dict(duration=round(len(audio) / sr, 3), clips=clips, words=words))
        n_ins = sum(c["src"] == "source" for c in clips)
        log.info("rendered %s (%.1f s, %d clip(s) from the original)", out, len(audio) / sr, n_ins)
        return dict(file=out.name, path=str(out), duration=round(len(audio) / sr, 3), inserted=n_ins, rev=rev)

class EditorHandler(RangeHandler):
    project = None

    def _send(self, code, body, ctype):
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _json(self, obj, code=200):
        self._send(code, json.dumps(obj).encode(), "application/json")

    def do_GET(self):
        u = urlparse(self.path)
        try:
            if u.path in ("/", "/editor", "/editor.html"):
                return self._send(200, PAGE.read_bytes(), "text/html; charset=utf-8")
            if u.path == "/api/project":
                return self._json(self.project.info())
            if u.path == "/api/slice":
                q = {k: v[0] for k, v in parse_qs(u.query).items()}
                if q.get("src") not in self.project.files:
                    return self._json(dict(error="bad src"), 400)
                return self._send(200, self.project.slice_wav(q["src"], float(q["a"]), float(q["b"])), "audio/wav")
        except (BrokenPipeError, ConnectionResetError):
            return
        except (KeyError, ValueError) as e:
            return self._json(dict(error=str(e)), 400)
        return super().do_GET()

    def do_POST(self):
        u = urlparse(self.path)
        try:
            body = json.loads(self.rfile.read(int(self.headers.get("Content-Length", 0))) or b"{}")
            if u.path == "/api/save":
                return self._json(dict(ok=True, rev=self.project.save(body.get("clips"), body.get("rev"))))
            if u.path == "/api/render":
                return self._json(dict(ok=True, **self.project.render(body.get("clips"), body.get("rev"))))
            return self._json(dict(error="not found"), 404)
        except Stale as e:
            return self._json(dict(error=str(e), stale=True), 409)
        except ValueError as e:
            return self._json(dict(error=str(e)), 400)

def edit(P, port=8802):
    EditorHandler.project = Project(P)
    handler = functools.partial(EditorHandler, directory=str(P["root"]))
    with Server(("127.0.0.1", port), handler) as httpd:
        log.info("voiceover editor for %s at http://127.0.0.1:%d/ (Ctrl+C to stop)", P["root"], port)
        try:
            httpd.serve_forever()
        except KeyboardInterrupt:
            log.info("stopped")
