# Đánh giá rủi ro & phương án thay thế

## 1. Ma trận khả thi tổng hợp

Dựa trên phân tích thực tế file `382014584-sk70sr-1es-shop-manual.pdf`:

| Hạng mục | Khả thi | Điểm | Ghi chú |
|----------|---------|------|---------|
| Trích xuất text | Rất cao | 9/10 | Text-native, không OCR |
| Parse cấu trúc section | Cao | 7/10 | Không có PDF TOC, dùng heuristic |
| Extract 675 hình + caption | Cao | 8/10 | Regex + bbox |
| Figure index đối chiếu gốc | Rất cao | 9/10 | Core deliverable |
| Glossary 800+ terms | Cao | 8/10 | Cần human review |
| Dịch batch qua API | Rất cao | 9/10 | ~$80–180 full doc |
| Giữ format bảng torque | Trung bình | 6/10 | Cần table pipeline riêng |
| Layout DOCX đẹp ≈ gốc | Trung bình | 5/10 | Chấp nhận khác layout, cùng nội dung |
| Mermaid cho wiring diagram | Rất thấp | 2/10 | Không khuyến nghị |
| Dịch full qua Cursor chat | Rất thấp | 2/10 | Chỉ pilot nhỏ |
| **Tổng pipeline hybrid** | **Cao** | **7.8/10** | **Khuyến nghị** |

---

## 2. Rủi ro chi tiết

### R1: Parser misclassify blocks (Mức: Trung bình)

**Mô tả:** Heading vs paragraph vs caption nhầm do font size không nhất quán.

**Impact:** Chunk sai → dịch lệch context.

**Giảm thiểu:**
- Rule-based + font size threshold per document
- Manual spot-check 20 trang đầu mỗi section
- `block_type` có thể override thủ công trong JSON

**Residual risk:** 5–10% blocks cần fix tay.

---

### R2: Caption không link đúng figure (Mức: Trung bình)

**Mô tả:** Một số trang có nhiều hình, caption có thể không theo thứ tự block.

**Impact:** Figure index sai mapping.

**Giảm thiểu:**
- Match caption `Fig. XX-YY` với image trên cùng trang
- QA report: figures without caption / captions without image
- Human review figure index cho section 23 pilot

**Residual risk:** <3% figures cần manual link.

---

### R3: Thuật ngữ không nhất quán (Mức: Cao nếu không có glossary)

**Mô tả:** `ground` dịch lúc "tiếp đất" lúc "mass" lúc "đất".

**Impact:** KTV không tin tưởng bản dịch.

**Giảm thiểu:**
- Glossary bắt buộc trước batch
- Post-QA consistency scan
- Re-run affected chunks khi update glossary

**Residual risk:** Thấp nếu có glossary; Cao nếu không.

---

### R4: Bảng torque bị vỡ format (Mức: Trung bình–Cao)

**Mô tả:** 142 trang table-like; LLM free-text phá cấu trúc cột.

**Impact:** KTV không đọc được mô-men xoắn.

**Giảm thiểu:**
- Table pipeline riêng (parse → translate name column → render)
- Không dùng LLM cho toàn bộ table
- Fallback: embed ảnh crop bảng gốc + caption VI

**Residual risk:** 10–15% bảng phức tạp cần review.

---

### R5: Chi phí API vượt dự toán (Mức: Thấp)

**Mô tả:** Re-run nhiều lần do glossary thay đổi.

**Impact:** +$50–100.

**Giảm thiểu:**
- Pilot trước ($3–8) để tune prompt
- TM cache
- `glossary-diff` re-run selective
- Dùng model tier phù hợp (Sonnet cho bulk, Opus cho warnings)

---

### R6: Bản quyền Kobelco (Mức: Pháp lý — cần lưu ý)

**Mô tả:** Shop manual có copyright © 2018 Kobelco. Dịch cho nội bộ sửa chữa vs phân phối.

**Impact:** Pháp lý nếu distribute publicly.

**Giảm thiểu:**
- Xác nhận mục đích sử dụng (nội bộ xưởng vs commercial)
- Không publish bản dịch online
- Ghi rõ "bản dịch tham khảo, không thay thế manual gốc"

---

## 3. So sánh 4 phương án

### Phương án A: Pipeline hybrid (KHUYẾN NGHỊ)

