from __future__ import annotations

import re
from collections import defaultdict
from pathlib import Path
from typing import Optional

import pymupdf
import yaml
from pydantic import BaseModel

DEFAULT_PATTERNS = Path(__file__).with_name("patterns.yaml")
STOP = frozenset("a an and are as at be by for from in into is it of on or the to with when if then not no "
                 "this that these those can must should shall may use used".split())
WORD = re.compile(r"[A-Za-z][A-Za-z\-]+")


class TermCandidate(BaseModel):
    term: str
    count: int
    contexts: list[str]
    pages: list[int]
    protected: bool = False
    translate: bool = True


def load_patterns(path: Optional[Path] = None) -> list[tuple[str, "re.Pattern[str]"]]:
    data = yaml.safe_load(Path(path or DEFAULT_PATTERNS).read_text(encoding="utf-8"))
    return [(p["name"], re.compile(p["regex"])) for p in data["protected_patterns"]]


def _snippet(text: str, start: int, end: int, width: int = 40) -> str:
    s = text[max(0, start - width):end + width]
    return " ".join(s.split())


def extract_terms(pdf: Path, patterns: Optional[list] = None, max_ngram: int = 3,
                  min_count: int = 1, pages: Optional[list[int]] = None) -> list[TermCandidate]:
    patterns = patterns if patterns is not None else load_patterns()
    prot: dict[str, dict] = {}
    cand: dict[str, dict] = {}
    with pymupdf.open(pdf) as doc:
        for i in range(doc.page_count):
            n = i + 1
            if pages and n not in pages:
                continue
            text = doc[i].get_text("text")
            spans: list[tuple[int, int]] = []
            for _, rx in patterns:
                for m in rx.finditer(text):
                    spans.append(m.span())
                    d = prot.setdefault(m.group(0), {"count": 0, "ctx": [], "pages": set()})
                    d["count"] += 1
                    d["pages"].add(n)
                    if len(d["ctx"]) < 2:
                        d["ctx"].append(_snippet(text, *m.span()))
            words = [m for m in WORD.finditer(text)
                     if not any(a < m.end() and m.start() < b for a, b in spans)]
            for k in range(len(words)):
                for L in range(1, max_ngram + 1):
                    seq = words[k:k + L]
                    if len(seq) < L:
                        break
                    # n-gram words must be adjacent (only whitespace between)
                    if any(text[seq[j].end():seq[j + 1].start()].strip() for j in range(L - 1)):
                        break
                    toks = [w.group(0).lower() for w in seq]
                    if toks[0] in STOP or toks[-1] in STOP or len(toks[0]) < 3 and L == 1:
                        continue
                    term = " ".join(toks)
                    d = cand.setdefault(term, {"count": 0, "ctx": [], "pages": set()})
                    d["count"] += 1
                    d["pages"].add(n)
                    if len(d["ctx"]) < 2:
                        d["ctx"].append(_snippet(text, seq[0].start(), seq[-1].end()))
    out = [TermCandidate(term=t, count=d["count"], contexts=d["ctx"], pages=sorted(d["pages"]),
                         protected=True, translate=False) for t, d in prot.items()]
    out += [TermCandidate(term=t, count=d["count"], contexts=d["ctx"], pages=sorted(d["pages"]))
            for t, d in cand.items() if d["count"] >= min_count]
    out.sort(key=lambda c: (not c.protected, -c.count, c.term))
    return out
