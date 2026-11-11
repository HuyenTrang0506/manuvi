import re
from pathlib import Path

import pytest

from src.formats import get_adapter
from src.formats.md import MdAdapter
from src.formats.tags import tag_names

CORPUS = {
    "tricky": '''---
title: "Hello world"
description: Short text about the relay
slug: keep-this-key
tags: [a, b]
---

# Heading **bold** and `code`

Setext Title
============

Sub Title
---------

Some *emphasis*, **strong**, ~~gone~~ and a [link](http://x.y/a_b "t") plus ![alt text](img.png "cap").
Second line with $x^2$ math and :smile: and https://example.com/z?q=1.
Hard  
break and\\
backslash break. Escaped \\*star\\* and &amp; entity and snake_case_word.

> quote line one
continued lazy line
> > nested quote here

- item one
  continued
- [ ] task item
    1. nested ordered item
* other marker item

1. first
2. second

| Name | Value |
|:-----|------:|
| `x` | plain cell text |
| [l](u) | **b** cell |

Term
: not definition list

[^1]: A footnote text here.

[ref]: http://example.com "Title"
[Other Ref]: <http://example.org>

Use [ref] and [other][ref] and [Other Ref][] and a footnote[^1]. Visit <https://auto.example/x> or <b>raw html</b>.

```python
print("hello")   # not translated
```

~~~
tilde fence
~~~

    indented code block

<div>
html *not touched*
</div>

<!-- comment -->

***

$$
a = b
$$

Price is $5 and $6 today, not math.
''',
    "crlf_no_trailing_newline": "# Title\r\n\r\nPara one\r\nline two  \r\nhard\r\n\r\n- a\r\n- b\r\n\r\nlast line no newline",
    "empty_and_symbols": "\n\n***\n\n---\n\n12345\n\n<br>\n",
    "only_front_matter": "---\ntitle: Just a title\n---\n",
    "trailing_spaces_and_tabs": "Line with trailing spaces   \n\n\tTabbed code\n\n-\tTab list item\n\n#Not a heading\n\n#   Spaced heading   ##\n",
    "unicode": "# Tiêu đề đã dịch\n\nĐoạn văn tiếng Việt và English mix, emoji 🙂 and 中文.\n",
    "placeholder_brackets": "Text with ⟦P001⟧ already inside.\n\nNormal paragraph here.\n",
    "links_in_lists": "- [One](a)\n- ![Two](b)\n  - nested [Three](c) text\n\n> **Note**\n> Quoted *text* here\n",
}


def write(tmp_path, name, content) -> Path:
    p = tmp_path / f"{name}.md"
    p.write_bytes(content.encode("utf-8"))
    return p


@pytest.mark.parametrize("name", list(CORPUS))
def test_identity_round_trip_is_byte_identical(tmp_path, name):
    p = write(tmp_path, name, CORPUS[name])
    a = MdAdapter()
    segs = a.extract(p)
    tr = {s.id: s.text for s in segs}
    out = tmp_path / "out.md"
    a.rebuild(p, tr, out)                      # untouched fast path
    assert out.read_bytes() == p.read_bytes()
    a.rebuild(p, tr, out, force=True)          # every segment really re-rendered from its tagged text
    assert out.read_bytes() == p.read_bytes()
    a.rebuild(p, {}, out)
    assert out.read_bytes() == p.read_bytes()


def test_segments_are_stable_and_ordered(tmp_path):
    p = write(tmp_path, "t", CORPUS["tricky"])
    a = MdAdapter()
    s1, s2 = a.extract(p), a.extract(p)
    assert [(s.id, s.text) for s in s1] == [(s.id, s.text) for s in s2]
    assert [s.id for s in s1] == [f"S{i:04d}" for i in range(1, len(s1) + 1)]
    kinds = {s.kind for s in s1}
    assert {"heading", "paragraph", "list_item", "table_cell", "quote", "meta"} <= kinds


def vi(text: str) -> str:
    """Fake translator: changes only English words, keeps tags and protected spans (they are skipped by regex)."""
    return re.sub(r"\b(Heading|bold|and|Some|emphasis|strong|link|first|second|item|one|Title|Name|Value|plain|cell|text)\b",
                  lambda m: "Việt_" + m.group(1), text)


