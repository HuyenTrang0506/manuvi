from __future__ import annotations

import re
from pathlib import Path
from typing import Optional

import fitz

from src.ingest.noise_filter import FIGURE_REF_PATTERN, strip_noise
from src.models import FigureEntry


def extract_page_images(
    page: fitz.Page,
    pdf_page: int,
    output_dir: Path,
    prefix: str,
    rel_prefix: str,
) -> list[tuple[str, str, list[float]]]:
    """Trích ảnh từ trang PDF. Returns (rel_path, abs_path, bbox)."""
    output_dir.mkdir(parents=True, exist_ok=True)
    results: list[tuple[str, str, list[float]]] = []
    blocks = page.get_text("dict")["blocks"]

    img_idx = 0
    for block in blocks:
        if block.get("type") != 1:
            continue
        bbox = block.get("bbox", [0, 0, 0, 0])
        try:
            pix = page.get_pixmap(clip=fitz.Rect(bbox), dpi=150)
            img_idx += 1
            filename = f"{prefix}_p{pdf_page}_img{img_idx}.png"
            abs_path = output_dir / filename
            pix.save(str(abs_path))
            rel_path = f"{rel_prefix}/{filename}"
            results.append((rel_path, str(abs_path), list(bbox)))
        except Exception:
            continue
    return results


def link_captions_to_figures(
    page_text: str,
    pdf_page: int,
    manual_ref: Optional[str],
    image_paths: list[str],
    registry: dict[str, FigureEntry],
) -> None:
    """Đăng ký figure từ caption trên trang."""
    captions = []
    for m in FIGURE_REF_PATTERN.finditer(page_text):
        fig_num = m.group(1)
        start = m.end()
        rest = page_text[start : start + 120]
        title = rest.split("\n")[0].strip(" :.-")
        captions.append((fig_num, title))

    for i, (fig_num, title) in enumerate(captions):
        fig_id = f"fig-{fig_num}"
        img_path = str(image_paths[i]) if i < len(image_paths) else None
        if fig_id in registry:
            entry = registry[fig_id]
            if pdf_page not in entry.pdf_pages:
                entry.pdf_pages.append(pdf_page)
            if manual_ref and manual_ref not in entry.manual_refs:
                entry.manual_refs.append(manual_ref)
            if img_path and img_path not in entry.image_paths:
                entry.image_paths.append(img_path)
            if title and not entry.caption_en:
                entry.caption_en = title
        else:
            registry[fig_id] = FigureEntry(
                figure_id=fig_id,
                figure_number=fig_num,
                caption_en=title,
                pdf_pages=[pdf_page],
                manual_refs=[manual_ref] if manual_ref else [],
                image_paths=[img_path] if img_path else [],
            )
