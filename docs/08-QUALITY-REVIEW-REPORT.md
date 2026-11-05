# Báo cáo review chất lượng — sk70sr-1es

**Ngày:** 2026-07-29  
**Phạm vi:** Section 23 (điện) + Section 43 (chẩn đoán điện) — 64 trang PDF  
**Phương pháp:** Random sample 8 trang (seed=42) + QA tự động toàn pipeline

---

## Tóm tắt

| Metric | Giá trị |
|--------|---------|
| Tổng chunks | 388 |
| Đã dịch | 165 (42%) |
| Pending | 223 (58%) |
| QA warnings | 32 |
| QA errors | 0 |
| Sample review | ✅ 3 pass · ⚠️ 5 warn · ❌ 0 fail |

**Output đã build:**

| File | Kích thước |
|------|------------|
| `output/sk70sr-1es/vi/section_electrical_vi.docx` | 14.5 MB |
| `output/sk70sr-1es/vi/section_electrical_vi.pdf` | 11.2 MB |

---

## Kết luận nhanh

**Cấu trúc text (xuống dòng, ref):** Đạt trên các trang Section 23 đã kiểm tra. Block NOTES (p.191) và TOC (p.187) giữ đúng dòng gốc; không còn chữ dính kiểu `23.2.5UPPER`.

**Dịch thuật:** Chưa đủ — 58% chunk còn pending. Section 43 (troubleshooting flowchart) thiếu dịch nhiều nhất; trang prose tạm hiển thị EN trong DOCX/PDF.

**Ref hình (Fig.):** Một số chunk caption đã dịch nhưng bỏ số hình `23-11`, `23-12` trong `text_vi` — cần sửa khi dịch batch tiếp.

---

## Trang mẫu (seed=42)

### ✅ PDF p.195 — ref 23-9

- Blocks: 5 | Chunks: 1 | Đã dịch: 1/1
- Cấu trúc dòng: OK
- Không có finding

### ✅ PDF p.202 — ref 23-16

- Blocks: 5 | Chunks: 2 | Đã dịch: 1/2
- Cấu trúc dòng: OK (list item `23.2.6 / UPPER HARNESS ASSY` giữ 2 dòng)
- Chunk heading list còn EN — chấp nhận tạm vì pending

### ✅ PDF p.204 — ref 23-18

- Tương tự p.202 — cấu trúc OK, 1/2 chunk đã dịch

### ⚠️ PDF p.188 — ref 23-2

- Trang bìa/revision — 0/1 chunk dịch
- Không ảnh hưởng nội dung kỹ thuật chính

### ⚠️ PDF p.201 — ref 23-15

- 2/3 chunk dịch
- `chunk_0043`, `chunk_0044`: thiếu ref `Fig. 23-12`, `23-11` trong bản VI (caption đã rút gọn)

### ⚠️ PDF p.666 — ref 43-6

- Trang flowchart controller error — 2/8 chunk dịch (25%)
- Cấu trúc EN giữ tốt; cần ưu tiên dịch batch Section 43

### ⚠️ PDF p.680 — ref 43-20

- 5/13 chunk dịch (38%)
- Một số chunk đã dịch có cấu trúc dòng lệch (EN 3–4 dòng → VI 11 dòng) — do bản dịch cũ gộp câu, không phải lỗi parser
- Khuyến nghị: dịch lại các chunk `chunk_0354`–`chunk_0361` với prompt giữ `\n`

### ⚠️ PDF p.682 — ref 43-22

- 3/10 chunk dịch (30%)
- Cùng pattern cấu trúc lệch ở chunk đã dịch sẵn

---

## Findings (ưu tiên cao → thấp)

| Sev | Vị trí | Vấn đề | Hành động |
|-----|--------|--------|-----------|
| 🟡 | Toàn pipeline | 223/388 chunk pending | Dịch batch qua Cursor (`export-pending`) |
| 🟡 | p.680, p.682 | Cấu trúc VI lệch so EN (flowchart) | Dịch lại, giữ xuống dòng |
| 🟡 | chunk_0043–0044 | Thiếu số Fig. trong VI | Thêm `23-11`, `23-12` vào caption VI |
| 🔵 | Một số chunk | `mm 2` chưa → `mm²` | Re-ingest hoặc normalize khi assemble |
| 🔵 | Label bảng hình | Nhiều nhãn diagram còn EN | Bổ sung glossary + auto_captions |

---

## Công cụ review đã thêm

```bash
# Lấy mẫu ngẫu nhiên N trang
PYTHONPATH=. python3 -m src.cli review-samples --count 8 --seed 42

# QA toàn bộ
PYTHONPATH=. python3 -m src.cli qa

# Build DOCX + PDF
PYTHONPATH=. python3 -m src.cli assemble --pdf
```

Report tự động:

- `workspace/sk70sr-1es/qa/sample_review.md`
- `workspace/sk70sr-1es/qa/sample_review.json`
- `workspace/sk70sr-1es/qa/report.json`

Skills (cho agent):

- `.cursor/skills/review-output-pages/SKILL.md`
- `.cursor/skills/write-technical-docs/SKILL.md`

---

## Khuyến nghị tiếp theo

1. **Dịch 223 chunk pending** — ưu tiên Section 43 flowchart (p.666–682)
2. **Sửa chunk đã dịch có cấu trúc lệch** — ~6 chunk ở p.680–682
3. **Giữ ref Fig.** trong mọi caption VI
4. **Re-run** `review-samples` sau mỗi batch dịch để theo dõi pass rate

---

*Báo cáo sinh tự động; số liệu lấy từ `status`, `qa`, `review-samples` ngày 2026-07-29.*
