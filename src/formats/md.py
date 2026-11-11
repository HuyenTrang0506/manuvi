"""Markdown (CommonMark + GFM tables/strikethrough) adapter.

Parser choice: markdown-it-py. It is CommonMark-compliant, ships GFM tables and strikethrough, and every block token
carries `.map` (source line range), which is what makes byte-exact splicing possible. mistune's AST has no source
positions, and Python-Markdown is not CommonMark.

Method: the parser only *locates* translatable inline blocks (paragraphs, headings, list items, table cells,
blockquote paragraphs). Each block is mapped back to a character range of the original text; a small scanner turns the
inline source into tagged text (emphasis, links, images -> tags; code spans, URLs, LaTeX, HTML, escapes, footnote
refs, emoji shortcodes -> protected literals). `rebuild` splices translations into those ranges and touches nothing
else, so untranslated parts (and a document translated with the identity function) stay byte-identical: line
endings, trailing newline, heading levels, list markers, table delimiter rows, fences, reference definitions.
Anything the locator cannot verify exactly is left untouched.
"""
from __future__ import annotations

import re
from pathlib import Path
from typing import Mapping

from markdown_it import MarkdownIt
from markdown_it.common.utils import normalizeReference

from .base import FormatError, RebuildReport, Segment
from .tags import esc, parse, strip_tags, tags_match, unesc

_EOL = re.compile(r"\r\n|\r|\n")
_LINE = re.compile(r"[^\r\n]*(?:\r\n|\r|\n|$)")
_FM = re.compile(r"\A(---|\+\+\+)[ \t]*\r?\n(.*?)\r?\n(?:---|\.\.\.|\+\+\+)[ \t]*(?:\r?\n|\Z)", re.S)
_FM_KEYS = ("title", "description", "summary", "subtitle")
_FM_LINE = re.compile(r"^(?P<k>" + "|".join(_FM_KEYS) + r"):[ \t]*(?P<q>[\"']?)(?P<v>[^\r\n]*?)(?P=q)[ \t]*$")
_FOOTDEF = re.compile(r"^(?P<pre>[ \t]{0,3}\[\^[^\]\s]+\]:[ \t]+)(?P<body>\S.*?)[ \t]*$")
_LEAD = re.compile(r"^(?:\[[ xX]\](?=[ \t])|\[![A-Za-z]+\])")      # task checkbox, GitHub alert marker

_CODE = re.compile(r"(`+)(?!`)[\s\S]*?(?<!`)\1(?!`)")
_DMATH = re.compile(r"\$\$[\s\S]+?\$\$")
_MATH = re.compile(r"(?<![\\$\w])\$(?![\s$])(?:[^$\\\n]|\\.)+?(?<![\s\\])\$(?![\w$])")
_ESCAPE = re.compile(r"\\[!-/:-@\[-`{-~]")
_AUTOLINK = re.compile(r"<(?:[A-Za-z][A-Za-z0-9+.-]{1,31}:[^\s<>]*|[^\s<>@]+@[^\s<>]+)>")
_HTML = re.compile(r"</?[A-Za-z][A-Za-z0-9-]*(?:\s+[^<>]*?)?/?>|<!--[\s\S]*?-->")
_FOOTREF = re.compile(r"\[\^[^\]\s]+\]")
_EMOJI = re.compile(r"(?<![\w:]):[a-z0-9_+-]+:(?![\w:])")
_URL = re.compile(r"(?:https?://|www\.)[^\s<>]*[^\s<>.,;:!?)\]'\"]")
_ENTITY = re.compile(r"&(?:[A-Za-z][A-Za-z0-9]{1,31}|#\d{1,7}|#[xX][0-9A-Fa-f]{1,6});")
_HARDBREAK = re.compile(r"(?: {2,}|\\)\n")
_PROTECTED_RES = (_CODE, _DMATH, _MATH, _ESCAPE, _AUTOLINK, _HTML, _FOOTREF, _EMOJI, _URL, _ENTITY)


