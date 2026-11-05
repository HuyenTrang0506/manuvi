"""Trích hình theo bbox / full-page cho trang sơ đồ xoay 90°."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Optional

import fitz

from src.ingest.noise_filter import strip_noise
from src.models import FigureEntry, LabelNote

FIG_CAPTION = re.compile(r"Fig\.?\s*(\d+-\d+)\s*(.*)", re.I | re.S)
MIN_IMAGE_AREA = 35 * 35  # pt² — bỏ icon nhỏ
FOOTER_Y_RATIO = 0.92  # vùng footer


def is_diagram_page(page: fitz.Page) -> bool:
    """Trang sơ đồ Kobelco thường xoay 90° hoặc có ảnh chiếm phần lớn."""
    if page.rotation in (90, 270):
        return True
    page_area = page.rect.width * page.rect.height
    img_area = 0.0
    for b in page.get_text("dict")["blocks"]:
        if b.get("type") == 1:
            x0, y0, x1, y1 = b["bbox"]
            img_area += (x1 - x0) * (y1 - y0)
    return img_area > page_area * 0.25


def content_clip_rect(page: fitz.Page) -> fitz.Rect:
    """Vùng nội dung — bỏ footer copyright."""
    r = page.rect
    margin = 8
    footer_cut = r.height * (1 - FOOTER_Y_RATIO)
    return fitz.Rect(r.x0 + margin, r.y0 + margin, r.x1 - margin, r.y1 - footer_cut)


def render_clip(
    page: fitz.Page,
    clip: fitz.Rect,
    out_path: Path,
    dpi: int = 180,
) -> None:
    scale = dpi / 72.0
    mat = fitz.Matrix(scale, scale)
    pix = page.get_pixmap(matrix=mat, clip=clip, alpha=False)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    pix.save(str(out_path))


def extract_image_block(
    page: fitz.Page,
    bbox: list[float],
    out_path: Path,
    dpi: int = 180,
) -> bool:
    x0, y0, x1, y1 = bbox
    area = (x1 - x0) * (y1 - y0)
    if area < MIN_IMAGE_AREA:
        return False
    clip = fitz.Rect(x0, y0, x1, y1)
    # Mở rộng nhẹ để không cắt mất viền
    clip = clip + (-2, -2, 2, 2)
    clip &= page.rect
    try:
        render_clip(page, clip, out_path, dpi=dpi)
        return True
    except Exception:
        return False


def extract_full_page_diagram(
    page: fitz.Page,
    pdf_page: int,
    output_dir: Path,
    prefix: str,
    rel_prefix: str,
) -> Optional[tuple[str, str, fitz.Rect]]:
    """Render toàn vùng nội dung trang sơ đồ — giữ layout gốc."""
    clip = content_clip_rect(page)
    filename = f"{prefix}_p{pdf_page}_full.png"
    abs_path = output_dir / filename
    render_clip(page, clip, abs_path)
    rel_path = f"{rel_prefix}/{filename}"
    return rel_path, str(abs_path), clip


def parse_captions_on_page(page_text: str) -> list[tuple[str, str]]:
    results = []
    for m in FIG_CAPTION.finditer(page_text):
        fig_num = m.group(1)
        title = (m.group(2) or "").strip().split("\n")[0].strip(" :.-")
        results.append((fig_num, title))
    return results


def register_figure(
    registry: dict[str, FigureEntry],
    fig_num: str,
    pdf_page: int,
    manual_ref: Optional[str],
    image_path: Optional[str],
    caption_en: str = "",
    bbox: Optional[list[float]] = None,
    full_page: bool = False,
    labels: Optional[list[LabelNote]] = None,
) -> None:
    fig_id = f"fig-{fig_num}"
    if fig_id in registry:
        e = registry[fig_id]
        if pdf_page not in e.pdf_pages:
            e.pdf_pages.append(pdf_page)
        if manual_ref and manual_ref not in e.manual_refs:
            e.manual_refs.append(manual_ref)
        if image_path and image_path not in e.image_paths:
            e.image_paths.append(image_path)
        if caption_en and not e.caption_en:
            e.caption_en = caption_en
        if labels:
            existing = {n.text_en for n in e.labels}
            for lb in labels:
                if lb.text_en not in existing:
                    e.labels.append(lb)
    else:
        registry[fig_id] = FigureEntry(
            figure_id=fig_id,
            figure_number=fig_num,
            caption_en=caption_en,
            pdf_pages=[pdf_page],
            manual_refs=[manual_ref] if manual_ref else [],
            image_paths=[image_path] if image_path else [],
            bbox=bbox,
            full_page=full_page,
            labels=labels or [],
        )
