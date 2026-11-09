from __future__ import annotations

from pathlib import Path

from src.ingest.parser import load_document


def build_figure_index(workspace: Path) -> Path:
    document = load_document(workspace)
    ref_dir = workspace / "reference"
    ref_dir.mkdir(parents=True, exist_ok=True)
    out = ref_dir / "figure_index.md"

    lines = [
        "# Danh mục hình ảnh — Chương Điện",
        "",
        "| Hình gốc | Trang manual | Trang PDF | Tiêu đề EN | Tiêu đề VI | File ảnh |",
        "|----------|--------------|-----------|------------|------------|----------|",
    ]

    for fig_id in sorted(document.figures_registry.keys()):
        fig = document.figures_registry[fig_id]
        manual = ", ".join(fig.manual_refs) if fig.manual_refs else "—"
        pdf_p = ", ".join(str(p) for p in fig.pdf_pages)
        img = fig.image_paths[0] if fig.image_paths else "—"
        vi = fig.caption_vi or "*(chưa dịch)*"
        lines.append(
            f"| Fig. {fig.figure_number} | {manual} | {pdf_p} | {fig.caption_en} | {vi} | `{img}` |"
        )

    lines.extend(["", f"Tổng: **{len(document.figures_registry)}** hình.", ""])
    out.write_text("\n".join(lines), encoding="utf-8")
    return out