```
Parse local → Glossary → API batch → QA → Assemble DOCX
```

| Ưu | Nhược |
|----|-------|
| Khả thi 1.012 trang | Cần build tool 2–4 tuần |
| Nhất quán thuật ngữ | Cần API budget ~$100–200 |
| Resume, reproducible | |
| Figure index đầy đủ | |

**Khả thi: 78%** | **Timeline: 14–16 tuần**

---

### Phương án B: Bán tự động Cursor agent

```
Parse local → Chunk files → Cursor chat dịch từng file → Ghép
```

| Ưu | Nhược |
|----|-------|
| Không cần API | 1.012 trang = không realistic |
| Dev ít hơn | Không resume tốt |
| | Thuật ngữ lệch |
| | Pro quota hết nhanh |

**Khả thi: 20%** cho full doc | **Chỉ hợp pilot ≤50 trang**

---

### Phương án C: Dịch vụ professional (agency)

```
Gửi PDF → Agency dịch + DTP → Nhận bản VI
```

| Ưu | Nhược |
|----|-------|
| Chất lượng cao nhất | $5.000–15.000+ |
| Không cần dev | Mất kiểm soát glossary |
| | Timeline 2–4 tháng |
| | Vẫn cần cung cấp glossary nếu muốn đúng thuật ngữ VN |

**Khả thi: 95%** về chất lượng | **Chi phí cao**

---

### Phương án D: Chỉ dịch phần điện (MVP thực dụng)

```
Pilot sec 23 + 43 only (~64 trang)
```

| Ưu | Nhược |
|----|-------|
| 2–3 tuần có kết quả | Không full manual |
| ~$5–10 API | |
| Validate toàn bộ pipeline | |
| Đủ cho team sửa điện | |

**Khả thi: 90%** | **Khuyến nghị nếu muốn kết quả nhanh**

---

## 4. Decision matrix

| Tiêu chí (trọng số) | A Hybrid | B Cursor | C Agency | D MVP điện |
|---------------------|----------|----------|----------|------------|
| Chi phí (20%) | 8 | 9 | 3 | 10 |
| Thời gian (20%) | 7 | 2 | 5 | 9 |
| Chất lượng (25%) | 8 | 4 | 10 | 8 |
| Khả thi 1012 trang (25%) | 8 | 2 | 9 | 3 |
| Tái sử dụng (10%) | 10 | 3 | 2 | 8 |
| **Weighted score** | **8.0** | **3.8** | **6.1** | **7.6** |

---

## 5. Khuyến nghị cuối

### Nếu mục tiêu là full 1.012 trang
→ **Phương án A** (pipeline hybrid), bắt đầu bằng **Phương án D** (pilot điện) làm proof-of-concept.

### Nếu chỉ cần phần điện + troubleshooting
→ **Phương án D** đủ dùng, 3 tuần có deliverable.

### Nếu budget không giới hạn và cần chất lượng publish
→ **Phương án C**, nhưng vẫn nên build glossary trước để đưa cho agency.

### Không khuyến nghị
→ **Phương án B** cho >100 trang.

---

## 6. Go / No-go criteria sau pilot (tuần 5)

| Metric | Go | No-go |
|--------|-----|-------|
| Chunk success rate | ≥98% | <95% |
| Protected token violations | 0 | >5 |
| KTV satisfaction (1–5) | ≥4 | <3 |
| Figure index accuracy | ≥95% | <90% |
| API cost per page | <$0.15 | >$0.30 |
| Pilot timeline | ≤5 tuần | >7 tuần |

**No-go action:** Reassess — cân nhắc agency cho phần còn lại, giữ pipeline cho glossary + index.

---

## 7. Câu hỏi cần bạn xác nhận trước khi code

1. **Mục tiêu scope:** Full 1.012 trang hay chỉ electrical (sec 23 + 43)?
2. **API budget:** Có sẵn ~$100–200 cho LLM API không?
3. **Output format:** DOCX ưu tiên hay PDF?
4. **Review resource:** Có KTV điện review glossary 5h/tuần không?
5. **Mục đích sử dụng:** Nội bộ xưởng hay phân phối?

Trả lời 5 câu này → có thể bắt đầu **Sprint 1** (M0 + M1) ngay.
