from __future__ import annotations

import re
from pathlib import Path
from typing import Optional

import fitz

from src.glossary.manager import load_seed_glossary
from src.ingest.text_structure import extract_text_from_block, normalize_pdf_text
from src.ingest.diagram_labels import extract_labels_for_bbox, extract_labels_from_page
from src.ingest.figure_regions import (
    content_clip_rect,
    extract_full_page_diagram,
    extract_image_block,
    is_diagram_page,
    parse_captions_on_page,
    register_figure,
)
from src.ingest.noise_filter import (
    classify_block,
    extract_manual_ref,
    extract_protected_tokens,
    strip_noise,
)
from src.models import Block, BlockType, Document, LabelNote, Page

SEEDS = Path(__file__).resolve().parents[1] / "glossary" / "seeds" / "construction_electrical_en_vi.csv"

SECTION_RANGES: dict[str, tuple[str, int, int]] = {
    "23": ("23. ELECTRICAL SYSTEM", 187, 228),
    "43": ("43. TROUBLESHOOTING (ELECTRIC)", 661, 682),
}

MIN_IMAGE_AREA = 35 * 35


def _block_sort_key(bbox: list[float], rotation: int) -> tuple:
    x0, y0, x1, y1 = bbox
    if rotation in (90, 270):
        return (x0, y0)
    return (y0, x0)


def _raster_image_blocks(page: fitz.Page) -> list[dict]:
    imgs = []
    for b in page.get_text("dict")["blocks"]:
        if b.get("type") != 1:
            continue
        bbox = b["bbox"]
        area = (bbox[2] - bbox[0]) * (bbox[3] - bbox[1])
        if area >= MIN_IMAGE_AREA:
            imgs.append({"bbox": list(bbox), "area": area})
    imgs.sort(key=lambda x: _block_sort_key(x["bbox"], page.rotation))
    return imgs


def _blocks_from_page(
    page: fitz.Page,
    pdf_page: int,
    section_label: str,
    section_id: str,
    assets_dir: Path,
    rel_prefix: str,
    figures_registry: dict,
    glossary,
) -> Page:
    raw = page.get_text("text")
    clean = strip_noise(raw)
    manual_ref = extract_manual_ref(clean, section_hint=section_id)
    prefix = f"sec{section_id}"
    blocks: list[Block] = []
    captions = parse_captions_on_page(clean)
    diagram = is_diagram_page(page)
    raster_imgs = _raster_image_blocks(page)

    # --- Hình ảnh: vector (full page) hoặc raster theo bbox ---
    if diagram and not raster_imgs:
        result = extract_full_page_diagram(page, pdf_page, assets_dir, prefix, rel_prefix)
        if result:
            rel_path, _, clip = result
            labels = extract_labels_from_page(page, glossary)
            fig_nums = [c[0] for c in captions] or [f"23-{pdf_page % 100}"]
            fig_num = fig_nums[0]
            register_figure(
                figures_registry,
                fig_num,
                pdf_page,
                manual_ref,
                rel_path,
                captions[0][1] if captions else "",
                bbox=[clip.x0, clip.y0, clip.x1, clip.y1],
                full_page=True,
                labels=labels,
            )
            blocks.append(
                Block(
                    block_id=f"p{pdf_page}_full",
                    type=BlockType.FIGURE,
                    image_path=rel_path,
                    full_page=True,
                    bbox=[clip.x0, clip.y0, clip.x1, clip.y1],
                    figure_id=f"fig-{fig_num}",
                    labels=labels,
                )
            )
    elif raster_imgs:
        for idx, img in enumerate(raster_imgs):
            filename = f"{prefix}_p{pdf_page}_fig{idx + 1}.png"
            abs_path = assets_dir / filename
            if not extract_image_block(page, img["bbox"], abs_path):
                continue
            rel_path = f"{rel_prefix}/{filename}"
            labels = extract_labels_for_bbox(page, img["bbox"], glossary)
            fig_num = captions[idx][0] if idx < len(captions) else f"?-{idx+1}"
            cap_en = captions[idx][1] if idx < len(captions) else ""
            register_figure(
                figures_registry,
                fig_num,
                pdf_page,
                manual_ref,
                rel_path,
                cap_en,
                bbox=img["bbox"],
                labels=labels,
            )
            blocks.append(
                Block(
                    block_id=f"p{pdf_page}_fig{idx + 1}",
                    type=BlockType.FIGURE,
                    image_path=rel_path,
                    bbox=img["bbox"],
                    figure_id=f"fig-{fig_num}",
                    labels=labels,
                )
            )

    # --- Text blocks (thứ tự đọc) ---
    text_items = []
    for b in page.get_text("dict")["blocks"]:
        if b.get("type") != 0:
            continue
        bbox = b.get("bbox")
        font_sizes = [
            span.get("size", 10.0)
            for line in b.get("lines", [])
            for span in line.get("spans", [])
        ]
        raw_text = extract_text_from_block(b)
        text = strip_noise(normalize_pdf_text(raw_text))
        if not text:
            continue
        text_items.append((bbox, text, font_sizes[0] if font_sizes else None))

    text_items.sort(key=lambda x: _block_sort_key(list(x[0]), page.rotation))

    text_block_idx = 0
    for bbox, text, font_size in text_items:
        btype = classify_block(text, font_size)
        if btype == BlockType.FOOTER:
            continue
        # Bỏ nhãn đã gom vào figure labels (trang sơ đồ vector)
        if diagram and not raster_imgs and btype not in (
            BlockType.HEADING,
            BlockType.CAPTION,
            BlockType.WARNING,
            BlockType.LIST,
            BlockType.PARAGRAPH,
        ):
            if len(text) < 90 and btype != BlockType.CAPTION:
                continue
        if diagram and btype == BlockType.PARAGRAPH and len(text) < 60:
            continue

        text_block_idx += 1
        protected = extract_protected_tokens(text)
        figure_id = None
        if btype == BlockType.CAPTION:
            m = re.search(r"Fig\.?\s*(\d+-\d+)", text, re.I)
            if m:
                figure_id = f"fig-{m.group(1)}"

        blocks.append(
            Block(
                block_id=f"p{pdf_page}_b{text_block_idx}",
                type=BlockType(btype),
                text_en=text,
                font_size=font_size,
                bbox=list(bbox),
                figure_id=figure_id,
                protected_tokens=protected,
                requires_review=btype == BlockType.WARNING,
            )
        )

    # Sắp xếp tất cả blocks theo thứ tự đọc gốc
    blocks.sort(key=lambda b: _block_sort_key(b.bbox or [0, 0, 0, 0], page.rotation))

    return Page(
        pdf_page=pdf_page,
        manual_ref=manual_ref,
        section=section_label,
        blocks=blocks,
    )


