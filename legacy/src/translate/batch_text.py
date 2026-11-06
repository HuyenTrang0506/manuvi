"""Batch text translation — for-loop với per-unit cache và resume support.

Xử lý từng chunk văn bản một lần, ghi cache ngay sau mỗi unit.
Không bỏ sót: mỗi chunk được liệt kê trước, xử lý tuần tự.

Cache dir: workspace/<doc>/translations/vision_text/<chunk_id>.json
"""

from __future__ import annotations

import base64
import json
import os
import re
import time
import urllib.error
import urllib.request
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional

from src.models import BlockStatus, Chunk

SYSTEM_PROMPT = """Bạn là kỹ thuật viên điện máy công trình và biên dịch viên kỹ thuật EN→VI.
Nguồn: Kobelco SK70SR-1ES Shop Manual, Chương 23 (Electrical System) và 43 (Troubleshooting Electric).
Độc giả: thợ sửa chữa máy xúc, cần bản dịch tiếng Việt kỹ thuật rõ ràng, đúng thuật ngữ.

Nhiệm vụ: Dịch đoạn văn bản tiếng Anh sang tiếng Việt.

QUY TẮC BẮT BUỘC:
1. Dịch TOÀN BỘ văn bản — không bỏ sót câu, không rút gọn.
2. Giữ nguyên mã kỹ thuật: mã hình (Fig. 23-3), mã linh kiện (CN-19F, SW-7, C-1),
   mã dây màu (WB, LR, BrB), số hiệu phụ tùng (YT04-07175), số mục (23.2.1).
3. Thuật ngữ điện-thủy lực: harness→bộ dây điện, relay→rơ-le, solenoid→van điện từ,
   controller→bộ điều khiển, sensor→cảm biến, ground→mass, connector→đầu nối.
4. KHÔNG lẫn Anh-Việt trong cùng câu (cấm kiểu "PRESSURE cảm biến").
5. Giữ định dạng: xuống dòng (\\n), danh sách, số thứ tự như bản gốc.
6. Nếu cả đoạn chỉ là mã kỹ thuật / part number / figure ref → giữ nguyên.
7. Trả về CHỈ bản dịch tiếng Việt, không có giải thích thêm.
"""

TEXT_BATCH_DIR = "translations/vision_text"


def _load_dotenv(workspace: Path) -> None:
    for candidate in (workspace / ".env", workspace.parent / ".env", Path.cwd() / ".env"):
        if not candidate.exists():
            continue
        for line in candidate.read_text(encoding="utf-8", errors="ignore").splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, v = line.split("=", 1)
            k, v = k.strip(), v.strip().strip('"').strip("'")
            if k and k not in os.environ:
                os.environ[k] = v


def cache_dir(workspace: Path) -> Path:
    d = workspace / TEXT_BATCH_DIR
    d.mkdir(parents=True, exist_ok=True)
    return d


def cache_path(workspace: Path, chunk_id: str) -> Path:
    safe = re.sub(r"[^\w.\-]+", "_", chunk_id)
    return cache_dir(workspace) / f"{safe}.json"


