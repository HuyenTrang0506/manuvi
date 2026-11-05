---
name: test-label-legend
description: Test quality of diagram label legends — code sort order, Vietnamese coverage, no EN/VI mix, no pin numbers. Use after retranslate-labels / assemble, or when user asks to verify chú thích / bảng tra cứu nhãn.
---

# Test Label Legend

Kiểm tra bảng chú thích dưới sơ đồ mạch (legend) đạt chuẩn tra cứu kỹ thuật.

## Khi nào dùng

- Sau `reextract-labels` / `retranslate-labels` / `assemble --pdf`
- User báo “thiếu dịch”, “lẫn Anh–Việt”, “không tìm được mã”, “sort lung tung”
- Trước khi giao bản PDF/DOCX

## Chạy tự động

```bash
PYTHONPATH=. python3 -m src.cli test-legend
PYTHONPATH=. python3 -m unittest tests.test_label_semantics -v
```

Exit ≠ 0 nếu còn lỗi nghiêm trọng (mixed EN/VI hoặc thiếu nghĩa VI trên mã có tên).

## Tiêu chí bắt buộc

| # | Tiêu chí | Pass | Fail |
|---|----------|------|------|
| 1 | **Sort theo mã** | C-1 → C-2 → D-… → E-… → L-… → M-… → R-… → SE-… → SV-… → SW-1 → SW-2… | Nhóm chức năng làm mã giữa khó tìm |
| 2 | **Có nghĩa VI** | Mọi dòng có mã `X-n` phải có tiếng Việt (có dấu hoặc thuật ngữ đã chuẩn) | `SW-7 → SW-7` / cột VI trống / chỉ copy EN |
| 3 | **Không lẫn EN–VI** | Không còn `KEY SW`, `TRAVEL TWO-SPEED`, `RELEASE` trong cột nghĩa | `máy khởi động KEY SW` |
| 4 | **Bỏ số chân** | Không có dòng chỉ `179`, `802`, `11-13` | Số line No. trong bảng tra cứu |
| 5 | **Mã màu dây** | Không nhồi dưới mỗi hình; có **Phụ lục A** đầu tài liệu | Lặp bảng màu mỗi trang |

## Quy trình agent

1. Chạy `test-legend` → đọc `workspace/<doc_id>/qa/legend_audit.md`
2. Với mỗi finding `mixed` / `missing_vi`:
   - Thêm cụm vào `src/translate/label_translator.py` `PHRASES` **hoặc** `equipment_list.py` / `_EXTRA_SW_PHRASES` trong `label_table.py`
   - Dịch **semantic** (chức năng), không word-by-word
3. `retranslate-labels` → `test-legend` lại đến khi pass
4. `assemble --pdf` — mở trang có Fig; xác nhận:
   - Bảng chú thích sort SW-1, SW-2, SW-3…
   - Dòng “Mã màu dây: xem Phụ lục A”
   - Đầu PDF có heading Phụ lục A + bảng B/G/L/R/W…

## Format finding

```
fig-23-2 / SW-30: 🔴 mixed: 'tự động ACCEL RELEASE SW' → thêm phrase auto accel release sw
fig-23-2 / 179: 🔴 noise: số chân còn trong legend → filter PIN_NUM
sort: 🟡 SW-10 trước SW-2 → sửa code_sort_key (số nguyên, không lexicographic)
```

## Không làm

- Không nhóm lại theo “Công tắc / Cảm biến” (user yêu cầu sort theo mã)
- Không list lại toàn bộ mã dây dưới mỗi hình
- Không commit trừ khi user yêu cầu
