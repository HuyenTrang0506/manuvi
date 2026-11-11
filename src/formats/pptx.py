"""PPTX adapter (direct OOXML, shares the paragraph engine with DOCX).

Translated: text in every shape (placeholders, text boxes, grouped shapes), table cells, speaker notes and chart titles
(any `a:p` in a slide, notes slide or chart part). Slide numbers, dates and other `a:fld` fields, pictures, SmartArt,
layouts and masters are left alone.
Shrink-to-fit guard: when the translation is estimated to overflow its shape (and is longer than the source was), the
shape gets `normAutofit` with a font scale stepped down 96% -> 92% -> 88%; if 88% still overflows, the segments of that
shape are reported as needs_review (`OVERFLOW`). The estimate is a character-width heuristic, not a layout engine;
PowerPoint re-fits normAutofit text when the shape is edited.
"""
from __future__ import annotations

import math
import re
from pathlib import Path
from typing import Mapping, Optional

from lxml import etree

from .base import RebuildReport, Segment
from .ooxml import PPTX, Package, apply_translation, build_model, rels_of, _a

P_NS = "http://schemas.openxmlformats.org/presentationml/2006/main"
R_NS = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
def _p(n): return "{%s}%s" % (P_NS, n)

STEPS = (0.96, 0.92, 0.88)
FLOOR = 0.88
CHAR_W = 0.5          # average glyph width as a fraction of the font size (Latin + Vietnamese diacritics)
LINE_H = 1.2


def slide_order(pkg: Package) -> list[str]:
    pres = pkg.xml("ppt/presentation.xml")
    rels = rels_of(pkg, "ppt/presentation.xml")
    out = []
    for sid in pres.iter(_p("sldId")):
        rid = sid.get("{%s}id" % R_NS)
        if rid in rels and rels[rid][1] in pkg.data:
            out.append(rels[rid][1])
    return out


def _shape_of(p: etree._Element) -> Optional[etree._Element]:
    for a in p.iterancestors():
        if a.tag == _p("sp"):
            return a
    return None


def _ph(sp) -> Optional[tuple]:
    ph = sp.find(f"{_p('nvSpPr')}/{_p('nvPr')}/{_p('ph')}")
    return None if ph is None else (ph.get("type", "body"), ph.get("idx"))


def _box(pkg: Package, slide: str, sp) -> Optional[tuple[int, int]]:
    ext = sp.find(f"{_p('spPr')}/{_a('xfrm')}/{_a('ext')}")
    if ext is not None:
        return int(ext.get("cx")), int(ext.get("cy"))
    ph = _ph(sp)
    if ph is None:
        return None
    part = slide
    for _ in range(2):          # slide -> layout -> master
        nxt = [t for (typ, t) in rels_of(pkg, part).values() if typ in ("slideLayout", "slideMaster")]
        if not nxt or nxt[0] not in pkg.data:
            return None
        part = nxt[0]
        for s in pkg.xml(part).iter(_p("sp")):
            if _ph(s) and (_ph(s)[1] == ph[1] and ph[1] is not None or _ph(s)[0] == ph[0]):
                e = s.find(f"{_p('spPr')}/{_a('xfrm')}/{_a('ext')}")
                if e is not None:
                    return int(e.get("cx")), int(e.get("cy"))
    return None


def estimate_height(sp, box: tuple[int, int], scale: float) -> float:
    """Estimated text height in points."""
    body = sp.find(_p("txBody"))
    bp = body.find(_a("bodyPr"))
    li, ri = int(bp.get("lIns", 91440)), int(bp.get("rIns", 91440))
    ti, bi = int(bp.get("tIns", 45720)), int(bp.get("bIns", 45720))
    width = max(1.0, (box[0] - li - ri) / 12700)
    h = (ti + bi) / 12700
    for p in body.findall(_a("p")):
        size = 18.0
        for r in [*p.findall(_a("r")), *p.findall(_a("endParaRPr"))]:
            rp = r if r.tag == _a("endParaRPr") else r.find(_a("rPr"))
            if rp is not None and rp.get("sz"):
                size = int(rp.get("sz")) / 100
                break
        size *= scale
        text = "".join("\n" if e.tag == _a("br") else (e.findtext(_a("t")) or "") for e in p
                       if e.tag in (_a("r"), _a("br"), _a("fld")))
        cpl = max(1, int(width / (CHAR_W * size)))
        lines = sum(max(1, math.ceil(len(x) / cpl)) for x in text.split("\n"))
        h += lines * size * LINE_H
    return h


