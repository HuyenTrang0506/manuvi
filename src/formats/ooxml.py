"""Shared Office Open XML plumbing: safe zip/XML IO, the paragraph engine (docx `w:p` and pptx `a:p`), language and
Vietnamese-font handling.

Why direct OOXML (zipfile + lxml) and not python-docx / python-pptx: they cannot reach footnotes, endnotes, text boxes,
chart titles or tracked-change runs, and they re-serialise parts we never meant to touch. Here every package entry we do
not edit is copied byte for byte, and an edited part changes only the paragraphs that were translated.

Paragraph engine: a paragraph is flattened to tagged text. Runs with identical formatting merge into one *span*; when a
paragraph has several spans each gets `<rN>..</rN>`; hyperlinks / tracked insertions are *groups* `<hN>..</hN>`; fields,
drawings, footnote references and other opaque content are self-closing `<xN/>` tags that are moved back unchanged.
Tabs and soft line breaks stay in the text as `\\t` and `\\n`. After translation the tagged text is parsed and the
original run properties are re-attached to each span, so bold/italic/colour/size survive reordering by the translator.
"""
from __future__ import annotations

import copy
import re
import zipfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Optional

from lxml import etree

from .base import FormatError
from .tags import Node, esc, parse, tags_match

PARSER = etree.XMLParser(resolve_entities=False, no_network=True, load_dtd=False, huge_tree=False, dtd_validation=False)
VI_LANG = "vi-VN"
XML_NS = "http://www.w3.org/XML/1998/namespace"

# Fonts known to carry the Vietnamese Latin Extended Additional range. Anything else gets FALLBACK_FONT on translated
# runs (display / symbol fonts often lack it). Theme fonts (asciiTheme / +mn-lt) are never touched.
VI_FONTS = {f.lower() for f in """Arial;Arial Narrow;Arial Unicode MS;Times New Roman;Calibri;Calibri Light;Cambria;Cambria Math;
Tahoma;Verdana;Segoe UI;Segoe UI Light;Segoe UI Semibold;Courier New;Georgia;Trebuchet MS;Consolas;Candara;Constantia;
Corbel;Palatino Linotype;Book Antiqua;Lucida Sans Unicode;Microsoft Sans Serif;Century Gothic;Franklin Gothic Book;
Noto Sans;Noto Serif;DejaVu Sans;DejaVu Serif;DejaVu Sans Mono;Liberation Sans;Liberation Serif;Liberation Mono;
Arimo;Tinos;Cousine;Carlito;Caladea;Open Sans;Roboto;Be Vietnam Pro;Aptos;Aptos Display;Aptos Narrow;Source Sans Pro;
Lato;Montserrat;Times;Helvetica;Helvetica Neue;SimSun;MS Gothic;Malgun Gothic;Yu Gothic;Meiryo""".replace("\n", "").split(";")}
SYMBOL_FONTS = {"symbol", "wingdings", "wingdings 2", "wingdings 3", "webdings", "marlett", "zapfdingbats"}
FALLBACK_FONT = "Arial"
URL_RE = re.compile(r"(?:https?://|www\.)[^\s<>]*[^\s<>.,;:!?)\]'\"]|[\w.+-]+@[\w-]+\.[\w.-]+")


# ------------------------------------------------------------------ package IO
class Package:
    """A zip package read fully into memory (limits are enforced by detect.check_zip before this is used)."""

    def __init__(self, path: Path):
        try:
            with zipfile.ZipFile(path) as zf:
                self.infos = zf.infolist()
                self.data = {i.filename: zf.read(i) for i in self.infos}
        except zipfile.BadZipFile:
            raise FormatError("BAD_FORMAT", "corrupt zip container")
        self.roots: dict[str, etree._Element] = {}
        self.dirty: set[str] = set()

    def has(self, name: str) -> bool:
        return name in self.data

    def xml(self, name: str) -> etree._Element:
        if name not in self.roots:
            try:
                self.roots[name] = etree.fromstring(self.data[name], PARSER)
            except etree.XMLSyntaxError:
                raise FormatError("BAD_FORMAT", f"unreadable XML part {name}")
        return self.roots[name]

    def touch(self, name: str) -> None:
        self.dirty.add(name)

    def write(self, out: Path) -> None:
        tmp = Path(str(out) + ".part")
        with zipfile.ZipFile(tmp, "w", zipfile.ZIP_DEFLATED) as zf:
            for i in self.infos:
                if i.is_dir():
                    continue
                data = self.data[i.filename]
                if i.filename in self.dirty:
                    data = etree.tostring(self.roots[i.filename], xml_declaration=True, encoding="UTF-8",
                                          standalone=True)
                zi = zipfile.ZipInfo(i.filename, date_time=i.date_time)
                zi.compress_type = zipfile.ZIP_STORED if i.compress_type == zipfile.ZIP_STORED else zipfile.ZIP_DEFLATED
                zi.external_attr = i.external_attr
                zf.writestr(zi, data)
        tmp.replace(out)


