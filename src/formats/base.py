"""Adapter interface shared by every document format.

An adapter turns a file into a flat list of `Segment`s and, given translations keyed by segment id, writes the same
format back. Both directions are deterministic and stateless: `rebuild` re-runs `extract` on the same file, so ids
are stable and nothing has to be stored between the two calls.

Segment text conventions (see tags.py): inline formatting is carried as numbered tags (`<b1>..</b1>`, `<x1/>`),
and anything that must never change (code spans, URLs, LaTeX ...) is left in the text verbatim and listed in
`Segment.protected`; the guard masks those spans with placeholders exactly like part numbers in PDFs.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Mapping, Protocol

# Pipeline kinds (src/pipeline/schema.Kind) a format kind maps to.
PIPELINE_KIND = {"heading": "label", "list_item": "step", "table_cell": "table", "paragraph": "prose",
                 "quote": "prose", "alt": "label", "meta": "label", "note": "prose", "shape": "prose",
                 "title": "label", "caption": "prose"}


class FormatError(Exception):
    """Untrusted file rejected or unreadable. `code` is a short job error code (never document text)."""

    def __init__(self, code: str, message: str = ""):
        super().__init__(message or code)
        self.code = code


@dataclass
class Segment:
    id: str                      # stable across runs: "S0001", ...
    text: str                    # escaped text with inline tags (what the model sees)
    kind: str                    # heading | list_item | table_cell | paragraph | quote | alt | meta | note | shape
    protected: list[str] = field(default_factory=list)   # literal strings (as they appear in `text`) to mask
    context: str = ""            # short hint for the translator (nearest heading, slide title); never translated
    page: int = 1                # slide number; 1 for flowing documents
    section: str = ""            # part / location label, e.g. "slide3:notes", "footnotes"
    meta: dict[str, Any] = field(default_factory=dict)   # adapter private, not serialised

    @property
    def pipeline_kind(self) -> str:
        return PIPELINE_KIND.get(self.kind, "prose")


@dataclass
class RebuildReport:
    segments_applied: int = 0
    segments_untouched: int = 0
    needs_review: dict[str, str] = field(default_factory=dict)   # segment id -> reason (e.g. text overflow)
    notes: list[str] = field(default_factory=list)


class Adapter(Protocol):
    name: str
    extensions: tuple[str, ...]

    def extract(self, path: Path) -> list[Segment]: ...

    def rebuild(self, path: Path, translations: Mapping[str, str], out: Path) -> RebuildReport: ...
