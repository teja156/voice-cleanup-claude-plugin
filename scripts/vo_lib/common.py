"""Shared helpers for the voiceover-cleanup pipeline.

Script model: the narration script (Markdown or plain text) is parsed into sections → paragraphs → sentences. Every
sentence gets a stable id "S<section>.P<para>.<n>" and a range in one flat token list used for alignment.

What counts as spoken text:
  - YAML front matter, fenced code blocks, HTML comments, tables, and lines that are only a bracketed tag
    ([SHOW: …], [MUSIC], [PAUSE]) are skipped. Bracketed tags inside a paragraph are removed too.
  - Every heading starts the next section. Text before the first heading is section 1.
  - Each list item is its own paragraph. Inline Markdown (**bold**, _italic_, `code`, [links](url)) is reduced to its text.
"""
import json, logging, re, subprocess, sys, time
from dataclasses import dataclass, field, asdict
from pathlib import Path

log = logging.getLogger("voiceover-cleanup")

def setup_logging(verbose=False):
    logging.basicConfig(level=logging.DEBUG if verbose else logging.INFO, stream=sys.stderr,
                        format="%(asctime)s [%(name)s] %(levelname)s %(message)s", datefmt="%Y-%m-%dT%H:%M:%S")

# ---------------------------------------------------------------- tokens
_APOS = re.compile(r"[’'`]")
_NONWORD = re.compile(r"[^a-z0-9]+")
FILLERS = {"um", "umm", "uh", "uhh", "uhm", "erm", "er", "ah", "ahh", "hmm", "hm", "mm", "mhm"}

def tokens(text):
    """Lowercase alphanumeric tokens. Apostrophes are dropped (it's → its); everything else splits."""
    t = _APOS.sub("", text.lower()).replace("&", " and ")
    return [x for x in _NONWORD.split(t) if x]

# ---------------------------------------------------------------- script model
HEADING_RX = re.compile(r"^#{1,6}\s+(.+?)\s*#*\s*$", re.M)
TAG_LINE_RX = re.compile(r"^\s*\[[^\]]*\]\s*$")
INLINE_TAG_RX = re.compile(r"\[(?:[A-Z][A-Z ]*[A-Z])(?::[^\]]*)?\](?!\()")    # [SHOW: x], [MUSIC], [PAUSE] — not [link](url)
LIST_RX = re.compile(r"^\s*(?:[-*+]|\d+[.)])\s+")
SENT_RX = re.compile(r"(?<=[.!?])\s+(?=[A-Z\"(“‘'])")

def _plain(text):
    text = INLINE_TAG_RX.sub(" ", text)
    text = re.sub(r"!?\[([^\]]*)\]\([^)]*\)", r"\1", text)             # links and images → their text
    text = re.sub(r"(\*\*|__|\*|`)", "", text)
    text = re.sub(r"(?<!\w)_(.+?)_(?!\w)", r"\1", text)
    return " ".join(text.split())

@dataclass
class Sentence:
    id: str
    section: int
    para: int
    idx: int
    text: str
    tok_start: int = 0
    tok_end: int = 0

@dataclass
class Section:
    n: int
    title: str
    paragraphs: list = field(default_factory=list)

    @property
    def sentences(self):
        return [s for p in self.paragraphs for s in p]

class Script:
    def __init__(self, path):
        self.path = Path(path)
        self.sections = []
        self.tokens = []          # flat token list
        self.tok_sent = []        # sentence index for each token
        self.sentences = []
        self._parse()

    def _sections(self, text):
        hs = list(HEADING_RX.finditer(text))
        out, pre = [], text[: hs[0].start()] if hs else text
        if pre.strip():
            out.append((1, "", pre))
        for i, m in enumerate(hs):
            out.append((len(out) + 1, m.group(1).strip(), text[m.end(): hs[i + 1].start() if i + 1 < len(hs) else len(text)]))
        return out

    def _parse(self):
        text = self.path.read_text(encoding="utf-8").replace("\r\n", "\n")
        text = re.sub(r"\A---\n.*?\n---\n", "", text, flags=re.S)                # front matter
        text = re.sub(r"^(```|~~~).*?^\1\s*$", "", text, flags=re.S | re.M)     # fenced code blocks
        text = re.sub(r"<!--.*?-->", "", text, flags=re.S)
        for n, title, body in self._sections(text):
            sec = Section(n, title)
            para_n = 0
            for block in re.split(r"\n\s*\n", body):
                lines = [l for l in block.split("\n") if l.strip() and not TAG_LINE_RX.match(l)
                         and not HEADING_RX.match(l) and not re.match(r"^\s*(\||---+\s*$|\*\*\*+\s*$)", l)]
                # each list item is its own paragraph; other lines join into one paragraph
                paras, cur = [], []
                for l in lines:
                    if LIST_RX.match(l):
                        if cur:
                            paras.append(cur)
                        cur = [LIST_RX.sub("", l)]
                    else:
                        cur.append(l.lstrip("> "))
                if cur:
                    paras.append(cur)
                for p in paras:
                    plain = _plain(" ".join(p))
                    if not tokens(plain):
                        continue
                    para_n += 1
                    paragraph = []
                    for j, sent in enumerate(SENT_RX.split(plain), 1):
                        s = Sentence(f"S{n}.P{para_n}.{j}", n, para_n, j, sent)
                        s.tok_start = len(self.tokens)
                        toks = tokens(sent)
                        self.tokens += toks
                        self.tok_sent += [len(self.sentences)] * len(toks)
                        s.tok_end = len(self.tokens)
                        self.sentences.append(s)
                        paragraph.append(s)
                    sec.paragraphs.append(paragraph)
            if sec.paragraphs:
                self.sections.append(sec)
        if not self.sentences:
            raise SystemExit(f"no spoken text found in {self.path}")

    def sentence_by_id(self, sid):
        return next(s for s in self.sentences if s.id == sid)

# ---------------------------------------------------------------- project folder
def project_paths(project):
    """Everything for one cleanup lives in one folder: script.md (a frozen copy of the script), work/ (intermediate
    files), clean.wav (the result), review.html."""
    root = Path(project).expanduser().resolve()
    return dict(root=root, script=root / "script.md", work=root / "work", clean=root / "clean.wav", review=root / "review.html")

# ---------------------------------------------------------------- io helpers
def write_json(path, obj):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(obj, indent=2, default=lambda o: asdict(o) if hasattr(o, "__dataclass_fields__") else str(o)))

def read_json(path):
    return json.loads(Path(path).read_text())

def run(cmd, **kw):
    """Run a command (list form, no shell). Raises with stderr tail on failure."""
    t0 = time.time()
    p = subprocess.run(cmd, capture_output=True, text=True, **kw)
    if p.returncode != 0:
        raise RuntimeError(f"command failed ({p.returncode}): {' '.join(map(str, cmd[:6]))} …\n{p.stderr[-2000:]}")
    log.debug("ran %s in %.1fs", cmd[0], time.time() - t0)
    return p

def ffprobe_duration(path):
    p = run(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "default=nw=1:nk=1", str(path)])
    return float(p.stdout.strip())

def fmt_time(t):
    m, s = divmod(max(0.0, t), 60)
    return f"{int(m):02d}:{s:06.3f}"
