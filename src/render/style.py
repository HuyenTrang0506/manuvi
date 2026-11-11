"""Original text style of one block, read from the source PDF, used to redraw the Vietnamese text faithfully."""
from __future__ import annotations

from dataclasses import dataclass, field

Rect4 = tuple[float, float, float, float]


@dataclass
class TextStyle:
    font: str = ""                 # original PDF font name as printed (subset prefix removed)
    size: float = 10.0             # dominant original size (pt)
    color: tuple[float, float, float] = (0.0, 0.0, 0.0)
    bold: bool = False
    italic: bool = False
    align: str = "left"            # left | center | right (in the reading frame)
    pitch: float = 0.0             # original distance between baselines (pt); 0 = unknown
    lines: int = 1                 # original line count
    base_off: float = 0.0          # reading-frame distance from the box edge to the first baseline; 0 = unknown
    spans: list[Rect4] = field(default_factory=list)  # page-space rectangles of the original spans (redaction)


def int_to_rgb(c: int) -> tuple[float, float, float]:
    return ((c >> 16) & 255) / 255.0, ((c >> 8) & 255) / 255.0, (c & 255) / 255.0