def rels_of(pkg: Package, part: str) -> dict[str, tuple[str, str]]:
    """relationship id -> (type suffix, resolved target part name) for internal relationships of `part`."""
    d, _, f = part.rpartition("/")
    rp = f"{d}/_rels/{f}.rels" if d else f"_rels/{f}.rels"
    if not pkg.has(rp):
        return {}
    out = {}
    for r in pkg.xml(rp):
        if r.get("TargetMode") == "External":
            continue
        tgt = r.get("Target", "")
        parts = ([] if tgt.startswith("/") else d.split("/") if d else []) + tgt.lstrip("/").split("/")
        norm: list[str] = []
        for p in parts:
            if p == "..":
                if norm:
                    norm.pop()
            elif p and p != ".":
                norm.append(p)
        out[r.get("Id")] = (r.get("Type", "").rsplit("/", 1)[-1], "/".join(norm))
    return out


# ------------------------------------------------------------------ dialects
@dataclass(frozen=True)
class Dialect:
    ns: str
    p: str
    r: str
    t: str
    rpr: str
    br: str
    ppr: str
    wrappers: frozenset
    opaque_para_children: bool      # unknown paragraph children are opaque atoms (docx: fldSimple, sdt ...)
    leave: frozenset                # paragraph children left in place (never atoms)
    fld: frozenset = frozenset()    # paragraph-level elements that are opaque fields (pptx a:fld)
    tab: Optional[str] = None
    is_docx: bool = True


def _w(n): return "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}" + n
def _a(n): return "{http://schemas.openxmlformats.org/drawingml/2006/main}" + n


DOCX = Dialect(
    ns="w", p=_w("p"), r=_w("r"), t=_w("t"), rpr=_w("rPr"), br=_w("br"), ppr=_w("pPr"), tab=_w("tab"),
    wrappers=frozenset({_w("hyperlink"), _w("ins"), _w("smartTag")}), opaque_para_children=True,
    leave=frozenset({_w("pPr"), _w("bookmarkStart"), _w("bookmarkEnd"), _w("proofErr"), _w("commentRangeStart"),
                     _w("commentRangeEnd"), _w("permStart"), _w("permEnd"), _w("del"), _w("moveFrom"),
                     _w("moveFromRangeStart"), _w("moveFromRangeEnd"), _w("moveToRangeStart"),
                     _w("moveToRangeEnd"), _w("rPr")}))
PPTX = Dialect(
    ns="a", p=_a("p"), r=_a("r"), t=_a("t"), rpr=_a("rPr"), br=_a("br"), ppr=_a("pPr"), is_docx=False,
    wrappers=frozenset(), opaque_para_children=False, leave=frozenset({_a("pPr"), _a("endParaRPr")}),
    fld=frozenset({_a("fld")}))


# ------------------------------------------------------------------ paragraph model
def _key(rpr: Optional[etree._Element], d: Dialect) -> str:
    if rpr is None:
        return ""
    c = copy.deepcopy(rpr)
    for e in list(c):
        if etree.QName(e).localname in ("lang", "noProof", "rPrChange"):
            c.remove(e)
    for a in list(c.attrib):
        if a in ("lang", "err", "dirty", "smtClean", "smtId", "noProof"):
            del c.attrib[a]
    return etree.tostring(c, method="c14n").decode()


@dataclass
class N:                       # model node
    t: str                     # span | group | self
    key: str = ""
    rpr: Optional[etree._Element] = None
    text: str = ""
    el: Optional[etree._Element] = None            # group wrapper
    elems: list = field(default_factory=list)      # self: opaque elements
    kids: list = field(default_factory=list)       # group children
    name: str = ""


@dataclass
class ParaModel:
    p: etree._Element
    nodes: list
    atoms: list
    specs: dict
    text: str
    default_rpr: Optional[etree._Element]
    protected: list