class _Enc:
    """Inline markdown source -> tagged text. Lossless by construction: every consumed piece is either escaped text,
    a verbatim protected literal, or a tag whose exact opening/closing source strings are stored in `book`."""

    def __init__(self, refs: set[str]):
        self.refs = refs
        self.book: dict[str, tuple[str, str]] = {}
        self.protected: list[str] = []
        self.n = 0

    def tag(self, letter: str, open_: str, close: str) -> str:
        self.n += 1
        name = f"{letter}{self.n}"
        self.book[name] = (open_, close)
        return name

    def prot(self, raw: str) -> str:
        e = esc(raw)
        if e not in self.protected:
            self.protected.append(e)
        return e

    # --- bracket helpers
    @staticmethod
    def _match_bracket(s: str, i: int) -> int:
        """Index of the `]` closing the `[` at s[i], or -1. Honours escapes, code spans and nesting."""
        depth, j = 0, i
        while j < len(s):
            c = s[j]
            if c == "\\":
                j += 2
                continue
            if c == "`":
                m = _CODE.match(s, j)
                if m:
                    j = m.end()
                    continue
            if c == "[":
                depth += 1
            elif c == "]":
                depth -= 1
                if depth == 0:
                    return j
            j += 1
        return -1

    @staticmethod
    def _match_paren(s: str, i: int) -> int:
        depth, j = 0, i
        while j < len(s):
            c = s[j]
            if c == "\\":
                j += 2
                continue
            if c == "\n" and s[j + 1:j + 2] == "\n":
                return -1
            if c == "(":
                depth += 1
            elif c == ")":
                depth -= 1
                if depth == 0:
                    return j
            j += 1
        return -1

    def _link(self, s: str, i: int, image: bool):
        lb = i + 1 if image else i
        rb = self._match_bracket(s, lb)
        if rb < 0:
            return None
        inner = s[lb + 1:rb]
        nxt = s[rb + 1:rb + 2]
        if nxt == "(":
            rp = self._match_paren(s, rb + 1)
            if rp < 0:
                return None
            suffix = s[rb:rp + 1]
            end = rp + 1
        elif nxt == "[":
            rb2 = s.find("]", rb + 1)
            if rb2 < 0:
                return None
            suffix = s[rb:rb2 + 1]
            end = rb2 + 1
        else:
            if normalizeReference(inner) not in self.refs:
                return None
            # shortcut reference: the visible text IS the label, so it must stay verbatim
            return self.prot(s[i:rb + 1]), rb + 1
        name = self.tag("i" if image else "l", s[i:lb + 1], suffix)
        return f"<{name}>{self.enc(inner)}</{name}>" + self._suffix_marker(name), end

    def _suffix_marker(self, name: str) -> str:
        # the destination lives in the book; nothing for the model to see
        return ""

    def _emphasis(self, s: str, i: int):
        for marker, letter in (("~~", "s"), ("**", "b"), ("__", "b"), ("*", "e"), ("_", "e")):
            if not s.startswith(marker, i):
                continue
            k = i + len(marker)
            if k >= len(s) or s[k].isspace():
                return None
            if marker[0] == "_" and i > 0 and s[i - 1].isalnum():
                return None
            j = k
            while j < len(s):
                c = s[j]
                if c == "\\":
                    j += 2
                    continue
                if c == "`":
                    m = _CODE.match(s, j)
                    if m:
                        j = m.end()
                        continue
                if s.startswith(marker, j) and not s[j - 1].isspace() and j > k:
                    after = s[j + len(marker):j + len(marker) + 1]
                    if len(marker) == 1 and after == marker:      # part of a ** run: skip it whole
                        j += 2
                        continue
                    if marker[0] == "_" and after.isalnum():
                        j += len(marker)
                        continue
                    name = self.tag(letter, marker, marker)
                    return f"<{name}>{self.enc(s[k:j])}</{name}>", j + len(marker)
                j += 1
            return None
        return None

    def enc(self, s: str) -> str:
        out: list[str] = []
        plain: list[str] = []
        i = 0

        def flush():
            if plain:
                out.append(esc("".join(plain)))
                plain.clear()

        while i < len(s):
            c = s[i]
            m = _HARDBREAK.match(s, i) if c in " \\" else None
            if m:
                flush()
                name = self.tag("x", m.group(0), "")
                out.append(f"<{name}/>")
                i = m.end()
                continue
            hit = None
            if c in "`$\\<[:&hw":
                for rx in _PROTECTED_RES:
                    if c == "[" and rx is not _FOOTREF:
                        continue
                    mm = rx.match(s, i)
                    if mm and mm.end() > i:
                        hit = (self.prot(mm.group(0)), mm.end())
                        break
            if hit is None and c == "!" and s.startswith("![", i):
                hit = self._link(s, i, True)
            elif hit is None and c == "[":
                hit = self._link(s, i, False)
            if hit is None and c in "*_~":
                hit = self._emphasis(s, i)
            if hit is None and c == "h" or hit is None and c == "w":
                mm = _URL.match(s, i) if (i == 0 or not s[i - 1].isalnum()) else None
                if mm:
                    hit = (self.prot(mm.group(0)), mm.end())
            if hit:
                flush()
                out.append(hit[0])
                i = hit[1]
                continue
            plain.append(c)
            i += 1
        flush()
        return "".join(out)


def _decode(text: str, book: dict[str, tuple[str, str]]) -> str:
    def render(node) -> str:
        parts = []
        for ch in node.children:
            if ch.kind == "text":
                parts.append(ch.text)
            elif ch.kind == "self":
                parts.append(book[ch.name][0])
            else:
                o, c = book[ch.name]
                parts.append(o + render(ch) + c)
        return "".join(parts)
    return render(parse(text))