def _is_mixed_en_vi(text: str) -> bool:
    """Phát hiện văn bản vẫn còn tiếng Anh rõ ràng (không phải mã kỹ thuật)."""
    if not text or text.strip().startswith("[GIỮ NGUYÊN"):
        return False
    # Kiểm tra có từ tiếng Anh thường chưa dịch
    en_common = re.findall(
        r'\b(mounting|harness|assembly|switch|motor|sensor|relay|connector|'
        r'circuit|valve|pump|cable|panel|engine|install|check|adjust|error|'
        r'replace|remove|inspect|measure|troubleshooting|used to be|'
        r'part numbers|may be changed)\b',
        text, re.I
    )
    # Phần lowercase rõ ràng tiếng Anh
    if len(en_common) >= 2:
        # Nhưng cho phép nếu đây là cụm kỹ thuật duy nhất (không có nhiều EN)
        if len(en_common) >= 3:
            return True

    # Tìm từ uppercase ASCII (2+ ký tự)
    en_words = re.findall(r'\b[A-Z]{2,}\b', text)
    if not en_words:
        return len(en_common) >= 3

    # Nếu text có ký tự dấu tiếng Việt → chủ yếu là Việt, chỉ flag nếu có từ EN thực sự rõ
    vi_diacritics = re.findall(r'[àáâãèéêìíòóôõùúýăắặặẳẵặđờởặịọụứừặ'
                                r'ẠẢẤẦẨẪẬẮẰẲẴẶẸẺẼẾỀỂỄỆỈỊỌỎỐỒỔỖỘỚỜỞỠỢỤỦỨỪỬỮỰỲỴỶỸ'
                                r'ạảấầẩẫậắằẳẵặẹẻẽếềểễệỉịọỏốồổỗộớờởỡợụủứừửữựỳỵỷỹ'
                                r'ÀÁÂÃÈÉÊÌÍÒÓÔÕÙÚÝ]', text)
    is_mainly_vi = len(vi_diacritics) >= 3

    # Lọc mã kỹ thuật hợp lệ phải giữ nguyên
    keep_pats = [
        r'^[A-Z]{1,3}-\d+[A-Z]?$',   # C-1, SW-3, SE-3, CN-19F
        r'^YT\d{2}',                   # part numbers
        r'^[A-Z][0-9]+[A-Z]?$',        # E1, E18
        r'^S[0-9]YT',                  # serial format
        r'^(AIS|CPU|GPS|LCD|LED|USB|VDC|AC|DC|AM|FM|OK|ON|OFF|YES|NO|NHK|KE|TEST|RUN|'
        r'ENG|SUB|PHD|ABS|RPM|PSI|MPa|kPa|kW|kN|mm|cm|kg|Hz|MHz|GHz|IP|ID|'
        r'AVSS|AVX|GA|GHI|CHU|CHO|CHE|DE|DO|DU|DEN|TU|NHA|BI|KE|TREN|'
        r'LA|LG|LR|LB|LY|LO|LW|WB|WR|WY|WL|WG|WW|BR|BW|BY|BL|BG|BO|BB|'
        r'RW|RB|RG|RY|RL|YR|YB|YG|YW|YL|GR|GB|GY|GW|GL|'
        r'OW|OB|OR|OG|OY|OL|PW|PB|PR|PY|PG|PO)$',
    ]

    tech_en_words = {
        'MOUNTING', 'HARNESS', 'ASSEMBLY', 'SWITCH', 'MOTOR', 'SENSOR',
        'RELAY', 'CONNECTOR', 'CIRCUIT', 'VALVE', 'PUMP', 'CABLE', 'LIGHT',
        'PANEL', 'UPPER', 'ENGINE', 'ERROR', 'CHECK', 'ASSY', 'INSTALL',
        'CAUTION', 'WARNING', 'DANGER', 'PRESS', 'LEVER', 'NOTES', 'EDITION',
        'DATE', 'ISSUE', 'REMARKS', 'APPLICABLE', 'MACHINES', 'TRAVEL',
        'INDICATED', 'INDICATED', 'LOCKLEVER', 'LIMIT', 'BETWEEN', 'HARD',
    }

    real_en = [w for w in en_words if not any(re.match(p, w) for p in keep_pats)]
    has_tech = any(w in tech_en_words for w in real_en)

    if is_mainly_vi:
        # Chủ yếu là tiếng Việt — chỉ flag nếu có TỪ TIẾNG ANH RÕ RÀNG (không phải mã)
        return has_tech or len([w for w in real_en if w in tech_en_words]) >= 1
    else:
        return has_tech or len(real_en) >= 3


