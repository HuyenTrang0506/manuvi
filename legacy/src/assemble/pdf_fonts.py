"""Font Unicode cho PDF (tiếng Việt)."""

from __future__ import annotations

from pathlib import Path
from typing import Optional, Tuple

import fitz

# macOS / Linux paths — thử lần lượt
FONT_CANDIDATES = [
    Path("/System/Library/Fonts/Supplemental/Arial Unicode.ttf"),
    Path("/Library/Fonts/Arial Unicode.ttf"),
    Path("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"),
    Path("/usr/share/fonts/TTF/DejaVuSans.ttf"),
]


def resolve_unicode_font() -> Tuple[Path, str]:
    for p in FONT_CANDIDATES:
        if p.exists():
            return p, "viuni"
    raise FileNotFoundError(
        "Không tìm thấy font Unicode (Arial Unicode / DejaVu). "
        "Cài Arial Unicode hoặc DejaVu Sans."
    )


class UnicodePdfFonts:
    """Đăng ký font TTF một lần cho cả document."""

    def __init__(self, doc: fitz.Document):
        path, self.name = resolve_unicode_font()
        self.path = path
        # embed cho mọi page hiện tại và tương lai
        for page in doc:
            page.insert_font(fontname=self.name, fontfile=str(path))
        self._doc = doc

    def ensure_page(self, page: fitz.Page) -> None:
        """Đảm bảo page có font (page mới tạo)."""
        try:
            page.insert_font(fontname=self.name, fontfile=str(self.path))
        except Exception:
            pass  # đã có

    @property
    def fontname(self) -> str:
        return self.name
