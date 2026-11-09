# Dịch Shop Manual — Prompt cho Cursor Agent

Bạn dịch chunk từ **tiếng Anh sang tiếng Việt** cho Shop Manual máy xúc Kobelco SK70SR-1ES (chương điện).

## Quy tắc BẮT BUỘC

1. Đọc `workspace/{doc_id}/glossary/glossary.json` — dùng **ĐÚNG** `term_vi` cho mọi `term_en` có `status: approved`.
2. **KHÔNG dịch / KHÔNG đổi:**
   - Mã phụ tùng: `YT13E01079P1` (pattern `YT##X#####X#`)
   - Số hình: `Fig. 23-11` (giữ nguyên format)
   - Số mục: `23.2.5`, `23-15`
   - Đơn vị số: `N•m`, `°C`, `°F`, `V`, `A`, `mm`
   - `ECU`, `OBD`, book code
3. Cross-reference:
   - `See Fig. 23-11` → `Xem Hình 23-11`
   - `WARNING` → `CẢNH BÁO`, `CAUTION` → `THẬN TRỌNG`, `DANGER` → `NGUY HIỂM`
4. Giữ format bước: `(1)`, `(2)`, `(3)`...
5. Văn phong: kỹ thuật, ngắn gọn, dùng được cho KTV sửa máy.

## Workflow mỗi phiên (~10 chunk)

```
1. Liệt kê workspace/{doc_id}/chunks/ có "status": "pending"
2. Lấy tối đa 10 chunk đầu tiên (theo chunk_id)
3. Với mỗi chunk:
   - Đọc file chunks/{chunk_id}.json
   - Dịch field text_en → text_vi
   - Ghi workspace/{doc_id}/chunks_vi/{chunk_id}.json (full object)
   - Cập nhật chunks/{chunk_id}.json: status=done, text_vi=...
4. Báo cáo: đã dịch bao nhiêu chunk, còn bao nhiêu pending
```

## Ví dụ output chunk

```json
{
  "chunk_id": "chunk_0001",
  "text_en": "23.2.5 UPPER ELECTRIC ASSY (2/2)...",
  "text_vi": "23.2.5 LẮP RÁP ĐIỆN PHẦN TRÊN (2/2)...",
  "status": "done"
}
```

## Lệnh kiểm tra tiến độ

```bash
python -m src.cli status --doc-id sk70sr-1es
python -m src.cli qa check --doc-id sk70sr-1es
```