def list_pending_text_units(
    workspace: Path,
    *,
    include_mixed: bool = True,
    force: bool = False,
    only: Optional[List[str]] = None,
) -> List[Dict[str, Any]]:
    """Liệt kê tất cả chunk cần (re-)dịch — trả danh sách job dict."""
    from src.translate.chunker import load_chunks

    chunks = load_chunks(workspace)
    jobs: List[Dict[str, Any]] = []

    for chunk in chunks:
        # Lọc theo --only
        if only and chunk.chunk_id not in only:
            continue

        needs_work = False
        reason = ""

        if chunk.status == BlockStatus.PENDING or not chunk.text_vi:
            needs_work = True
            reason = "pending"
        elif include_mixed and _is_mixed_en_vi(chunk.text_vi or ""):
            needs_work = True
            reason = "mixed_en_vi"
        elif force:
            needs_work = True
            reason = "force"

        if not needs_work:
            continue

        # Kiểm tra cache
        cp = cache_path(workspace, chunk.chunk_id)
        cached = cp.exists()
        skip_reason = ""
        if cached and not force:
            prev = json.loads(cp.read_text(encoding="utf-8"))
            if prev.get("text_vi") and not _is_mixed_en_vi(prev["text_vi"]):
                skip_reason = "cached_clean"

        jobs.append({
            "chunk_id": chunk.chunk_id,
            "manual_ref": chunk.manual_ref or "",
            "section": chunk.section or "",
            "block_type": chunk.block_type,
            "pdf_page": chunk.pdf_page,
            "text_en": chunk.text_en,
            "text_vi_current": chunk.text_vi or "",
            "reason": reason,
            "cached": cached,
            "skip_reason": skip_reason,
        })

    return jobs


@dataclass
class TextTranslateResult:
    chunk_id: str
    pdf_page: int
    manual_ref: str
    text_en: str
    text_vi: str = ""
    model: str = "engine"
    error: Optional[str] = None
    skipped: bool = False
    skip_reason: str = ""


def _encode_page_image(source_pdf_path: Path, pdf_page_num: int, max_side: int = 1600) -> Optional[tuple[str, str]]:
    """Render trang nguồn → base64 PNG. Trả (media_type, b64) hoặc None."""
    try:
        import fitz
        doc = fitz.open(str(source_pdf_path))
        if pdf_page_num < 1 or pdf_page_num > len(doc):
            doc.close()
            return None
        page = doc[pdf_page_num - 1]
        mat = fitz.Matrix(120 / 72.0, 120 / 72.0)
        pix = page.get_pixmap(matrix=mat, alpha=False)
        doc.close()
        data = pix.tobytes("png")
        b64 = base64.standard_b64encode(data).decode("ascii")
        return "image/png", b64
    except Exception:
        return None


