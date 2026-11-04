"""Tests for the apply stage (scripts/vo_lib/audio.py). Run: python -m unittest discover -s tests"""
import sys, tempfile, unittest
from pathlib import Path
import numpy as np
import soundfile as sf

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts" / "vo_lib"))
from audio import build, validate, overlaps          # noqa: E402
from common import Script                           # noqa: E402

SR = 48000

def tone(secs, hz):
    t = np.arange(int(secs * SR)) / SR
    return 0.3 * np.sin(2 * np.pi * hz * t).astype(np.float32)

def quiet(secs, rng):
    return (1e-4 * rng.standard_normal(int(secs * SR))).astype(np.float32)

class BackToBack(unittest.TestCase):
    """Two sentences spoken with less than 0.25 s between them form one speech block. Cut as separate segments,
    the second one must be in clean.wav exactly once (it used to play twice: once as the first one's tail)."""

    def setUp(self):
        rng = np.random.default_rng(0)
        self.tmp = tempfile.TemporaryDirectory(); self.work = Path(self.tmp.name)
        # 0.5 s quiet · "sentence one" 0.5–1.5 · 0.1 s quiet · "Cheers!" 1.6–2.1 · 1 s quiet
        x = np.concatenate([quiet(0.5, rng), tone(1.0, 220), quiet(0.1, rng), tone(0.5, 660), quiet(1.0, rng)])
        sf.write(self.work / "source.wav", x, SR, subtype="PCM_16")
        (self.work / "script.md").write_text("I will see you in the next one. Cheers!\n", encoding="utf-8")
        self.script = Script(self.work / "script.md")
        self.ids = [s.id for s in self.script.sentences]
        self.words = [dict(w="one.", start=0.5, end=1.5), dict(w="Cheers!", start=1.6, end=2.1)]

    def tearDown(self):
        self.tmp.cleanup()

    def cuts(self, b_start=1.6):
        return dict(segments=[dict(sentence=self.ids[0], start=0.5, end=1.5, reason="t"),
                              dict(sentence=self.ids[1], start=b_start, end=2.1, reason="t")], dropped=[])

    def test_second_sentence_once(self):
        res = build(self.cuts(), dict(words=self.words), self.script, self.work)
        (a0, a1), (b0, b1) = (p["src"] for p in res["placement"])
        self.assertLessEqual(a1, b0 + 0.001, "the first sentence's end runs into the second")
        self.assertGreaterEqual(a1, 1.5, "the first sentence lost its last word")
        self.assertLessEqual(b0, 1.6, "the second sentence lost its first word")
        self.assertEqual(overlaps(res["placement"]), [])
        # the 660 Hz "Cheers!" lasts 0.5 s in the source; in clean.wav it must last about as long, not twice that
        y, _ = sf.read(self.work / "clean.wav", dtype="float32")
        n = int(0.01 * SR); frames = len(y) // n
        f = np.fft.rfftfreq(n, 1 / SR)
        hi = sum(1 for i in range(frames) if f[np.argmax(np.abs(np.fft.rfft(y[i * n:(i + 1) * n])))] > 440
                 and np.abs(y[i * n:(i + 1) * n]).max() > 0.05) * 0.01
        self.assertLess(hi, 0.65, f"'Cheers!' is {hi:.2f}s long in clean.wav (0.5s in the source): it plays twice")

    def test_validate_rejects_overlapping_cuts(self):
        errs, _ = validate(self.cuts(b_start=1.4), self.script, 3.1)
        self.assertTrue(any("inside" in e for e in errs), errs)

if __name__ == "__main__":
    unittest.main()