def test_translation_changes_only_translatable_text(tmp_path):
    src = CORPUS["tricky"]
    p = write(tmp_path, "t", src)
    a = MdAdapter()
    segs = a.extract(p)
    out = tmp_path / "o.md"
    rep = a.rebuild(p, {s.id: vi(s.text) for s in segs}, out)
    res = out.read_text("utf-8")
    assert rep.segments_applied > 5 and "Việt_" in res
    for must in ['slug: keep-this-key', "tags: [a, b]", "print(\"hello\")   # not translated", "~~~\ntilde fence\n~~~",
                 "    indented code block", "<div>\nhtml *not touched*\n</div>", "<!-- comment -->",
                 "[ref]: http://example.com \"Title\"", "[Other Ref]: <http://example.org>", "|:-----|------:|",
                 "$$\na = b\n$$", "http://x.y/a_b \"t\"", "(img.png \"cap\")", "$x^2$", ":smile:",
                 "https://example.com/z?q=1", "<https://auto.example/x>", "<b>raw html</b>", "[^1]", "`code`",
                 "\\*star\\*", "&amp;", "snake_case_word", "Hard  \nbreak", "Price is $5 Việt_and $6 today", "***"]:
        assert must in res, must
    assert res.startswith("---\ntitle: \"") and "\nslug:" in res
    # heading levels, list markers and numbering unchanged
    heads = lambda t: re.findall(r"^(#+|=+|-{3,}) ", t, re.M)
    assert heads(res) == heads(src)
    assert re.findall(r"^\s*(?:[-*]|\d+\.) ", res, re.M) == re.findall(r"^\s*(?:[-*]|\d+\.) ", src, re.M)
    assert res.endswith("not math.\n")
    # emphasis / link / image markup survives around the translated words
    assert "**Việt_strong**" in res or "**strong**" in res
    assert "[Việt_link](http://x.y/a_b \"t\")" in res
    assert "![alt Việt_text](img.png \"cap\")" in res
    assert "| `x` | Việt_plain Việt_cell Việt_text |" in res


def test_reordered_tags_keep_markup(tmp_path):
    p = write(tmp_path, "t", "Press **the red button** and read [the manual](http://m.example/x).\n")
    a = MdAdapter()
    (s,) = a.extract(p)
    assert tag_names(s.text) == ["b1", "/b1", "l2", "/l2"] or len(tag_names(s.text)) == 4
    names = re.findall(r"<(\w+\d)>", s.text)
    tr = f"Đọc <{names[1]}>sách hướng dẫn</{names[1]}> và nhấn <{names[0]}>nút đỏ</{names[0]}>."
    out = tmp_path / "o.md"
    a.rebuild(p, {s.id: tr}, out)
    assert out.read_text("utf-8") == "Đọc [sách hướng dẫn](http://m.example/x) và nhấn **nút đỏ**.\n"


def test_broken_tags_keep_source_and_flag(tmp_path):
    p = write(tmp_path, "t", "Press **the red button** now.\n")
    a = MdAdapter()
    (s,) = a.extract(p)
    out = tmp_path / "o.md"
    rep = a.rebuild(p, {s.id: "Nhấn nút đỏ ngay."}, out)
    assert out.read_bytes() == p.read_bytes() and s.id in rep.needs_review


def test_multiline_paragraph_keeps_container_prefix(tmp_path):
    src = "> first line here\n> second line here\n\n- item first\n  item second\n"
    p = write(tmp_path, "t", src)
    a = MdAdapter()
    segs = a.extract(p)
    out = tmp_path / "o.md"
    a.rebuild(p, {segs[0].id: "dòng một\ndòng hai", segs[1].id: "mục một\nmục hai"}, out)
    assert out.read_text("utf-8") == "> dòng một\n> dòng hai\n\n- mục một\n  mục hai\n"


def test_front_matter_quote_styles(tmp_path):
    p = write(tmp_path, "t", "---\ntitle: Plain title\ndescription: 'Quoted one'\n---\nBody text here.\n")
    a = MdAdapter()
    segs = a.extract(p)
    out = tmp_path / "o.md"
    a.rebuild(p, {segs[0].id: "Tiêu đề: có dấu hai chấm", segs[1].id: "Nó's đây"}, out)
    res = out.read_text("utf-8")
    assert 'title: "Tiêu đề: có dấu hai chấm"' in res and "description: 'Nó''s đây'" in res


def test_protected_spans_listed_and_text_escaped(tmp_path):
    p = write(tmp_path, "t", "Use `a < b` and <kbd>Ctrl</kbd> with R&D &lt; done.\n")
    a = MdAdapter()
    (s,) = a.extract(p)
    assert "`a &lt; b`" in s.protected and "&lt;kbd&gt;" in s.protected
    assert "<" not in re.sub(r"<[a-z]+\d+/?>|</[a-z]+\d+>", "", s.text)
    out = tmp_path / "o.md"
    a.rebuild(p, {s.id: s.text}, out, force=True)
    assert out.read_bytes() == p.read_bytes()


def test_registry():
    assert isinstance(get_adapter("md"), MdAdapter)
