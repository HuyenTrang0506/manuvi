# Tóm tắt điều hành — Dự án dịch Shop Manual Kobelco SK70SR-1ES

**Ngày phân tích:** 2026-07-29  
**Cập nhật scope:** 2026-07-29 — **Ưu tiên chương Điện**  
**Tài liệu nguồn:** `data/382014584-sk70sr-1es-shop-manual.pdf`  
**Đánh giá khả thi (scope điện):** **94/100 — Cursor Pro làm engine dịch, $0 thêm**

---

## Kết luận một dòng

**Scope chính: Section 23 (Điện) + 43 (Troubleshooting điện)** — 64 trang, **~12.000 từ**, **57 hình**. Pipeline **parse → glossary → Cursor agent dịch file chunk → assemble**. Ollama **không** dùng bulk dịch (thiếu data chuyên ngành). **3–4 tuần**, **$0 thêm** (Cursor Pro đã có).

## Scope & timeline (điện only)

| Hạng mục | Giá trị |
|----------|---------|
| Section 23 ELECTRICAL | 42 trang, ~5.300 từ, 57 hình |
| Section 43 TROUBLESHOOTING ELECTRIC | 22 trang, ~6.900 từ |
| **Tổng** | **64 trang, ~12.000 từ, ~80 chunks** |
| **Thời gian** | **3–4 tuần** (gồm KTV review) |
| **Chi phí thêm** | **$0** (Cursor Pro) |
| **Engine dịch** | **Cursor agent** (~8 phiên, 10 chunk/phiên) |

## Khuyến nghị chiến lược (cập nhật)

| Giai đoạn | Phạm vi | Thời gian | Chi phí |
|-----------|---------|-----------|---------|
| **Phase 1 (NOW)** | Sec 23 + 43 — Cursor dịch ~80 chunks | **3–4 tuần** | **$0** |
| Phase 2 (optional) | + Sec 0 Safety, harness disassembly refs | +1 tuần | $0–2 |
| Phase 3 (tương lai) | Full manual 1.012 trang | 14–16 tuần | $80–180 |

> Chi tiết requirements, local model, tối ưu chi phí: [07-REQUIREMENTS-ELECTRICAL-FOCUS.md](./07-REQUIREMENTS-ELECTRICAL-FOCUS.md)

## Điểm mạnh của tài liệu (giảm rủi ro)

- Text copy được, không cần OCR
- Đánh số trang nội bộ dạng `23-15` (mục-trang) — anchor tự nhiên
- 709 caption hình trích xuất được bằng regex
- 249 mã harness `YTxx...` — có thể whitelist "không dịch"

## Điểm khó cần thiết kế quanh

- **Không có PDF bookmarks/TOC** — phải parse cấu trúc từ header
- **1.378 ảnh nhúng** — giữ ảnh gốc + index, không vẽ lại wiring diagram
- **142 trang dạng bảng** — cần pipeline riêng cho torque table
- **Footer copyright** lặp mỗi trang — phải strip trước khi dịch
- **675 hình** — caption dịch, ảnh giữ nguyên

## Chi phí vận hành (scope điện — tối ưu)

| Hạng mục | Ước lượng |
|----------|-----------|
| Dịch ~80 chunks (Cursor agent) | **$0** — dùng Pro đã có |
| Script ingest/QA/assemble | $0 — Python local |
| Ollama | Không dùng bulk dịch |
| KTV review glossary + warnings | 8–15 giờ |
| Paid API (chỉ phase full manual sau) | $80–180 |

*Full manual 1.012 trang (phase sau): $80–180 API nếu cần.*

## Deliverable cuối cùng

```
output/sk70sr-1es/
├── vi/
│   ├── shop_manual_vi.docx      # Bản đọc/chỉnh sửa chính
│   ├── shop_manual_vi.pdf
│   └── shop_manual_bilingual.pdf # Optional: đối chiếu
├── reference/
│   ├── figure_index_vi.md       # 675 hình: số gốc ↔ caption VI ↔ PDF page
│   ├── page_map.json            # {manual_ref: "23-15", pdf_page: 201}
│   └── section_index.md
├── assets/figures/              # Ảnh gốc extracted
├── glossary/
│   └── glossary_v1.json
└── qa/
    ├── untranslated_terms.csv
    └── review_queue.json
```

## Đọc tiếp

1. [Phân tích tài liệu nguồn](./01-SOURCE-ANALYSIS.md)
2. [Kiến trúc hệ thống](./02-ARCHITECTURE.md)
3. [Pipeline glossary & corpus](./03-GLOSSARY-PIPELINE.md)
4. [Pipeline dịch & lắp ráp output](./04-TRANSLATION-PIPELINE.md)
5. [Lộ trình triển khai](./05-IMPLEMENTATION-ROADMAP.md)
6. [Đánh giá rủi ro & phương án thay thế](./06-RISK-AND-ALTERNATIVES.md)
7. [**Requirements chương Điện + local free + tối ưu chi phí**](./07-REQUIREMENTS-ELECTRICAL-FOCUS.md)
