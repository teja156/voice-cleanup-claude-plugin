"""Tests for breath detection and removal (scripts/vo_lib/breaths.py, via audio.build). Run: python -m unittest discover -s tests"""
import sys, tempfile, unittest
from pathlib import Path
import numpy as np
import soundfile as sf

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts" / "vo_lib"))
from audio import build, validate                   # noqa: E402
from common import Script                           # noqa: E402

SR = 48000
RNG = np.random.default_rng(1)

def db(v):
    return 10 ** (v / 20)

def voiced(secs, level, f0=150, top=2100):
    """A 'word': harmonics of f0 up to `top` Hz (pitched, mid-band centroid), at `level` dB RMS."""
    t = np.arange(int(secs * SR)) / SR
    y = sum(np.sin(2 * np.pi * f * t) for f in range(f0, top + 1, f0))
    return (y / np.sqrt(np.mean(y ** 2)) * db(level)).astype(np.float32)

def band_noise(secs, level, lo, hi):
    """Unpitched noise limited to lo–hi Hz (a breath at 500–3000 Hz, a sibilant at 4–8 kHz), at `level` dB RMS."""
    n = int(secs * SR)
    spec = np.fft.rfft(RNG.standard_normal(n)); f = np.fft.rfftfreq(n, 1 / SR)
    spec[(f < lo) | (f > hi)] = 0
    y = np.fft.irfft(spec, n)
    return (y / np.sqrt(np.mean(y ** 2)) * db(level)).astype(np.float32)

def room(secs):
    return (db(-75) * RNG.standard_normal(int(secs * SR))).astype(np.float32)

class Breaths(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(); self.work = Path(self.tmp.name)
        (self.work / "script.md").write_text("This is a sentence with a breath in the middle.\n", encoding="utf-8")
        self.script = Script(self.work / "script.md")

    def tearDown(self):
        self.tmp.cleanup()

    def run_build(self, parts, mode="cut", seg_extra=None):
        """parts: [(name, audio)]. One segment covers all but the lead/tail room noise. Returns (result, spans)."""
        spans, pos, chunks = {}, 0.0, [room(0.5)]
        pos = 0.5
        for name, y in parts:
            spans[name] = (pos, pos + len(y) / SR); pos += len(y) / SR; chunks.append(y)
        chunks.append(room(1.0))
        sf.write(self.work / "source.wav", np.concatenate(chunks), SR, subtype="PCM_24")
        seg = dict(sentence=self.script.sentences[0].id, start=0.5, end=pos, reason="t", **(seg_extra or {}))
        cuts = dict(segments=[seg], dropped=[], **({} if mode is None else dict(breaths=mode)))
        return build(cuts, dict(words=[]), self.script, self.work), spans

    def test_breath_between_words_is_cut_by_default(self):
        res, sp = self.run_build([("w1", voiced(0.6, -22)), ("gap1", room(0.2)), ("breath", band_noise(0.3, -50, 500, 3000)),
                                  ("gap2", room(0.2)), ("w2", voiced(0.6, -22))], mode=None)
        cut = [b for b in res["breaths"] if b["action"] != "kept"]
        self.assertEqual(len(cut), 1, res["breaths"])
        b0, b1 = sp["breath"]
        self.assertTrue(abs(cut[0]["start"] - b0) < 0.05 and abs(cut[0]["end"] - b1) < 0.05, (cut[0], sp["breath"]))
        (r0, r1), = res["placement"][0]["removed"]
        self.assertGreater(r0, sp["w1"][1] + 0.01, "the cut reaches into the first word")
        self.assertLess(r1, sp["w2"][0] - 0.01, "the cut reaches into the second word")
        self.assertGreater(r1 - r0, 0.15, "most of the breath should be gone")

    def test_sibilant_is_kept(self):
        res, _ = self.run_build([("w1", voiced(0.6, -22)), ("s", band_noise(0.25, -35, 4000, 8000)), ("gap", room(0.3)),
                                 ("w2", voiced(0.6, -22))])
        self.assertEqual(res["breaths"], [])
        self.assertEqual(res["placement"][0]["removed"], [])

    def test_quiet_voiced_sound_is_kept(self):
        res, _ = self.run_build([("w1", voiced(0.6, -22)), ("gap1", room(0.2)), ("hum", voiced(0.3, -55)), ("gap2", room(0.2)),
                                 ("w2", voiced(0.6, -22))])
        self.assertEqual(res["breaths"], [], "a pitched sound is not a breath, however quiet")

    def test_segment_marked_keep_is_untouched(self):
        res, _ = self.run_build([("w1", voiced(0.6, -22)), ("gap1", room(0.2)), ("breath", band_noise(0.3, -50, 500, 3000)),
                                 ("gap2", room(0.2)), ("w2", voiced(0.6, -22))], seg_extra=dict(breaths="keep"))
        self.assertEqual([b["action"] for b in res["breaths"]], ["kept"])
        self.assertEqual(res["placement"][0]["removed"], [])

    def test_keep_reports_but_does_not_cut(self):
        res, _ = self.run_build([("w1", voiced(0.6, -22)), ("gap1", room(0.2)), ("breath", band_noise(0.3, -50, 500, 3000)),
                                 ("gap2", room(0.2)), ("w2", voiced(0.6, -22))], mode="keep")
        self.assertEqual([b["action"] for b in res["breaths"]], ["kept"])
        self.assertEqual(res["placement"][0]["removed"], [])

    def test_validate_breaths_values(self):
        seg = dict(sentence=self.script.sentences[0].id, start=0.5, end=1.0, reason="t")
        self.assertTrue(validate(dict(segments=[seg], breaths="maybe"), self.script, 3)[0])
        self.assertTrue(validate(dict(segments=[dict(seg, breaths="cut")]), self.script, 3)[0])
        self.assertFalse(validate(dict(segments=[dict(seg, breaths="keep")], breaths="cut"), self.script, 3)[0])

if __name__ == "__main__":
    unittest.main()
