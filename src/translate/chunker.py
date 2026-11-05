from __future__ import annotations

import json
import re
from pathlib import Path

from src.ingest.diagram_filter import is_translatable_prose
from src.models import BlockStatus, BlockType, Chunk, Document

TRANSLATABLE_TYPES = {
    BlockType.HEADING,
    BlockType.PARAGRAPH,
    BlockType.CAPTION,
    BlockType.WARNING,
    BlockType.LIST,
    BlockType.TABLE,
}


def document_to_chunks(document: Document) -> list[Chunk]:
    """1 block = 1 chunk — giữ cấu trúc text gốc (xuống dòng, ref)."""
    chunks: list[Chunk] = []
    chunk_idx = 0

    for page in document.pages:
        for block in page.blocks:
            if block.type not in TRANSLATABLE_TYPES:
                continue
            if not block.text_en.strip():
                continue
            if not is_translatable_prose(block.text_en, block.type.value):
                if block.type != BlockType.CAPTION:
                    continue

            chunk_idx += 1
            protected = re.findall(r"YT\d{2}[A-Z]\d{5}[A-Z]\d", block.text_en)
            chunks.append(
                Chunk(
                    chunk_id=f"chunk_{chunk_idx:04d}",
                    doc_id=document.doc_id,
                    pdf_page=page.pdf_page,
                    manual_ref=page.manual_ref,
                    section=page.section,
                    block_type=block.type.value,
                    text_en=block.text_en,
                    protected_tokens=block.protected_tokens or sorted(set(protected)),
                    requires_review=block.requires_review,
                    status=BlockStatus.PENDING,
                    source_block_ids=[block.block_id],
                )
            )

    return chunks


def remap_translations_from_backup(
    new_chunks: list[Chunk],
    backup_chunks: list[Chunk],
) -> list[Chunk]:
    """Gán lại text_vi từ chunk cũ theo block_id hoặc khớp nội dung."""
    from src.ingest.text_structure import apply_structure_from_en, normalize_pdf_text

    by_block: dict[str, str] = {}
    by_en: dict[str, str] = {}

    def norm(s: str) -> str:
        return re.sub(r"\s+", " ", normalize_pdf_text(s).strip())[:400]

    for old in backup_chunks:
        if not old.text_vi or old.text_vi.startswith("[GIỮ NGUYÊN"):
            continue
        for bid in old.source_block_ids:
            by_block[bid] = old.text_vi
        by_en[norm(old.text_en)] = old.text_vi

    for c in new_chunks:
        if c.text_vi and c.status == BlockStatus.DONE:
            continue
        vi = None
        for bid in c.source_block_ids:
            if bid in by_block:
                vi = by_block[bid]
                break
        if not vi:
            vi = by_en.get(norm(c.text_en))
        if not vi:
            # Khớp mờ: chunk cũ chứa nội dung block mới
            cn = norm(c.text_en)
            if len(cn) >= 20:
                for old in backup_chunks:
                    if not old.text_vi:
                        continue
                    on = norm(old.text_en)
                    if cn in on or on in cn:
                        vi = old.text_vi
                        break
                    # Khớp 60 ký tự đầu
                    if cn[:60] == on[:60]:
                        vi = old.text_vi
                        break
        if vi:
            c.text_vi = apply_structure_from_en(c.text_en, vi)
            c.status = BlockStatus.DONE
    return new_chunks


def save_chunks(chunks: list[Chunk], workspace: Path) -> Path:
    chunks_dir = workspace / "chunks"
    chunks_dir.mkdir(parents=True, exist_ok=True)
    for old in chunks_dir.glob("*.json"):
        old.unlink()
    for chunk in chunks:
        path = chunks_dir / f"{chunk.chunk_id}.json"
        path.write_text(chunk.model_dump_json(indent=2), encoding="utf-8")
    manifest = chunks_dir / "manifest.json"
    manifest.write_text(
        json.dumps(
            {
                "total": len(chunks),
                "pending": sum(1 for c in chunks if c.status == BlockStatus.PENDING),
                "done": sum(1 for c in chunks if c.status == BlockStatus.DONE),
                "chunk_ids": [c.chunk_id for c in chunks],
            },
            indent=2,
        ),
        encoding="utf-8",
    )
    return chunks_dir


def load_chunks(workspace: Path) -> list[Chunk]:
    chunks_dir = workspace / "chunks"
    chunks = []
    for path in sorted(chunks_dir.glob("chunk_*.json")):
        chunks.append(Chunk.model_validate_json(path.read_text(encoding="utf-8")))
    return chunks


def load_chunks_backup(workspace: Path) -> list[Chunk]:
    """Đọc backup từ chunks_vi hoặc chunks hiện tại."""
    vi_dir = workspace / "chunks_vi"
    backup = []
    if vi_dir.exists():
        for path in sorted(vi_dir.glob("chunk_*.json")):
            backup.append(Chunk.model_validate_json(path.read_text(encoding="utf-8")))
    if backup:
        return backup
    return load_chunks(workspace)


def load_chunk(workspace: Path, chunk_id: str) -> Chunk:
    path = workspace / "chunks" / f"{chunk_id}.json"
    return Chunk.model_validate_json(path.read_text(encoding="utf-8"))


def save_chunk(workspace: Path, chunk: Chunk) -> None:
    path = workspace / "chunks" / f"{chunk.chunk_id}.json"
    path.write_text(chunk.model_dump_json(indent=2), encoding="utf-8")
    vi_dir = workspace / "chunks_vi"
    vi_dir.mkdir(exist_ok=True)
    if chunk.text_vi:
        vi_path = vi_dir / f"{chunk.chunk_id}.json"
        vi_path.write_text(chunk.model_dump_json(indent=2), encoding="utf-8")
