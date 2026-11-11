"""Inline tag layer shared by all adapters.

Segment text is plain text where
  * `&`, `<`, `>` are escaped only as far as needed to make tags unambiguous (`&lt;`, `&gt;`, `&amp;` when the
    source itself contains `&lt;`/`&gt;`/`&amp;`),
  * inline formatting is a tag pair `<b1>...</b1>` (bold, link, run ...) or a self-closing tag `<x1/>` (line break,
    opaque inline object).
The model may reorder tags (Vietnamese word order) but must keep every tag exactly once and nested properly;
`tag_issues` checks that deterministically.
"""
from __future__ import annotations

import re
from dataclasses import dataclass

TAG_RE = re.compile(r"<(/?)([a-z]{1,3}\d{1,4})(/?)>")
_ESC_RE = re.compile(r"&(lt|gt|amp);")
_UNESC = {"lt": "<", "gt": ">", "amp": "&"}


def esc(s: str) -> str:
    """Escape plain text. A bare `&` is left alone unless it would read as one of our entities."""
    s = re.sub(r"&(?=(?:lt|gt|amp);)", "&amp;", s)
    return s.replace("<", "&lt;").replace(">", "&gt;")


def unesc(s: str) -> str:
    return _ESC_RE.sub(lambda m: _UNESC[m.group(1)], s)


@dataclass
class Tok:
    kind: str    # text | open | close | self
    val: str     # text (still escaped) or tag name


def tokenize(text: str) -> list[Tok]:
    out: list[Tok] = []
    pos = 0
    for m in TAG_RE.finditer(text):
        if m.start() > pos:
            out.append(Tok("text", text[pos:m.start()]))
        closing, name, selfc = m.groups()
        out.append(Tok("close" if closing else "self" if selfc else "open", name))
        pos = m.end()
    if pos < len(text):
        out.append(Tok("text", text[pos:]))
    return out


def tag_names(text: str) -> list[str]:
    return [t.val if t.kind != "close" else "/" + t.val for t in tokenize(text) if t.kind != "text"]


def well_formed(text: str) -> bool:
    stack: list[str] = []
    for t in tokenize(text):
        if t.kind == "open":
            stack.append(t.val)
        elif t.kind == "close":
            if not stack or stack.pop() != t.val:
                return False
    return not stack


def tags_match(source: str, target: str) -> bool:
    return sorted(tag_names(source)) == sorted(tag_names(target)) and well_formed(target)


def strip_tags(text: str) -> str:
    return TAG_RE.sub("", text)


@dataclass
class Node:
    """Parse tree of a tagged text: `name` is None for the root and for text nodes."""
    name: str | None = None
    text: str | None = None          # for text nodes (unescaped)
    kind: str = "tag"                # tag | text | self
    children: list["Node"] = None    # type: ignore[assignment]

    def __post_init__(self):
        if self.children is None:
            self.children = []


def parse(text: str) -> Node:
    """Tagged text -> tree. Assumes `well_formed(text)`; callers fall back to strip_tags otherwise."""
    root = Node()
    stack = [root]
    for t in tokenize(text):
        if t.kind == "text":
            stack[-1].children.append(Node(text=unesc(t.val), kind="text"))
        elif t.kind == "open":
            n = Node(name=t.val)
            stack[-1].children.append(n)
            stack.append(n)
        elif t.kind == "close":
            stack.pop()
        else:
            stack[-1].children.append(Node(name=t.val, kind="self"))
    return root


def tag_issues(source: str, target: str):
    """FMT001 (error) when the target dropped, duplicated, invented or mis-nested inline tags."""
    from ..guard.models import Issue, Severity
    if tags_match(source, target):
        return []
    return [Issue(rule_id="FMT001", severity=Severity.error,
                  message=f"inline tags changed: source {tag_names(source)} target {tag_names(target)}",
                  fix_hint="Keep every tag like <b1>...</b1> and <x1/> exactly once, properly nested; "
                           "translate only the text between them.")]