def parse_pdf(
    pdf_path: Path,
    doc_id: str,
    sections: list[str],
    workspace: Path,
) -> Document:
    doc = fitz.open(str(pdf_path))
    assets_dir = workspace / "assets" / "figures"
    assets_dir.mkdir(parents=True, exist_ok=True)
    rel_prefix = "assets/figures"
    glossary = load_seed_glossary(SEEDS)

    figures_registry: dict = {}
    pages: list[Page] = []

    for sec_id in sections:
        if sec_id not in SECTION_RANGES:
            raise ValueError(f"Unknown section: {sec_id}")
        label, start, end = SECTION_RANGES[sec_id]
        for pdf_page in range(start, end + 1):
            if pdf_page > len(doc):
                continue
            page = doc[pdf_page - 1]
            parsed = _blocks_from_page(
                page,
                pdf_page,
                label,
                sec_id,
                assets_dir,
                rel_prefix,
                figures_registry,
                glossary,
            )
            pages.append(parsed)

    document = Document(
        doc_id=doc_id,
        source_file=str(pdf_path.name),
        sections=sections,
        total_pdf_pages=len(doc),
        pages=pages,
        figures_registry=figures_registry,
    )
    doc.close()
    return document


def save_document(document: Document, workspace: Path) -> Path:
    workspace.mkdir(parents=True, exist_ok=True)
    out = workspace / "document_model.json"
    out.write_text(document.model_dump_json(indent=2), encoding="utf-8")
    return out


def load_document(workspace: Path) -> Document:
    path = workspace / "document_model.json"
    return Document.model_validate_json(path.read_text(encoding="utf-8"))


def build_page_map(document: Document) -> dict:
    page_map = {}
    for page in document.pages:
        if page.manual_ref:
            page_map[page.manual_ref] = {
                "pdf_page": page.pdf_page,
                "section": page.section,
            }
    return page_map


def save_page_map(document: Document, workspace: Path) -> Path:
    import json

    ref_dir = workspace / "reference"
    ref_dir.mkdir(parents=True, exist_ok=True)
    path = ref_dir / "page_map.json"
    path.write_text(json.dumps(build_page_map(document), indent=2), encoding="utf-8")
    return path
