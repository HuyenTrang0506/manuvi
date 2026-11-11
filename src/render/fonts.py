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
    Path("/System/Library/Fonts/Supplemental/Arial Unicode.ttf"),  # macOS
    Path("/Library/Fonts/Arial Unicode.ttf"),
    Path("/System/Library/Fonts/Supplemental/Arial.ttf"),
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


# ---- per-original-font choice (metric-compatible, Vietnamese-capable) ---------------------------------------------
import re as _re
from dataclasses import dataclass as _dc

_SANS_DIRS = [
    Path("/usr/share/fonts/truetype/liberation"), Path("/usr/share/fonts/truetype/liberation2"),
    Path("/usr/share/fonts/liberation"), Path("/usr/share/fonts/truetype/arimo"),
    Path("/usr/share/fonts/truetype/google-fonts"),
]
_MAC_ARIAL = Path("/System/Library/Fonts/Supplemental")
_FAMILIES = {  # family -> (regular, bold, italic, bolditalic) candidates, first existing wins
    "sans": [("Arial.ttf", "Arial Bold.ttf", "Arial Italic.ttf", "Arial Bold Italic.ttf"),
             ("LiberationSans-Regular.ttf", "LiberationSans-Bold.ttf", "LiberationSans-Italic.ttf",
              "LiberationSans-BoldItalic.ttf"),
             ("Arimo-Regular.ttf", "Arimo-Bold.ttf", "Arimo-Italic.ttf", "Arimo-BoldItalic.ttf")],
    "serif": [("Times New Roman.ttf", "Times New Roman Bold.ttf", "Times New Roman Italic.ttf",
               "Times New Roman Bold Italic.ttf"),
              ("LiberationSerif-Regular.ttf", "LiberationSerif-Bold.ttf", "LiberationSerif-Italic.ttf",
               "LiberationSerif-BoldItalic.ttf")],
    "mono": [("Courier New.ttf", "Courier New Bold.ttf", "Courier New Italic.ttf", "Courier New Bold Italic.ttf"),
             ("LiberationMono-Regular.ttf", "LiberationMono-Bold.ttf", "LiberationMono-Italic.ttf",
              "LiberationMono-BoldItalic.ttf")],
}
_CJK = [Path("/System/Library/Fonts/Supplemental/Arial Unicode.ttf"),
        Path("/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc"),
        Path("/usr/share/fonts/opentype/noto/NotoSerifCJK-Regular.ttc"),
        Path("/usr/share/fonts/truetype/fonts-japanese-gothic.ttf")]
_CJK_RX = _re.compile(r"[　-ヿ㐀-䶿一-鿿＀-￯]")


def has_cjk(text: str) -> bool:
    return bool(_CJK_RX.search(text))


def classify_font(name: str, flags: int = 0) -> tuple[str, bool, bool]:
    """(family, bold, italic) of an original PDF font name. Subset prefixes (ABCDEF+) are ignored.

    family: sans (Arial, Helvetica, MS Gothic text, Symbol, unknown) | serif | mono.
    """
    n = _re.sub(r"^[A-Z]{6}\+", "", name or "").lower().replace(" ", "").replace("-", "").replace(",", "")
    bold = "bold" in n or "black" in n or "heavy" in n or "demi" in n or bool(flags & 16)
    italic = "italic" in n or "oblique" in n or bool(flags & 2)
    if any(k in n for k in ("times", "serif", "georgia", "garamond", "minion", "palatino")) and "sans" not in n:
        fam = "serif"
    elif any(k in n for k in ("courier", "consolas", "monaco", "lucidaconsole", "mono")) and "gothic" not in n:
        fam = "mono"
    else:
        fam = "sans"
    return fam, bold, italic


@_dc(frozen=True)
class Face:
    key: str          # unique PDF resource name, e.g. "viSansBI"
    path: str
    family: str
    bold: bool
    italic: bool

    @property
    def label(self) -> str:
        return Path(self.path).stem


class FontSet:
    """Resolves the Vietnamese-capable face for an original font. `explicit` (one .ttf) forces one face for all."""

    def __init__(self, explicit: str | None = None):
        self.explicit = str(explicit) if explicit else None
        self._cache: dict[tuple, Face | None] = {}
        self._fonts: dict[str, "object"] = {}

    def _find(self, family: str, bold: bool, italic: bool) -> str | None:
        idx = (1 if bold else 0) + (2 if italic else 0)
        idx = {0: 0, 1: 1, 2: 2, 3: 3}[idx]
        for names in _FAMILIES[family]:
            for d in [_MAC_ARIAL] + _SANS_DIRS + [Path(os.environ.get("MANUVI_FONT_DIR", "/nonexistent"))]:
                p = d / names[idx]
                if p.is_file() and covers_vietnamese(p):
                    return str(p)
        return None

    def face(self, name: str = "", flags: int = 0, text: str = "") -> Face:
        fam, bold, italic = classify_font(name, flags)
        if self.explicit:
            return Face("viR", self.explicit, "sans", False, False)
        if has_cjk(text):
            for p in _CJK:
                if p.is_file():
                    return Face("viCJK", str(p), "cjk", bold, italic)
        key = (fam, bold, italic)
        if key not in self._cache:
            path = self._find(fam, bold, italic) or self._find(fam, False, False) or self._find("sans", bold, italic)
            if path is None:
                f = find_vi_font()
                path = str(f) if f else None
            self._cache[key] = Face(f"vi{fam.title()}{'B' if bold else ''}{'I' if italic else ''}", path,
                                    fam, bold, italic) if path else None
        f = self._cache[key]
        if f is None:
            raise RuntimeError("no Vietnamese font found (set MANUVI_VI_FONT)")
        return f

    def font(self, face: Face):
        import pymupdf

        if face.key not in self._fonts:
            self._fonts[face.key] = pymupdf.Font(fontfile=face.path)
        return self._fonts[face.key]
