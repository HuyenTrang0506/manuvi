"""Locate a freely licensed font with Vietnamese coverage (Noto Sans preferred)."""
from __future__ import annotations

import os
from pathlib import Path

VI_SAMPLE = "ăâđêôơưĂÂĐÊÔƠƯáàảãạấầẩẫậắằẳẵặéèẻẽẹếềểễệíìỉĩịóòỏõọốồổỗộớờởỡợúùủũụứừửữựýỳỷỹỵ"

_REPO = Path(__file__).resolve().parents[2]
CANDIDATES = [
    _REPO / "workspace" / "fonts" / "NotoSans-Regular.ttf",
    Path("/usr/share/fonts/truetype/noto/NotoSans-Regular.ttf"),
    Path("/usr/share/fonts/truetype/dejavu/DejaVuSansCondensed.ttf"),
    Path("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"),
]


def covers_vietnamese(path: str | Path) -> bool:
    from fontTools.ttLib import TTFont

    cmap = TTFont(str(path), lazy=True).getBestCmap()
    return all(ord(ch) in cmap for ch in VI_SAMPLE)


def find_vi_font(explicit: str | None = None) -> Path | None:
    """Return the first existing candidate that covers Vietnamese, else None."""
    env = explicit or os.environ.get("MANUVI_VI_FONT")
    for p in ([Path(env)] if env else []) + CANDIDATES:
        if p.is_file() and covers_vietnamese(p):
            return p
    return None
