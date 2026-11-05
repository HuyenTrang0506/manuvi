---
name: write-technical-docs
description: Write careful, structured technical documentation for the translator-electronic project. Use when creating or updating docs in docs/, QA reports, README sections, or pipeline guides.
---

# Write Technical Docs (translator-electronic)

Viết tài liệu kỹ thuật chính xác, có cấu trúc, dựa trên bằng chứng thực tế từ pipeline — không suy đoán.

## Nguyên tắc

1. **Bằng chứng trước** — chạy CLI / đọc JSON trước khi viết số liệu (chunk count, QA issues, page refs).
2. **Một ý một đoạn** — không gộp nhiều chủ đề trong một section.
3. **Tiếng Việt** cho doc user-facing; giữ EN cho tên file/code/CLI.
4. **Không markdown table dày** nếu >6 cột — dùng danh sách hoặc tách section.
5. **Cập nhật cross-link** — `docs/README.md` index khi thêm file mới.

## Cấu trúc file docs/

| File | Nội dung |
|------|----------|
| `00-EXECUTIVE-SUMMARY.md` | Tóm tắt dự án, quyết định chính |
| `04-TRANSLATION-PIPELINE.md` | Luồng ingest→chunk→dịch→assemble |
| `08-QUALITY-REVIEW-REPORT.md` | Báo cáo review mẫu (cập nhật sau mỗi review) |

## Template báo cáo chất lượng

```markdown
# Báo cáo review chất lượng — <doc_id>

**Ngày:** YYYY-MM-DD  
**Phạm vi:** Section 23 + 43  
**Phương pháp:** Random sample N trang (seed=X) + QA tự động

## Tóm tắt

| Metric | Giá trị |
|--------|---------|
| Tổng chunks | ... |
| Đã dịch | ... |
| QA warnings | ... |

## Trang mẫu

### PDF p.XXX — ref YY-ZZ

**Trạng thái:** ✅ / ⚠️ / ❌

- Cấu trúc dòng: ...
- Ref: ...
- Dịch: ...

## Findings (ưu tiên cao → thấp)

1. ...

## Khuyến nghị

1. ...
```

## Checklist trước khi publish doc

- [ ] Số liệu khớp `python3 -m src.cli status` và `qa`
- [ ] Đường dẫn file output đúng (`output/<doc_id>/vi/`)
- [ ] Lệnh CLI copy-paste được (có `PYTHONPATH=.`)
- [ ] Ghi rõ hạn chế (pending chunks, label EN, v.v.)
- [ ] Thêm mục vào `docs/README.md`

## Thuật ngữ cố định

| EN | VI (dùng trong doc) |
|----|---------------------|
| chunk | chunk (không dịch) |
| manual ref | số trang sổ tay (vd. 23-15) |
| figure | hình / Fig. |
| harness | bộ dây điện |
| assy | cụm lắp ráp |
