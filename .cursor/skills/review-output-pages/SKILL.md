---
name: review-output-pages
description: Random-sample review of translated manual pages — compare PDF source, chunks, DOCX structure, refs, and translation coverage. Use when user asks to review output quality, spot-check pages, or audit translation pipeline results.
---

# Review Output Pages

Kiểm tra chất lượng output dịch sổ tay kỹ thuật bằng cách lấy mẫu ngẫu nhiên và so sánh nguồn vs output.

## Khi nào dùng

- User yêu cầu "review", "kiểm tra random", "spot check" output
- Sau `assemble` hoặc thay đổi `text_structure.py` / `docx_builder.py`
- Trước khi giao bản dịch cho khách

## Quy trình

1. Chạy CLI:
   ```bash
   PYTHONPATH=. python3 -m src.cli review-samples --count 8 --seed 42
   ```
2. Đọc report: `workspace/<doc_id>/qa/sample_review.md` và `sample_review.json`
3. Với mỗi trang mẫu, kiểm tra 5 tiêu chí:

| # | Tiêu chí | Pass | Fail |
|---|----------|------|------|
| 1 | **Cấu trúc dòng** | Số dòng EN ≈ số dòng VI (±1) | VI gộp 1 đoạn dài |
| 2 | **Ref giữ nguyên** | `23-15`, `Fig. 23-7`, `YT04C07175A1` có trong VI | Ref bị đổi/mất |
| 3 | **Không dính chữ** | Không có `\d\.\d+[A-Z]` không khoảng trắng | `23.2.5UPPER`, `NOTES:1.` |
| 4 | **Tiến độ dịch** | ≥80% chunk done trên trang prose | >50% pending EN |
| 5 | **Hình ảnh** | `image_path` tồn tại, figure trong registry | Ảnh thiếu / caption lệch |

## Format báo cáo (caveman-review style)

Mỗi finding một dòng:

```
p.201/23-15: 🟡 risk: 1/3 chunk pending (list heading). Dịch chunk_00xx.
p.204/23-18: 🔵 nit: list item vẫn EN "UPPER HARNESS ASSY". Dịch hoặc rule heading.
```

Severity:
- `🔴 bug:` ref/part number sai, cấu trúc hỏng, ảnh mất
- `🟡 risk:` pending dịch, mm² chưa chuẩn, label EN sót
- `🔵 nit:` style, caption auto, glossary draft

## So sánh thủ công (khi cần)

```bash
# Text gốc PDF page
PYTHONPATH=. python3 -c "
from src.ingest.parser import load_document
from pathlib import Path
p = [x for x in load_document(Path('workspace/sk70sr-1es')).pages if x.pdf_page==191][0]
for b in p.blocks: print(b.type, repr(b.text_en[:120]))
"
```

## Không làm

- Không sửa code trong bước review — chỉ ghi findings
- Không review toàn bộ 64 trang — dùng sample + QA report
- Không bỏ qua trang có `requires_review` hoặc WARNING