def _has_letters(text: str, protected: list[str]) -> bool:
    t = strip_tags(text)
    for p in protected:
        t = t.replace(p, " ")
    return any(ch.isalpha() for ch in t)


def _lines(text: str) -> list[tuple[int, int]]:
    """(start, end-without-eol) of every line, same line numbering as markdown-it."""
    out = []
    for m in _LINE.finditer(text):
        if m.start() == len(text) and out:
            break
        s = m.group(0)
        out.append((m.start(), m.start() + len(s.rstrip("\r\n"))))
    return out


class MdAdapter:
    name = "md"
    extensions = (".md", ".markdown")

    def __init__(self):
        self.parser = MarkdownIt("commonmark").enable("table").enable("strikethrough")

    # ------------------------------------------------------------------ extract
    def _read(self, path: Path) -> str:
        try:
            return Path(path).read_bytes().decode("utf-8")
        except UnicodeDecodeError:
            raise FormatError("BAD_FORMAT", "markdown must be UTF-8")

    def _segments(self, text: str) -> list[Segment]:
        segs: list[Segment] = []
        lines = _lines(text)
        fm = _FM.match(text)
        fm_end_line = 0
        if fm:
            fm_end_line = text[:fm.end()].count("\n") if text[:fm.end()].endswith("\n") else len(lines)
            if fm.group(1) == "---":
                self._front_matter(text, fm, segs)
        env: dict = {}
        tokens = self.parser.parse(text, env)
        refs = set(env.get("references", {}))
        heading = ""
        stack: list[str] = []
        cursor: dict[int, int] = {}
        covered: list[tuple[int, int]] = []
        fence_lines: set[int] = set()
        for k, t in enumerate(tokens):
            if t.type in ("fence", "code_block", "html_block") and t.map:
                fence_lines.update(range(t.map[0], t.map[1]))
            if t.nesting == 1:
                stack.append(t.type)
            elif t.nesting == -1 and stack:
                stack.pop()
            if t.type != "inline" or not t.map or t.map[0] < fm_end_line:
                continue
            if "⟦" in t.content or "⟧" in t.content:
                continue
            kind = self._kind(stack)
            seg = self._inline_segment(text, lines, t, refs, cursor, kind)
            if seg is None:
                continue
            covered.append((t.map[0], t.map[1]))
            if kind == "heading":
                heading = strip_tags(seg.text)
            seg.context = heading if kind != "heading" else ""
            segs.append(seg)
        self._footnotes(text, lines, segs, fence_lines, covered, fm_end_line)
        segs.sort(key=lambda s: s.meta["start"])
        for i, s in enumerate(segs, 1):
            s.id = f"S{i:04d}"
        return segs

    @staticmethod
    def _kind(stack: list[str]) -> str:
        if "heading_open" in stack:
            return "heading"
        if "td_open" in stack or "th_open" in stack:
            return "table_cell"
        if "list_item_open" in stack:
            return "list_item"
        if "blockquote_open" in stack:
            return "quote"
        return "paragraph"

    def _front_matter(self, text: str, fm, segs: list[Segment]) -> None:
        base = fm.start(2)
        pos = 0
        for line in fm.group(2).split("\n"):
            m = _FM_LINE.match(line.rstrip("\r"))
            if m and m.group("v") and (m.group("q") == "" or m.group("q") not in m.group("v")) \
                    and "\\" not in m.group("v") and "⟦" not in m.group("v"):
                v = m.group("v")
                if not (m.group("q") == "" and (v[0] in "&*!|>%@`{[#" or ": " in v or " #" in v)) \
                        and any(c.isalpha() for c in v):
                    start = base + pos + m.start("v")
                    segs.append(Segment(id="", text=esc(v), kind="meta", context=f"front matter: {m.group('k')}",
                                        meta={"start": start, "end": start + len(v), "mode": "fm", "q": m.group("q"),
                                              "book": {}, "prefixes": [""], "eol": "\n"}))
            pos += len(line) + 1

    def _inline_segment(self, text, lines, t, refs, cursor, kind) -> Segment | None:
        s0, e0 = t.map
        clines = t.content.split("\n")
        if len(clines) != e0 - s0 or e0 > len(lines) or not t.content.strip():
            return None
        first = lines[s0]
        ltext = text[first[0]:first[1]]
        if len(clines) == 1:
            p0 = ltext.find(clines[0], cursor.get(s0, 0))
            if p0 < 0 and "|" in clines[0]:      # table cells: the parser unescapes `\|`
                alt = clines[0].replace("|", "\\|")
                p0 = ltext.find(alt, cursor.get(s0, 0))
                if p0 >= 0:
                    clines = [alt]
        else:
            p0 = ltext.find(clines[0])
        if p0 < 0 or not clines[0]:
            return None
        prefixes: list[str] = []
        if len(clines) == 1:
            start, end = first[0] + p0, first[0] + p0 + len(clines[0])
            cursor[s0] = p0 + len(clines[0])
        else:
            for i in range(1, len(clines)):
                ls, le = lines[s0 + i]
                lt = text[ls:le]
                cl = clines[i]
                if i < len(clines) - 1 or lt.endswith(cl):
                    if not lt.endswith(cl):
                        return None
                    pref = lt[:len(lt) - len(cl)]
                else:
                    q = lt.find(cl)
                    if q < 0:
                        return None
                    pref = lt[:q]
                prefixes.append(pref)
            start = first[0] + p0
            ls, le = lines[e0 - 1]
            end = ls + len(prefixes[-1]) + len(clines[-1])
            sl = _EOL.split(text[start:end])
            if len(sl) != len(clines) or sl[0] != clines[0] \
                    or any(sl[i][len(prefixes[i - 1]):] != clines[i] or not sl[i].startswith(prefixes[i - 1])
                           for i in range(1, len(sl))):
                return None
        raw = "\n".join(clines)
        if raw.lstrip().startswith("$$") and raw.rstrip().endswith("$$"):
            return None                       # display math block
        enc = _Enc(refs)
        lead = _LEAD.match(raw) if kind != "table_cell" else None
        head = ""
        if lead:
            head = enc.prot(lead.group(0))
            body = enc.enc(raw[lead.end():])
            tagged = head + body
        else:
            tagged = enc.enc(raw)
        if not _has_letters(tagged, enc.protected):
            return None
        eol_m = _EOL.search(text[start:end])
        return Segment(id="", text=tagged, kind=kind, protected=enc.protected,
                       meta={"start": start, "end": end, "mode": "inline", "book": enc.book,
                             "prefixes": prefixes or [""], "eol": eol_m.group(0) if eol_m else "\n",
                             "lead": lead.group(0) if lead else ""})

    def _footnotes(self, text, lines, segs, fence_lines, covered, fm_end_line) -> None:
        """`[^1]: text` lines are consumed by the parser as reference definitions, so they are found by line."""
        for n, (ls, le) in enumerate(lines):
            if n in fence_lines or n < fm_end_line or any(a <= n < b for a, b in covered):
                continue
            m = _FOOTDEF.match(text[ls:le])
            if not m or "⟦" in m.group("body"):
                continue
            enc = _Enc(set())
            tagged = enc.enc(m.group("body"))
            if not _has_letters(tagged, enc.protected):
                continue
            start = ls + m.start("body")
            segs.append(Segment(id="", text=tagged, kind="note", protected=enc.protected,
                                meta={"start": start, "end": ls + m.end("body"), "mode": "inline", "book": enc.book,
                                      "prefixes": [""], "eol": "\n", "lead": ""}))

    def extract(self, path: Path) -> list[Segment]:
        return self._segments(self._read(path))

    # ------------------------------------------------------------------ rebuild
    def rebuild(self, path: Path, translations: Mapping[str, str], out: Path, *, force: bool = False) -> RebuildReport:
        text = self._read(path)
        segs = self._segments(text)
        rep = RebuildReport()
        edits: list[tuple[int, int, str]] = []
        for s in segs:
            tr = translations.get(s.id)
            if tr is None or (tr == s.text and not force):
                rep.segments_untouched += 1
                continue
            if not tags_match(s.text, tr):
                rep.segments_untouched += 1
                rep.needs_review[s.id] = "FMT001: inline tags changed; source kept"
                continue
            m = s.meta
            body = _decode(tr, m["book"])
            if m["mode"] == "fm":
                body = self._fm_value(body.replace("\n", " "), m["q"])
            else:
                parts = body.split("\n")
                pref = m["prefixes"]
                body = parts[0] + "".join(m["eol"] + pref[min(i, len(pref)) - 1 if len(pref) else 0] + p
                                          for i, p in enumerate(parts[1:], 1)) if len(parts) > 1 else parts[0]
            edits.append((m["start"], m["end"], body))
            rep.segments_applied += 1
        out_text = text
        for a, b, body in sorted(edits, reverse=True):
            out_text = out_text[:a] + body + out_text[b:]
        Path(out).write_bytes(out_text.encode("utf-8"))
        return rep

    @staticmethod
    def _fm_value(v: str, q: str) -> str:
        if q == "'":
            return v.replace("'", "''")
        if q == '"':
            return v.replace("\\", "\\\\").replace('"', '\\"')
        if v[:1] in "&*!|>%@`{[#'\"-?:" or ": " in v or " #" in v or v != v.strip():
            return '"' + v.replace("\\", "\\\\").replace('"', '\\"') + '"'
        return v