def _translate_with_llm(
    job: Dict[str, Any],
    *,
    model: str,
    api_key: str,
    provider: str,
    image_b64: Optional[str] = None,
    media_type: str = "image/png",
    timeout: int = 120,
) -> str:
    """Gọi LLM để dịch một chunk văn bản."""
    user_text = (
        f"Mục sổ tay: {job['manual_ref'] or '—'} | Loại block: {job['block_type']} "
        f"| Trang PDF: {job['pdf_page']}\n\n"
        f"=== VĂN BẢN TIẾNG ANH CẦN DỊCH ===\n{job['text_en']}\n\n"
        "Hãy dịch toàn bộ sang tiếng Việt kỹ thuật, giữ đúng cấu trúc dòng."
    )

    if provider == "openai":
        content_blocks: list = [{"type": "text", "text": user_text}]
        if image_b64:
            content_blocks.append({
                "type": "image_url",
                "image_url": {
                    "url": f"data:{media_type};base64,{image_b64}",
                    "detail": "low",
                },
            })
        body = {
            "model": model,
            "temperature": 0.1,
            "messages": [
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": content_blocks},
            ],
        }
        req = urllib.request.Request(
            "https://api.openai.com/v1/chat/completions",
            data=json.dumps(body).encode("utf-8"),
            headers={
                "Content-Type": "application/json",
                "Authorization": f"Bearer {api_key}",
            },
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            payload = json.loads(resp.read().decode("utf-8"))
        return payload["choices"][0]["message"]["content"].strip()

    elif provider == "anthropic":
        content_blocks_a: list = []
        if image_b64:
            content_blocks_a.append({
                "type": "image",
                "source": {
                    "type": "base64",
                    "media_type": media_type,
                    "data": image_b64,
                },
            })
        content_blocks_a.append({"type": "text", "text": user_text})
        body_a = {
            "model": model,
            "max_tokens": 4096,
            "temperature": 0.1,
            "system": SYSTEM_PROMPT,
            "messages": [{"role": "user", "content": content_blocks_a}],
        }
        req_a = urllib.request.Request(
            "https://api.anthropic.com/v1/messages",
            data=json.dumps(body_a).encode("utf-8"),
            headers={
                "Content-Type": "application/json",
                "x-api-key": api_key,
                "anthropic-version": "2023-06-01",
            },
            method="POST",
        )
        with urllib.request.urlopen(req_a, timeout=timeout) as resp_a:
            payload_a = json.loads(resp_a.read().decode("utf-8"))
        parts = payload_a.get("content") or []
        return "".join(p.get("text", "") for p in parts if p.get("type") == "text").strip()

    raise ValueError(f"Unknown provider: {provider}")


def _engine_translate(job: Dict[str, Any], workspace: Path) -> str:
    """Dịch bằng engine rule-based (không cần API)."""
    from src.glossary.manager import load_glossary
    from src.models import Chunk, BlockStatus
    from src.translate.engine import translate_chunk

    glossary = load_glossary(workspace)
    dummy = Chunk(
        chunk_id=job["chunk_id"],
        doc_id="",
        pdf_page=job["pdf_page"],
        manual_ref=job["manual_ref"] or None,
        section=job["section"] or None,
        block_type=job["block_type"],
        text_en=job["text_en"],
        status=BlockStatus.PENDING,
    )
    return translate_chunk(dummy, glossary)


def _save_cache(workspace: Path, result: TextTranslateResult) -> None:
    cp = cache_path(workspace, result.chunk_id)
    cp.write_text(
        json.dumps(asdict(result), ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def process_one_text(
    workspace: Path,
    job: Dict[str, Any],
    *,
    model: str = "gpt-4o",
    force: bool = False,
    source_pdf_path: Optional[Path] = None,
    use_vision_context: bool = True,
    sleep_s: float = 0.5,
) -> TextTranslateResult:
    """Xử lý một chunk văn bản — dịch + ghi cache."""
    chunk_id = job["chunk_id"]

    # Kiểm tra cache
    cp = cache_path(workspace, chunk_id)
    if cp.exists() and not force:
        prev = json.loads(cp.read_text(encoding="utf-8"))
        if prev.get("text_vi") and not _is_mixed_en_vi(prev["text_vi"]):
            return TextTranslateResult(
                chunk_id=chunk_id,
                pdf_page=job["pdf_page"],
                manual_ref=job["manual_ref"],
                text_en=job["text_en"],
                text_vi=prev["text_vi"],
                model=prev.get("model", "cached"),
                skipped=True,
                skip_reason="cached",
            )

    # Không có gì để dịch
    if not (job.get("text_en") or "").strip():
        result = TextTranslateResult(
            chunk_id=chunk_id,
            pdf_page=job["pdf_page"],
            manual_ref=job["manual_ref"],
            text_en=job["text_en"],
            skipped=True,
            skip_reason="empty",
        )
        _save_cache(workspace, result)
        return result

    openai_key = os.environ.get("OPENAI_API_KEY", "").strip()
    anthropic_key = os.environ.get("ANTHROPIC_API_KEY", "").strip()

    try:
        if openai_key or anthropic_key:
            # Render trang nguồn để cung cấp context hình ảnh (tùy chọn)
            image_b64: Optional[str] = None
            media_type = "image/png"
            if use_vision_context and source_pdf_path and source_pdf_path.exists():
                pair = _encode_page_image(source_pdf_path, job["pdf_page"])
                if pair:
                    media_type, image_b64 = pair

            if openai_key:
                provider = "openai"
                used_model = model if model.startswith(("gpt", "o")) else "gpt-4o"
                api_key = openai_key
            else:
                provider = "anthropic"
                used_model = model if model.startswith("claude") else "claude-sonnet-4-20250514"
                api_key = anthropic_key

            text_vi = _translate_with_llm(
                job,
                model=used_model,
                api_key=api_key,
                provider=provider,
                image_b64=image_b64,
                media_type=media_type,
            )
            result = TextTranslateResult(
                chunk_id=chunk_id,
                pdf_page=job["pdf_page"],
                manual_ref=job["manual_ref"],
                text_en=job["text_en"],
                text_vi=text_vi,
                model=used_model,
            )
        else:
            # Không có API key — dùng engine rule-based
            text_vi = _engine_translate(job, workspace)
            result = TextTranslateResult(
                chunk_id=chunk_id,
                pdf_page=job["pdf_page"],
                manual_ref=job["manual_ref"],
                text_en=job["text_en"],
                text_vi=text_vi,
                model="engine",
            )
    except Exception as e:
        result = TextTranslateResult(
            chunk_id=chunk_id,
            pdf_page=job["pdf_page"],
            manual_ref=job["manual_ref"],
            text_en=job["text_en"],
            error=f"{type(e).__name__}: {e}",
        )

    _save_cache(workspace, result)
    if sleep_s > 0 and not result.skipped:
        time.sleep(sleep_s)
    return result


def apply_cached_translations(workspace: Path) -> int:
    """Áp dụng kết quả cache vào chunk JSON files."""
    from src.translate.chunker import load_chunks, save_chunk

    cd = cache_dir(workspace)
    chunks = {c.chunk_id: c for c in load_chunks(workspace)}
    applied = 0

    for json_file in sorted(cd.glob("*.json")):
        if json_file.name == "_manifest.json":
            continue
        try:
            data = json.loads(json_file.read_text(encoding="utf-8"))
        except Exception:
            continue
        chunk_id = data.get("chunk_id")
        text_vi = data.get("text_vi", "").strip()
        if not chunk_id or not text_vi or data.get("error"):
            continue
        if _is_mixed_en_vi(text_vi):
            continue
        chunk = chunks.get(chunk_id)
        if chunk and (not chunk.text_vi or chunk.text_vi != text_vi):
            chunk.text_vi = text_vi
            chunk.status = BlockStatus.DONE
            save_chunk(workspace, chunk)
            applied += 1

    return applied


def run_batch_text(
    workspace: Path,
    *,
    model: str = "gpt-4o",
    force: bool = False,
    include_mixed: bool = True,
    only: Optional[List[str]] = None,
    source_pdf_path: Optional[Path] = None,
    use_vision_context: bool = True,
    sleep_s: float = 0.5,
    progress_cb=None,
    dry_run: bool = False,
) -> List[TextTranslateResult]:
    """Chạy batch dịch văn bản — for-loop một unit một lần, ghi cache ngay."""
    _load_dotenv(workspace)

    jobs = list_pending_text_units(
        workspace,
        include_mixed=include_mixed,
        force=force,
        only=only,
    )

    # Lọc job đã có cache sạch
    active_jobs = [j for j in jobs if not j["skip_reason"]]
    skipped = [j for j in jobs if j["skip_reason"]]

    results: List[TextTranslateResult] = []
    total = len(active_jobs)

    for i, job in enumerate(active_jobs, 1):
        if progress_cb:
            progress_cb(i, total, job)
        if dry_run:
            results.append(TextTranslateResult(
                chunk_id=job["chunk_id"],
                pdf_page=job["pdf_page"],
                manual_ref=job["manual_ref"],
                text_en=job["text_en"],
                skipped=True,
                skip_reason="dry_run",
            ))
            continue
        res = process_one_text(
            workspace,
            job,
            model=model,
            force=force,
            source_pdf_path=source_pdf_path,
            use_vision_context=use_vision_context,
            sleep_s=sleep_s,
        )
        results.append(res)

    # Thêm skipped vào kết quả để báo cáo đầy đủ
    for j in skipped:
        results.append(TextTranslateResult(
            chunk_id=j["chunk_id"],
            pdf_page=j["pdf_page"],
            manual_ref=j["manual_ref"],
            text_en=j["text_en"],
            skipped=True,
            skip_reason=j["skip_reason"],
        ))

    return results