def _set_autofit(sp, scale: float) -> None:
    bp = sp.find(_p("txBody")).find(_a("bodyPr"))
    for tag in ("noAutofit", "normAutofit", "spAutoFit"):
        for e in bp.findall(_a(tag)):
            bp.remove(e)
    fit = etree.Element(_a("normAutofit"))
    fit.set("fontScale", str(int(round(scale * 100000))))
    ptw = bp.find(_a("prstTxWarp"))
    if ptw is not None:
        ptw.addnext(fit)
    else:
        bp.insert(0, fit)


class PptxAdapter:
    name = "pptx"
    extensions = (".pptx",)

    def _load(self, path: Path):
        pkg = Package(path)
        segs: list[Segment] = []
        n = 0
        for si, slide in enumerate(slide_order(pkg), 1):
            rels = rels_of(pkg, slide)
            parts = [(slide, f"slide{si}")]
            parts += [(t, f"slide{si}:notes") for (typ, t) in rels.values() if typ == "notesSlide" and t in pkg.data]
            parts += [(t, f"slide{si}:chart") for (typ, t) in rels.values() if typ == "chart" and t in pkg.data]
            title = self._title(pkg.xml(slide))
            for part, label in parts:
                root = pkg.xml(part)
                for p in root.iter(PPTX.p):
                    m = build_model(p, PPTX)
                    if m is None:
                        continue
                    anc = {etree.QName(a).localname for a in p.iterancestors() if isinstance(a.tag, str)}
                    sp = _shape_of(p)
                    ph = _ph(sp) if sp is not None else None
                    if part != slide and label.endswith("notes"):
                        kind = "note"
                    elif label.endswith("chart"):
                        kind = "caption"
                    elif "tc" in anc:
                        kind = "table_cell"
                    elif ph and ph[0] in ("title", "ctrTitle"):
                        kind = "title"
                    else:
                        kind = "shape"
                    n += 1
                    segs.append(Segment(id=f"S{n:04d}", text=m.text, kind=kind, protected=m.protected,
                                        context="" if kind == "title" else title, page=si, section=label,
                                        meta={"model": m, "part": part, "slide": slide,
                                              "shape": sp if kind != "table_cell" and part == slide else None}))
        return pkg, segs

    @staticmethod
    def _title(root) -> str:
        for sp in root.iter(_p("sp")):
            ph = _ph(sp)
            if ph and ph[0] in ("title", "ctrTitle"):
                return "".join(t.text or "" for t in sp.iter(_a("t")))
        return ""

    def extract(self, path: Path) -> list[Segment]:
        return self._load(path)[1]

    def rebuild(self, path: Path, translations: Mapping[str, str], out: Path, *, force: bool = False) -> RebuildReport:
        pkg, segs = self._load(path)
        rep = RebuildReport()
        stats: dict = {}
        shapes: dict[int, dict] = {}
        for s in segs:
            tr = translations.get(s.id)
            if tr is None or (tr == s.text and not force):
                rep.segments_untouched += 1
                continue
            sp = s.meta["shape"]
            box = _box(pkg, s.meta["slide"], sp) if sp is not None and sp.find(_p("txBody")) is not None else None
            if box is not None and id(sp) not in shapes:
                shapes[id(sp)] = {"sp": sp, "box": box, "before": estimate_height(sp, box, 1.0), "ids": []}
            if apply_translation(s.meta["model"], tr, PPTX, stats):
                pkg.touch(s.meta["part"])
                rep.segments_applied += 1
                if id(sp) in shapes:
                    shapes[id(sp)]["ids"].append(s.id)
            else:
                rep.segments_untouched += 1
                rep.needs_review[s.id] = "FMT001: inline tags changed; source kept"
        for info in shapes.values():
            sp, box = info["sp"], info["box"]
            if not info["ids"] or sp.find(_p("txBody")).find(_a("bodyPr")).find(_a("spAutoFit")) is not None:
                continue
            limit = box[1] / 12700
            now = estimate_height(sp, box, 1.0)
            if now <= limit or now <= info["before"]:
                continue
            for sc in STEPS:
                if estimate_height(sp, box, sc) <= limit:
                    _set_autofit(sp, sc)
                    rep.notes.append(f"shrunk shape to {int(sc * 100)}%")
                    break
            else:
                _set_autofit(sp, FLOOR)
                for i in info["ids"]:
                    rep.needs_review[i] = f"OVERFLOW: text may not fit its shape even at {int(FLOOR * 100)}% size"
        if stats.get("font_substitutions"):
            rep.notes.append(f"font_substitutions={stats['font_substitutions']}")
        pkg.write(out)
        return rep