def build_model(p: etree._Element, d: Dialect) -> Optional[ParaModel]:
    atoms: list = []
    in_field = [0]

    def run_pieces(r) -> list:
        """Pieces of one run: ('t', text, rpr) / ('o', element)."""
        rpr = r.find(d.rpr)
        kids = [c for c in r if c.tag != d.rpr]
        has_fld = any(etree.QName(c).localname == "fldChar" for c in kids) if d.is_docx else False
        if in_field[0] > 0 or has_fld:
            for c in kids:
                if etree.QName(c).localname == "fldChar":
                    ft = c.get(_w("fldCharType"))
                    in_field[0] += 1 if ft == "begin" else -1 if ft == "end" else 0
            return [("o", r)]
        out: list = []
        for c in kids:
            ln = etree.QName(c).localname
            if c.tag == d.t:
                out.append(("t", c.text or "", rpr))
            elif d.tab and c.tag == d.tab:
                out.append(("t", "\t", rpr))
            elif ln == "cr" or (c.tag == d.br and c.get(_w("type") if d.is_docx else "type") in (None, "textWrapping")):
                out.append(("t", "\n", rpr))
            elif ln in ("lastRenderedPageBreak", "proofErr"):
                continue
            else:
                out.append(("o", c, r, rpr))
        return out

    def walk(children, top: bool = True) -> list:
        nodes: list = []

        def add_text(text, rpr):
            k = _key(rpr, d)
            if nodes and nodes[-1].t == "span" and nodes[-1].key == k:
                nodes[-1].text += text
            else:
                nodes.append(N("span", key=k, rpr=rpr, text=text))

        def add_opaque(elem):
            if nodes and nodes[-1].t == "self":
                nodes[-1].elems.append(elem)
            else:
                nodes.append(N("self", elems=[elem]))

        for el in children:
            if el.tag == d.r:
                if top:
                    atoms.append(el)
                for pc in run_pieces(el):
                    if pc[0] == "t":
                        add_text(pc[1], pc[2])
                    elif len(pc) == 2:
                        add_opaque(pc[1])
                    else:   # opaque child of a mixed run: wrap in a fresh run with the same properties
                        _, child, run, rpr = pc
                        nr = etree.Element(d.r, run.attrib)
                        if rpr is not None:
                            nr.append(copy.deepcopy(rpr))
                        nr.append(copy.deepcopy(child))
                        add_opaque(nr)
            elif el.tag in d.wrappers:
                if top:
                    atoms.append(el)
                nodes.append(N("group", el=el, kids=walk(list(el), False)))
            elif el.tag == d.br:
                if top:
                    atoms.append(el)
                add_text("\n", el.find(d.rpr))
            elif el.tag in d.fld or (d.opaque_para_children and el.tag not in d.leave and isinstance(el.tag, str)):
                if top:
                    atoms.append(el)
                add_opaque(el)
        return nodes

    nodes = walk(list(p))
    # tag naming + text
    specs: dict = {}
    counter = [0]
    spans = []

    def count(ns):
        for n in ns:
            if n.t == "span":
                spans.append(n)
            elif n.t == "group":
                count(n.kids)
    count(nodes)
    tag_spans = len(spans) > 1
    protected: list[str] = []

    def prot_text(s: str) -> str:
        out, pos = [], 0
        for m in URL_RE.finditer(s):
            out.append(esc(s[pos:m.start()]))
            e = esc(m.group(0))
            if e not in protected:
                protected.append(e)
            out.append(e)
            pos = m.end()
        out.append(esc(s[pos:]))
        return "".join(out)

    def render(ns) -> str:
        out = []
        for n in ns:
            counter[0] += 1
            if n.t == "span":
                body = prot_text(n.text)
                if tag_spans:
                    n.name = f"r{counter[0]}"
                    specs[n.name] = n
                    body = f"<{n.name}>{body}</{n.name}>"
                out.append(body)
            elif n.t == "group":
                n.name = f"h{counter[0]}"
                specs[n.name] = n
                out.append(f"<{n.name}>{render(n.kids)}</{n.name}>")
            else:
                n.name = f"x{counter[0]}"
                specs[n.name] = n
                out.append(f"<{n.name}/>")
        return "".join(out)

    text = render(nodes)
    if not any(ch.isalpha() for ch in re.sub(r"<[^>]*>", "", text)):
        return None
    return ParaModel(p, nodes, atoms, specs, text, spans[0].rpr if spans else None, protected)


