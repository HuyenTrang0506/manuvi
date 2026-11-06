# Docs — Translator Electronic

Kế hoạch khả thi dịch **Kobelco SK70SR-1ES Shop Manual** (1.012 trang EN → VI).

Phân tích dựa trên file thực tế: `data/382014584-sk70sr-1es-shop-manual.pdf`

**Scope chính thức:** Chương điện — Section 23 + 43 (64 trang, ~12K từ)

## Mục lục

| # | Tài liệu | Mô tả |
|---|----------|-------|
| 00 | [Executive Summary](./00-EXECUTIVE-SUMMARY.md) | Tóm tắt, kết luận, khuyến nghị |
| **07** | **[Requirements chương Điện](./07-REQUIREMENTS-ELECTRICAL-FOCUS.md)** | **Scope, timeline 3–4 tuần, local free, tối ưu $0** |
| 01 | [Source Analysis](./01-SOURCE-ANALYSIS.md) | Phân tích sâu PDF: cấu trúc, hình, bảng, thuật ngữ |
| 02 | [Architecture](./02-ARCHITECTURE.md) | Kiến trúc 5 module, schema, tech stack |
| 03 | [Glossary Pipeline](./03-GLOSSARY-PIPELINE.md) | Corpus, thuật ngữ, TM, term injection |
| 04 | [Translation Pipeline](./04-TRANSLATION-PIPELINE.md) | Chunking, API batch, QA, assemble output |
| **09** | **[Excel Workflow](./09-EXCEL-WORKFLOW.md)** | **Export/import Excel → DOCX/PDF** |
| **08** | **[Quality Review Report](./08-QUALITY-REVIEW-REPORT.md)** | **Review mẫu 8 trang, QA, output DOCX/PDF** |
| 05 | [Implementation Roadmap](./05-IMPLEMENTATION-ROADMAP.md) | Milestone, sprint, timeline 14–16 tuần |
| 06 | [Risk & Alternatives](./06-RISK-AND-ALTERNATIVES.md) | Ma trận rủi ro, so sánh 4 phương án |

## Kết luận nhanh

- **Khả thi scope điện: 92/100** — chỉ 64 trang, ~12K từ, 57 hình
- **Timeline: 3–4 tuần** đến bản dùng được
- **Chi phí: $0–3** — Ollama `qwen2.5:14b` local, API chỉ fix chunk lỗi
- **Không cần** dịch full 1.012 trang ở giai đoạn này

## Số liệu scope điện

```
Section 23:       42 trang, ~5.300 từ, 57 hình
Section 43:       22 trang, ~6.900 từ
Tổng:             64 trang, ~12.000 từ, ~80 chunks
Timeline:         3-4 tuần
Chi phí:          $0-3 (local first)
Model:            Ollama qwen2.5:14b (free)
```

## Next step

```bash
PYTHONPATH=. python3 -m src.cli export-excel    # KTV review
PYTHONPATH=. python3 -m src.cli import-excel --file workspace/sk70sr-1es/excel/translation.xlsx
PYTHONPATH=. python3 -m src.cli assemble --pdf
```
