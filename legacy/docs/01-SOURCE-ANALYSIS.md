# Phân tích sâu tài liệu nguồn

## 1. Thông tin cơ bản

| Thuộc tính | Giá trị |
|------------|---------|
| File | `382014584-sk70sr-1es-shop-manual.pdf` |
| Dung lượng | 24 MB |
| Số trang PDF | **1.012** |
| Định dạng | PDF 1.6 (Adobe Acrobat Pro 9) |
| Ngày tạo | 2018-04-08 |
| Nhà sản xuất | Kobelco Construction Machinery Co., Ltd. |
| Model | **SK70SR-1ES** Hydraulic Excavator |
| Book Code | S5YT0004E01 |
| Ngôn ngữ gốc | Tiếng Anh |

## 2. Cấu trúc 7 cuốn (Kobelco INDEX)

Manual gộp 7 book trong 1 PDF, đánh số mục theo quy ước **XY** trong đó **X = book**, **Y = section**:

| Book | Tên | Sections chính | ~Trang PDF (ước lượng) |
|------|-----|----------------|------------------------|
| 0 | Specifications / Safety | 0-x | ~15 |
| 1 | Maintenance | 11–14 | ~70 |
| 2 | System | 21–25 (Mechatro, Hydraulic, **Electrical**, Components, A/C) | ~266 |
| 3 | Disassembly | 31–34 | ~253 |
| 4 | Troubleshooting | 41–44 | ~71 |
| 5 | Engine | 51+ | ~20 |
| 6–7 | Options / Installing Options | 63 | ~90 |

### Hệ thống đánh số trang nội bộ

Mỗi trang có header dạng **`23-15`** = Mục 23 (Electrical System), trang thứ 15 trong mục đó.

**Ý nghĩa cho pipeline:** Đây là `anchor_id` tự nhiên, tốt hơn số trang PDF vì người đọc manual cũng tra theo số này.

```
anchor_id: "23-15"
pdf_page: 201
section: "23. ELECTRICAL SYSTEM"
subsection: "23.2.5 UPPER ELECTRIC ASSY (2/2)"
```

## 3. Phân tích kỹ thuật PDF

### 3.1 Text vs Scan

| Metric | Kết quả | Kết luận |
|--------|---------|----------|
| Trang text-dominant | 741 / 1.012 (73%) | Text-native |
| Trang image-heavy | 7 / 1.012 (0.7%) | Không cần OCR |
| Trang mixed (text + hình) | 264 / 1.012 (26%) | Chuẩn manual kỹ thuật |
| Ký tự trung bình/trang | 1.172 | Đủ dày để chunk |
| Tổng từ ước lượng | **~212.562** | ~850 trang A4 tương đương |
| PDF bookmarks (TOC) | **0 entries** | Phải build TOC bằng parser |

**Kết luận:** Không cần OCR. Tiết kiệm 30–50% effort so với scan.

### 3.2 Hình ảnh & sơ đồ

| Metric | Kết quả |
|--------|---------|
| Image references | 1.378 |
| Unique figure numbers (`Fig. XX-YY`) | **675** |
| Figure captions trích regex | **709** |
| Harness part numbers (`YT...`) | **249** unique |

**Ví dụ trang 201 (điện):**
- 2 ảnh lớn: wiring diagram + assembly drawing
- Caption: `Fig. 23-11 Upper electric assy (2/2)`, `Fig. 23-12 E/G room light harness`
- Part number: `YT13E01079P1`

**Kết luận hình ảnh:**
- ✅ Trích ảnh gốc theo bbox — khả thi
- ✅ Dịch caption — khả thi
- ✅ Index `Fig. 23-11 → PDF page 201 → caption VI` — khả thi
- ❌ Auto-convert wiring diagram sang Mermaid — **không khả thi** (quá phức tạp, nhiều symbol chuẩn)

### 3.3 Bảng & thông số kỹ thuật

| Loại | Số trang ước lượng |
|------|-------------------|
| Table-like pages | 142 |
| Torque specification tables | 10+ (nhiều hơn trong disassembly) |

