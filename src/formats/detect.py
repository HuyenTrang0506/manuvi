"""Format detection and hostile-input limits. Nothing here executes or extracts anything to disk.

Format = file extension AND magic bytes must agree. Macro-enabled files (.docm/.pptm, vbaProject.bin, macroEnabled
content types) are rejected outright: Manuvi never preserves or runs VBA. Zip containers are checked for entry count,
total uncompressed size and per-entry compression ratio before any part is parsed.
"""
from __future__ import annotations

import os
import zipfile
from pathlib import Path
from typing import Mapping, Optional

from .base import FormatError

ZIP_MAGIC = b"PK\x03\x04"
OLE_MAGIC = b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1"
EXT_FORMAT = {".md": "md", ".markdown": "md", ".docx": "docx", ".pptx": "pptx"}
MACRO_EXT = {".docm", ".dotm", ".pptm", ".potm", ".ppsm", ".xlsm", ".xltm", ".xlam", ".doc", ".ppt"}
REQUIRED_PART = {"docx": "word/document.xml", "pptx": "ppt/presentation.xml"}
MAIN_CT = {"docx": "wordprocessingml.document.main+xml", "pptx": "presentationml.presentation.main+xml"}

DEFAULTS = {"MANUVI_MAX_FILE_MB": 50.0, "MANUVI_MAX_MD_MB": 5.0, "MANUVI_MAX_UNZIPPED_MB": 200.0,
            "MANUVI_MAX_ZIP_ENTRIES": 5000.0, "MANUVI_MAX_ZIP_RATIO": 150.0}


def limit(name: str, env: Optional[Mapping[str, str]] = None) -> float:
    env = os.environ if env is None else env
    try:
        v = float(env.get(name, ""))
        return v if v > 0 else DEFAULTS[name]
    except ValueError:
        return DEFAULTS[name]


def detect_format(path: Path | str, env: Optional[Mapping[str, str]] = None) -> str:
    """Return 'md' | 'docx' | 'pptx' or raise FormatError. PDFs are handled by the existing intake, not here."""
    path = Path(path)
    ext = path.suffix.lower()
    if ext in MACRO_EXT:
        raise FormatError("MACRO_REJECTED", f"{ext} files are not accepted (macros / legacy binary format)")
    fmt = EXT_FORMAT.get(ext)
    if fmt is None:
        raise FormatError("UNSUPPORTED_FORMAT", f"unsupported extension {ext!r}")
    size = path.stat().st_size
    if size > limit("MANUVI_MAX_FILE_MB", env) * 1024 * 1024:
        raise FormatError("FILE_TOO_LARGE", "file is over the size limit")
    with open(path, "rb") as f:
        head = f.read(8)
    if fmt == "md":
        if size > limit("MANUVI_MAX_MD_MB", env) * 1024 * 1024:
            raise FormatError("FILE_TOO_LARGE", "markdown file is over the size limit")
        if head.startswith(ZIP_MAGIC) or head.startswith(OLE_MAGIC) or head.startswith(b"%PDF"):
            raise FormatError("BAD_FORMAT", "binary content in a .md file")
        data = path.read_bytes()
        if b"\x00" in data:
            raise FormatError("BAD_FORMAT", "NUL bytes in a .md file")
        try:
            data.decode("utf-8")
        except UnicodeDecodeError:
            raise FormatError("BAD_FORMAT", "markdown must be UTF-8")
        return fmt
    if head.startswith(OLE_MAGIC):
        raise FormatError("MACRO_REJECTED", "legacy or encrypted Office file")
    if not head.startswith(ZIP_MAGIC):
        raise FormatError("BAD_FORMAT", f"{ext} file is not an Office Open XML package")
    check_zip(path, fmt, env)
    return fmt


def check_zip(path: Path, fmt: str, env: Optional[Mapping[str, str]] = None) -> None:
    try:
        zf = zipfile.ZipFile(path)
    except zipfile.BadZipFile:
        raise FormatError("BAD_FORMAT", "corrupt zip container")
    with zf:
        infos = zf.infolist()
        if len(infos) > limit("MANUVI_MAX_ZIP_ENTRIES", env):
            raise FormatError("ZIP_BOMB", "too many entries")
        total = 0
        max_ratio = limit("MANUVI_MAX_ZIP_RATIO", env)
        names = set()
        for i in infos:
            n = i.filename
            if n.startswith("/") or ".." in n.split("/") or "\\" in n:
                raise FormatError("BAD_FORMAT", "unsafe path in container")
            if i.flag_bits & 0x1:
                raise FormatError("BAD_FORMAT", "encrypted container")
            names.add(n)
            total += i.file_size
            if i.compress_size and i.file_size / i.compress_size > max_ratio and i.file_size > 1024 * 1024:
                raise FormatError("ZIP_BOMB", "suspicious compression ratio")
        if total > limit("MANUVI_MAX_UNZIPPED_MB", env) * 1024 * 1024:
            raise FormatError("ZIP_BOMB", "uncompressed size over the limit")
        if any(n.lower().endswith(("vbaproject.bin", "vbadata.xml")) or "/macros/" in n.lower() for n in names):
            raise FormatError("MACRO_REJECTED", "VBA project found")
        if "[Content_Types].xml" not in names or REQUIRED_PART[fmt] not in names:
            raise FormatError("BAD_FORMAT", f"not a valid .{fmt} package")
        ct = zf.read("[Content_Types].xml")
        if len(ct) > 2_000_000:
            raise FormatError("ZIP_BOMB", "content types too large")
        if b"macroEnabled" in ct or b"vbaProject" in ct:
            raise FormatError("MACRO_REJECTED", "macro-enabled content type")
        if MAIN_CT[fmt].encode() not in ct:
            raise FormatError("BAD_FORMAT", f"main part is not a {fmt} document")
