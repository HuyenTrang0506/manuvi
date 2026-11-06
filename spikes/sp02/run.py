"""SP-02: fragment merge on PDF page 666 (printed 43-6) and neighbours."""
import json, sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import pymupdf
from _common import PDF
from src.labels.merge import count_suspicious, extract_spans, merge_adjacent

doc = pymupdf.open(PDF)
res = {}
for pno in (665, 662, 663, 666, 667):
    sp = extract_spans(doc[pno]); out = {}
    out["spans"] = len(sp); out["suspicious_before"] = count_suspicious(sp)
    for g in (0.4, 0.6, 1.0):
        m = merge_adjacent(sp, gap_factor=g)
        out[f"gap{g}"] = dict(labels=len(m), suspicious_after=count_suspicious(m), merged_groups=sum(1 for x in m if x.parts > 1))
    res[f"p{pno+1}"] = out
print(json.dumps(res, indent=1))