**Pattern điển hình (trang 501):**
```
Torque N•m (lbf•ft)
No. Name Q'ty Torque N•m (lbf•ft)
101 CASING 1 29.4 (22)
```

**Quy tắc xử lý:**
- Cột số, mã part, Q'ty → **không dịch**
- Cột Name (CASING, O-RING, PLUG) → dịch hoặc giữ EN tùy glossary
- Header đơn vị → dịch: `Mô-men xoắn N•m (lbf•ft)`

### 3.4 Nhiễu cần loại bỏ trước dịch

Mỗi trang có footer cố định:
```
Copyright © 2018 Kobelco Construction Machinery Co.,Ltd. All rights reserved.
[S5YT0004E01] [0207CsCsspWbYs]
```

→ Strip bằng regex trong ingestion. **100% trang** trong mẫu 100 trang đầu có footer này.

## 4. Phân tích nội dung chuyên ngành điện

### 4.1 Sections điện liên quan

| Section | Tên | PDF pages | Số trang |
|---------|-----|-----------|----------|
| **23** | ELECTRICAL SYSTEM | 187–228 | ~42 |
| **43** | TROUBLESHOOTING (ELECTRIC) | 661–682 | ~22 |
| 24 | COMPONENTS SYSTEM (có phần điện) | 229–338 | ~110 |
| 33–34 | Disassembly (harness, connector) | 419–626 | nhiều |

### 4.2 Tần suất thuật ngữ (toàn manual)

| Thuật ngữ EN | Tần suất |
|--------------|----------|
| sensor | 427 |
| connector | 274 |
| harness | 222 |
| relay | 189 |
| solenoid | 127 |
| battery | 103 |
| starter | 95 |
| ground | 80 |
| actuator | 37 |
| alternator | 25 |
| fuse | 17 |
| wiring | 11 |

→ Ước lượng **800–1.500 thuật ngữ domain** cần glossary khi dịch full manual; **~200–300** cho pilot điện.

### 4.3 Pattern cần bảo vệ (không dịch / không đổi)

```regex
# Part numbers
YT\d{2}[A-Z]\d{5}[A-Z]\d          # YT13E01079P1
[A-Z]{2,}-\d{2,}                     # CN-11, SW-19

# Figure/section refs — giữ số, dịch label
Fig\.\s*\d+-\d+
\d+\.\d+(\.\d+)*                     # 23.2.5

# Units & torque
\d+(\.\d+)?\s*N[•·.]?m\s*\([^)]+\)
\d+\s*°C\s*\(\d+\s*°F\)

# Book code
S5YT0004E01
```

## 5. Mẫu cấu trúc block (trang 201)

```
Block 0: HEADING  "23. ELECTRICAL SYSTEM"           [font 10pt]
Block 1: PAGE_REF "23-15"                           [font 12pt]
Block 2: IMAGE    wiring diagram                    [large bbox]
Block 3: IMAGE    assembly photo/drawing
Block 4: TEXT     "23.2.5 UPPER ELECTRIC ASSY..."   [font 10pt]
Block 5: CAPTION  "Fig. 23-11 Upper electric..."    [font 12pt]
Block 6: CAPTION  "Fig. 23-12 E/G room light..."    [font 12pt]
Block 7: FOOTER   copyright                        [font 6pt] → STRIP
```

→ Parser nên phân loại block theo **font size + position + regex**, không chỉ theo thứ tự.

## 6. Kết luận phân tích nguồn

| Khía cạnh | Đánh giá | Điểm /10 |
|-----------|----------|----------|
| Text extraction | Rất tốt | 9 |
| Structure parsing | TB (no TOC, cần heuristic) | 7 |
| Figure handling | Tốt (extract + index) | 8 |
| Table handling | Khó vừa | 6 |
| Term consistency need | Cao | 9 |
| Mermaid redraw | Không phù hợp | 2 |
| **Tổng khả thi dịch** | **Cao** | **7.8** |

**Khuyến nghị pilot:** Bắt đầu **Section 23 + 43** (64 trang PDF, ~13.000 từ) để validate pipeline trước khi chạy full 1.012 trang.
