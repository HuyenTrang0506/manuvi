"""DOCX adapter (direct OOXML via zipfile + lxml; see ooxml.py for why not python-docx).

Translated: body paragraphs, headings, list items, table cells (merged cells keep their gridSpan/vMerge because only
paragraph content is replaced), text boxes, headers, footers, footnotes, endnotes, hyperlink text, and accepted text of
tracked insertions (`w:ins` markup is preserved; `w:del` content is left alone). Not translated: field codes and field
results (fldChar/instrText/fldSimple are opaque), comments, drawings, math, document properties.
Output: language vi-VN in document defaults and on translated runs; fonts kept except where the original font is not in
the Vietnamese-capable list (then Arial, counted in the report).
"""
from __future__ import annotations

import re
from pathlib import Path
from typing import Mapping

from lxml import etree

from .base import RebuildReport, Segment
from .ooxml import DOCX, VI_LANG, Package, apply_translation, build_model, _w

_PART_RE = re.compile(r"^word/(document|footnotes|endnotes|header\d+|footer\d+)\.xml$")
_ORDER = {"document": 0, "header": 1, "footer": 2, "footnotes": 3, "endnotes": 4}
_HEADING = re.compile(r"^(heading\s*\d|title|subtitle)", re.I)


def _parts(pkg: Package) -> list[str]:
    names = [n for n in pkg.data if _PART_RE.match(n)]
    def k(n):
        base = re.sub(r"\d+", "", _PART_RE.match(n).group(1))
        return (_ORDER[base], n)
    return sorted(names, key=k)


class DocxAdapter:
    name = "docx"
    extensions = (".docx",)

    def _load(self, path: Path):
        pkg = Package(path)
        segs: list[Segment] = []
        n = 0
        for part in _parts(pkg):
            root = pkg.xml(part)
            label = _PART_RE.match(part).group(1)
            heading = ""
            for p in root.iter(DOCX.p):
                m = build_model(p, DOCX)
                if m is None:
                    continue
                style = p.find(f"{DOCX.ppr}/{_w('pStyle')}")
                sname = style.get(_w("val")) if style is not None else ""
                is_head = bool(sname and _HEADING.match(sname)) or p.find(f"{DOCX.ppr}/{_w('outlineLvl')}") is not None
                anc = {etree.QName(a).localname for a in p.iterancestors() if isinstance(a.tag, str)}
                if is_head:
                    kind = "heading"
                elif "tc" in anc:
                    kind = "table_cell"
                elif p.find(f"{DOCX.ppr}/{_w('numPr')}") is not None:
                    kind = "list_item"
                elif label in ("footnotes", "endnotes"):
                    kind = "note"
                else:
                    kind = "paragraph"
                n += 1
                if is_head:
                    heading = re.sub(r"<[^>]*>", "", m.text)
                segs.append(Segment(id=f"S{n:04d}", text=m.text, kind=kind, protected=m.protected,
                                    context="" if is_head else heading, section=label,
                                    meta={"model": m, "part": part}))
        return pkg, segs

    def extract(self, path: Path) -> list[Segment]:
        return self._load(path)[1]

    def rebuild(self, path: Path, translations: Mapping[str, str], out: Path, *, force: bool = False) -> RebuildReport:
        pkg, segs = self._load(path)
        rep = RebuildReport()
        stats: dict = {}
        for s in segs:
            tr = translations.get(s.id)
            if tr is None or (tr == s.text and not force):
                rep.segments_untouched += 1
                continue
            if apply_translation(s.meta["model"], tr, DOCX, stats):
                pkg.touch(s.meta["part"])
                rep.segments_applied += 1
            else:
                rep.segments_untouched += 1
                rep.needs_review[s.id] = "FMT001: inline tags changed; source kept"
        if rep.segments_applied:
            self._doc_lang(pkg)
        if stats.get("font_substitutions"):
            rep.notes.append(f"font_substitutions={stats['font_substitutions']}")
        pkg.write(out)
        return rep

    @staticmethod
    def _doc_lang(pkg: Package) -> None:
        name = "word/styles.xml"
        if not pkg.has(name):
            return
        root = pkg.xml(name)
        dd = root.find(_w("docDefaults"))
        if dd is None:
            dd = etree.Element(_w("docDefaults"))
            root.insert(0, dd)
        rd = dd.find(_w("rPrDefault"))
        if rd is None:
            rd = etree.SubElement(dd, _w("rPrDefault"))
            if dd.find(_w("pPrDefault")) is not None:
                dd.find(_w("pPrDefault")).addprevious(rd)
        rpr = rd.find(_w("rPr"))
        if rpr is None:
            rpr = etree.SubElement(rd, _w("rPr"))
        lang = rpr.find(_w("lang"))
        if lang is None:
            lang = etree.SubElement(rpr, _w("lang"))
        lang.set(_w("val"), VI_LANG)
        pkg.touch(name)