# ------------------------------------------------------------------ language and fonts
def _set_lang(rpr: etree._Element, d: Dialect) -> None:
    if not d.is_docx:
        rpr.set("lang", VI_LANG)
        return
    lang = rpr.find(_w("lang"))
    if lang is None:
        lang = etree.Element(_w("lang"))
        after = [c for c in rpr if etree.QName(c).localname in ("eastAsianLayout", "specVanish", "oMath")]
        if after:
            after[0].addprevious(lang)
        else:
            rpr.append(lang)
    lang.set(_w("val"), VI_LANG)


def _fix_font(rpr: etree._Element, d: Dialect, stats: dict) -> None:
    if d.is_docx:
        rf = rpr.find(_w("rFonts"))
        if rf is None or any(k.endswith(("asciiTheme", "hAnsiTheme")) for k in rf.attrib):
            return
        name = rf.get(_w("ascii")) or rf.get(_w("hAnsi"))
        if name and name.lower() not in VI_FONTS and name.lower() not in SYMBOL_FONTS:
            for a in ("ascii", "hAnsi", "cs"):
                if rf.get(_w(a)) is not None or a in ("ascii", "hAnsi"):
                    rf.set(_w(a), FALLBACK_FONT)
            stats["font_substitutions"] = stats.get("font_substitutions", 0) + 1
    else:
        lat = rpr.find(_a("latin"))
        if lat is None:
            return
        name = lat.get("typeface", "")
        if name and not name.startswith("+") and name.lower() not in VI_FONTS and name.lower() not in SYMBOL_FONTS:
            lat.set("typeface", FALLBACK_FONT)
            stats["font_substitutions"] = stats.get("font_substitutions", 0) + 1


def _new_rpr(rpr: Optional[etree._Element], d: Dialect, stats: dict) -> etree._Element:
    r = copy.deepcopy(rpr) if rpr is not None else etree.Element(d.rpr)
    _fix_font(r, d, stats)
    _set_lang(r, d)
    return r


def make_runs(text: str, rpr: Optional[etree._Element], d: Dialect, stats: dict) -> list:
    """Elements for translated plain text: one run (docx) or runs separated by `a:br` (pptx)."""
    out: list = []
    if d.is_docx:
        run = etree.Element(d.r)
        run.append(_new_rpr(rpr, d, stats))
        for piece in re.split(r"(\t|\n)", text):
            if piece == "\t":
                etree.SubElement(run, d.tab)
            elif piece == "\n":
                etree.SubElement(run, d.br)
            elif piece:
                t = etree.SubElement(run, d.t)
                t.text = piece
                t.set(f"{{{XML_NS}}}space", "preserve")
        return [run]
    for piece in re.split(r"(\n)", text):
        if piece == "\n":
            br = etree.Element(d.br)
            br.append(_new_rpr(rpr, d, stats))
            out.append(br)
        elif piece:
            run = etree.Element(d.r)
            run.append(_new_rpr(rpr, d, stats))
            etree.SubElement(run, d.t).text = piece
            out.append(run)
    return out


def apply_translation(m: ParaModel, translated: str, d: Dialect, stats: dict) -> bool:
    """Replace the paragraph's content with the translation. False (paragraph untouched) when tags don't match."""
    if not tags_match(m.text, translated):
        return False
    tree = parse(translated)

    def emit(nodes, rpr) -> list:
        out: list = []
        for ch in nodes:
            if ch.kind == "text":
                out.extend(make_runs(ch.text, rpr, d, stats))
                continue
            spec = m.specs[ch.name]
            if ch.kind == "self":
                out.extend(spec.elems)
            elif spec.t == "span":
                out.extend(emit(ch.children, spec.rpr))
            else:
                w = etree.Element(spec.el.tag, spec.el.attrib)
                inner = _first_rpr(spec.kids)
                for e in emit(ch.children, inner if inner is not None else rpr):
                    w.append(e)
                out.append(w)
        return out

    new = emit(tree.children, m.default_rpr)
    p = m.p
    first = min(p.index(a) for a in m.atoms) if m.atoms else len(p)
    for a in m.atoms:
        if a.getparent() is p:
            p.remove(a)
    first = min(first, len(p))
    for i, e in enumerate(new):
        p.insert(first + i, e)
    return True


def _first_rpr(nodes) -> Optional[etree._Element]:
    for n in nodes:
        if n.t == "span":
            return n.rpr
        if n.t == "group":
            r = _first_rpr(n.kids)
            if r is not None:
                return r
    return None
