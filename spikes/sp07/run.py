"""SP-07: signal-word and cross-reference counts for Sections 23 and 43 (counts only)."""
import json, re, sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import pymupdf
from _common import PDF

SIG = ["WARNING", "CAUTION", "NOTE", "DANGER", "IMPORTANT", "YES", "NO"]
XREF = {"Fig.": r"\bFig\.", "See page": r"\bSee\s+page\b", "Refer to": r"\bRefer\s+to\b"}
doc = pymupdf.open(PDF)
res = {}
for name, (a, b) in {"sec23_p187_228": (187, 228), "sec43_p661_682": (661, 682)}.items():
    sig = {k: 0 for k in SIG}; xr = {k: 0 for k in XREF}; pages_sig = {k: set() for k in SIG}
    pages_xr = {k: set() for k in XREF}
    for p in range(a - 1, b):
        t = doc[p].get_text()
        for k in SIG:
            n = len(re.findall(rf"(?<![A-Za-z]){k}(?![A-Za-z])", t))   # case-sensitive whole word
            sig[k] += n; pages_sig[k].add(p + 1) if n else None
        for k, rx in XREF.items():
            n = len(re.findall(rx, t, flags=re.I))
            xr[k] += n; pages_xr[k].add(p + 1) if n else None
    full = "\n".join(doc[p].get_text() for p in range(a - 1, b))
    ci = {k: len(re.findall(rf"\b{k}\b", full, flags=re.I)) for k in ['warning','caution','note','danger','important']}
    res[name] = dict(case_insensitive=ci, pages=b - a + 1, signal_words=sig, signal_pages={k: len(v) for k, v in pages_sig.items()},
                     crossrefs=xr, crossref_pages={k: len(v) for k, v in pages_xr.items()})
print(json.dumps(res, indent=1))
