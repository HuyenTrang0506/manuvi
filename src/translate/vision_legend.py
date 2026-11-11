"""Batch vision legends: đọc từng hình sơ đồ → bảng EN→VI.

Chạy toàn bộ figures (resume được). Kết quả:
  workspace/<doc>/translations/vision_legends/<figure_id>.json
  workspace/<doc>/translations/vision_legends/_manifest.json

Mọi lệnh gọi model đi qua LLM gateway (alias `vision`, data_class=customer): chỉ model local/private
(vd. Ollama) được nhận ảnh; gateway từ chối free hosted API (ADR-010). Module này không giữ API key
và không import SDK/HTTP client của model.
"""

from __future__ import annotations

import base64
import hashlib
import json
import re
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from src.ingest.parser import load_document
from src.llm_gateway import DataClassViolation, Gateway, LLMRequest, NotConfigured

PROMPT_VERSION = "vision-legend-v2"

MIN_LABELS_THRESHOLD = 8          # images > SPARSE_BYTES must have ≥ this many labels
SPARSE_BYTES = 50_000             # 50 KB — below this, few labels are acceptable

SYSTEM_PROMPT = """Bạn là kỹ thuật viên điện máy công trình + biên dịch viên EN→VI.
Nguồn: Kobelco SK70SR-1ES Shop Manual — chương Electrical / Troubleshooting (Electric).
Độc giả: thợ sửa máy, cần nghĩa tiếng Việt rõ, đúng thuật ngữ điện-thủy lực.

Nhiệm vụ: ĐỌC ảnh sơ đồ/hình lắp ráp và trích MỌI chữ tiếng Anh nhìn thấy trên hình
(nhãn linh kiện, chú thích, mũi tên TO/FROM, TAPING, tên cụm, tiêu đề nhỏ…),
rồi dịch nghĩa sang tiếng Việt.

Quy tắc bắt buộc:
1. Chỉ lấy chữ THỰC SỰ có trên ảnh — không bịa thêm.
2. Giữ nguyên mã kỹ thuật: CN-19F, SW-7, SE-3, R-8, C-1, màu dây (WB, LR, BrB…),
   part number (YT…), số chân pin, kích thước connector (4FA-CNA, 20FS-HM090S…).
3. Dịch phần mô tả tiếng Anh: SPEAKER (R) → Loa (phải); WIPER MOTOR → Mô-tơ gạt mưa;
   RED TAPING → Quấn băng keo đỏ; TO CAB → Đến cabin; CAB WORK LIGHT → Đèn làm việc cabin.
4. Không dịch word-by-word lẫn Anh-Việt (cấm kiểu "PRESSURE cảm biến").
5. Bỏ qua: số trang footer, copyright Kobelco, watermark mờ không đọc được.
6. Trả JSON THUẦN (không markdown fence), schema:
{
  "figure_summary_vi": "một câu mô tả hình",
  "labels": [
    {"en": "text gốc trên ảnh", "vi": "nghĩa tiếng Việt", "kind": "component|callout|title|other"}
  ]
}
7. Nếu ảnh gần như trống / không có chữ đọc được: labels = [].
"""

RICH_RETRY_PROMPT_SUFFIX = """
⚠️  CẢNH BÁO: Lần trích trước cho ÍT NHÃN hơn ngưỡng tối thiểu ({threshold}).
Lần này hãy QUÉT KỸ TOÀN BỘ ảnh từ góc trên-trái đến góc dưới-phải:
- Mọi chú thích hướng dẫn kỹ thuật (dù nhỏ): TAPING, WIRE IT AS, ATTACH, REMOVE, FIRST…
- Tiêu đề section/hình (SECTION AA, Fig. 23-xx, VIEW FROM…)
- Bảng CONNECTORS SELECTION TABLE — từng ô: CONNECTOR NAME, MANUFUC., HOUSING, TERMINAL
- Chiều dài dây / số hiệu đầu nối (2100, 174354-2…)
- Nhãn cụm: MAIN HARNESS, ENGINE ROOM LIGHT, TO MAIN HARNESS…
- Callout số (8, 9, 10) kèm ngữ cảnh nếu có mũi tên chỉ vào linh kiện
- VIEW/SECTION labels, mọi hướng dẫn IN HOA
Không được bỏ sót chữ nào có nghĩa kỹ thuật cho thợ sửa máy.
"""


@dataclass
class VisionLabel:
    en: str
    vi: str
    kind: str = "other"


@dataclass
class VisionLegendResult:
    figure_id: str
    figure_number: str
    caption_en: str
    image_path: str
    model: str
    figure_summary_vi: str = ""
    labels: List[VisionLabel] = field(default_factory=list)
    error: Optional[str] = None
    skipped: bool = False
    skip_reason: str = ""


def vision_dir(workspace: Path) -> Path:
    d = workspace / "translations" / "vision_legends"
    d.mkdir(parents=True, exist_ok=True)
    return d


def result_path(workspace: Path, figure_id: str) -> Path:
    safe = re.sub(r"[^\w.\-]+", "_", figure_id)
    return vision_dir(workspace) / f"{safe}.json"


def list_figure_jobs(workspace: Path, *, min_bytes: int = 8_000) -> List[Dict[str, Any]]:
    """Mọi figure có ảnh đủ lớn — mỗi figure_id một job (ảnh lớn nhất)."""
    doc = load_document(workspace)
    jobs: List[Dict[str, Any]] = []
    for fid, fig in sorted(doc.figures_registry.items()):
        best: Optional[Tuple[int, Path, str]] = None
        for rel in fig.image_paths or []:
            fp = workspace / rel
            if not fp.exists():
                continue
            sz = fp.stat().st_size
            if best is None or sz > best[0]:
                best = (sz, fp, rel)
        if best is None:
            jobs.append(
                {
                    "figure_id": fid,
                    "figure_number": fig.figure_number or "",
                    "caption_en": fig.caption_en or "",
                    "image_path": "",
                    "bytes": 0,
                    "skip": True,
                    "skip_reason": "missing_image",
                }
            )
            continue
        sz, fp, rel = best
        skip = sz < min_bytes
        jobs.append(
            {
                "figure_id": fid,
                "figure_number": fig.figure_number or "",
                "caption_en": fig.caption_en or "",
                "image_path": rel,
                "bytes": sz,
                "skip": skip,
                "skip_reason": "image_too_small" if skip else "",
            }
        )
    return jobs


def _encode_image_jpeg(path: Path, max_side: int = 2048, quality: int = 85) -> Tuple[str, str]:
    """Trả (media_type, base64). Resize bằng PyMuPDF nếu cạnh dài > max_side."""
    import fitz

    pix = fitz.Pixmap(str(path))
    if pix.n > 3:
        pix = fitz.Pixmap(fitz.csRGB, pix)
    w, h = pix.width, pix.height
    data: bytes
    tmp = path.parent / f".vision_{path.stem}.png"
    try:
        if max(w, h) > max_side:
            scale = max_side / float(max(w, h))
            doc = fitz.open()
            page = doc.new_page(width=w * scale, height=h * scale)
            page.insert_image(page.rect, filename=str(path))
            pix = page.get_pixmap(alpha=False)
            pix.save(str(tmp))
            doc.close()
        else:
            pix.save(str(tmp))
        data = tmp.read_bytes()
        # vẫn quá lớn → thu nhỏ thêm
        if len(data) > 4_500_000:
            doc = fitz.open()
            page = doc.new_page(width=1400, height=max(900, int(1400 * h / max(w, 1))))
            page.insert_image(page.rect, filename=str(path))
            pix = page.get_pixmap(alpha=False)
            pix.save(str(tmp))
            doc.close()
            data = tmp.read_bytes()
    finally:
        if tmp.exists():
            tmp.unlink()
    b64 = base64.standard_b64encode(data).decode("ascii")
    return "image/png", b64


def _parse_json_content(text: str) -> Dict[str, Any]:
    text = text.strip()
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?\s*", "", text)
        text = re.sub(r"\s*```$", "", text)
    return json.loads(text)


def _gateway_vision(gateway: Gateway, *, figure_id: str, image_b64: str, media_type: str, user_text: str,
                    job_id: str) -> Tuple[Dict[str, Any], str]:
    """One vision call through the gateway. Returns (parsed JSON, model id actually used)."""
    req = LLMRequest(
        messages=[
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": [
                {"type": "text", "text": user_text + "\n\nTrả JSON thuần theo schema đã nêu."},
                {"type": "image_url", "image_url": {"url": f"data:{media_type};base64,{image_b64}"}},
            ]},
        ],
        response_format={"type": "json_object"},
        model_alias="vision",
        data_class="customer",
        job_id=job_id,
        target_id=figure_id,
        prompt_version=PROMPT_VERSION,
        source_hash=hashlib.sha256(image_b64.encode("ascii")).hexdigest(),
        temperature=0.1,
        max_tokens=8192,
    )
    out = gateway.complete(req)
    return _parse_json_content(out.text), out.model_id


def _user_prompt(job: Dict[str, Any], *, retry: bool = False) -> str:
    base = (
        f"Hình: {job['figure_id']} | Số hình sổ tay: {job.get('figure_number') or '—'} | "
        f"Caption EN: {job.get('caption_en') or '(không có)'}\n"
        f"File ảnh: {job.get('image_path')}\n\n"
        "Hãy đọc kỹ toàn bộ ảnh (cả góc, chú thích nhỏ, TAPING, TO/FROM, tên linh kiện, "
        "tiêu đề hình, chú thích hướng dẫn kỹ thuật, bảng đầu nối, chiều dài dây). "
        "Liệt kê ĐỦ TOÀN BỘ nhãn chữ tiếng Anh nhìn thấy và dịch VI. "
        "Ưu tiên nhãn giúp thợ tra cứu khi nhìn sơ đồ."
    )
    if retry:
        base += RICH_RETRY_PROMPT_SUFFIX.format(threshold=MIN_LABELS_THRESHOLD)
    return base


def process_one(
    workspace: Path,
    job: Dict[str, Any],
    *,
    model: str = "",
    force: bool = False,
    sleep_s: float = 0.5,
    gateway: Optional[Gateway] = None,
) -> VisionLegendResult:
    """`model` is only a label for cached/failed results; the model used is the gateway's `vision` alias."""
    gateway = gateway or Gateway()
    fid = job["figure_id"]
    out = result_path(workspace, fid)
    if out.exists() and not force:
        prev = json.loads(out.read_text(encoding="utf-8"))
        labels = [VisionLabel(**x) for x in prev.get("labels") or []]
        return VisionLegendResult(
            figure_id=fid,
            figure_number=prev.get("figure_number") or job.get("figure_number") or "",
            caption_en=prev.get("caption_en") or job.get("caption_en") or "",
            image_path=prev.get("image_path") or job.get("image_path") or "",
            model=prev.get("model") or model,
            figure_summary_vi=prev.get("figure_summary_vi") or "",
            labels=labels,
            skipped=True,
            skip_reason="cached",
        )

    if job.get("skip"):
        res = VisionLegendResult(
            figure_id=fid,
            figure_number=job.get("figure_number") or "",
            caption_en=job.get("caption_en") or "",
            image_path=job.get("image_path") or "",
            model=model,
            skipped=True,
            skip_reason=job.get("skip_reason") or "skip",
        )
        _save_result(workspace, res)
        return res

    img_path = workspace / job["image_path"]
    img_bytes = job.get("bytes", img_path.stat().st_size if img_path.exists() else 0)
    media_type, b64 = _encode_image_jpeg(img_path)

    job_id = f"vision-legend:{workspace.name}"

    def _call_vision(user_text: str) -> tuple:
        """Returns (data_dict, used_model)."""
        return _gateway_vision(gateway, figure_id=fid, image_b64=b64, media_type=media_type,
                               user_text=user_text, job_id=job_id)

    def _parse_labels(data: Dict[str, Any]) -> List[VisionLabel]:
        out: List[VisionLabel] = []
        for row in data.get("labels") or []:
            en = str(row.get("en") or "").strip()
            vi = str(row.get("vi") or "").strip()
            if not en or not vi:
                continue
            out.append(VisionLabel(en=en, vi=vi, kind=str(row.get("kind") or "other")))
        return out

    try:
        # ── First pass ──────────────────────────────────────────────────────────
        user_text = _user_prompt(job, retry=False)
        data, used_model = _call_vision(user_text)
        labels = _parse_labels(data)

        # ── Validation gate: retry once if image is substantial but labels sparse ─
        sparse = img_bytes >= SPARSE_BYTES and len(labels) < MIN_LABELS_THRESHOLD
        if sparse:
            print(
                f"  [WARN] {fid}: {len(labels)} labels < threshold {MIN_LABELS_THRESHOLD} "
                f"(image {img_bytes // 1024} KB) — retrying with richer prompt …"
            )
            if sleep_s > 0:
                time.sleep(min(sleep_s, 1.0))
            data2, _ = _call_vision(_user_prompt(job, retry=True))
            labels2 = _parse_labels(data2)
            if len(labels2) > len(labels):
                print(f"  [RETRY OK] {fid}: {len(labels)} → {len(labels2)} labels")
                labels = labels2
                data = data2
            else:
                print(f"  [RETRY SAME] {fid}: still {len(labels2)} labels after retry")

        validation_status = "PASS"
        if img_bytes >= SPARSE_BYTES and len(labels) < MIN_LABELS_THRESHOLD:
            validation_status = f"FAIL_SPARSE({len(labels)}<{MIN_LABELS_THRESHOLD})"

        res = VisionLegendResult(
            figure_id=fid,
            figure_number=job.get("figure_number") or "",
            caption_en=job.get("caption_en") or "",
            image_path=job.get("image_path") or "",
            model=used_model,
            figure_summary_vi=str(data.get("figure_summary_vi") or ""),
            labels=labels,
        )
        # Attach validation status as extra field via manual payload merge
        res._validation_status = validation_status  # type: ignore[attr-defined]
    except (DataClassViolation, NotConfigured):
        raise  # policy/setup problems are never recorded as a figure error
    except Exception as e:
        res = VisionLegendResult(
            figure_id=fid,
            figure_number=job.get("figure_number") or "",
            caption_en=job.get("caption_en") or "",
            image_path=job.get("image_path") or "",
            model=model,
            error=f"{type(e).__name__}: {e}",
        )

    _save_result(workspace, res)
    if sleep_s > 0 and not res.skipped:
        time.sleep(sleep_s)
    return res


def _save_result(workspace: Path, res: VisionLegendResult) -> None:
    path = result_path(workspace, res.figure_id)
    payload = {
        "figure_id": res.figure_id,
        "figure_number": res.figure_number,
        "caption_en": res.caption_en,
        "image_path": res.image_path,
        "model": res.model,
        "figure_summary_vi": res.figure_summary_vi,
        "labels": [asdict(x) for x in res.labels],
        "error": res.error,
        "skipped": res.skipped,
        "skip_reason": res.skip_reason,
        "validation_status": getattr(res, "_validation_status", "PASS"),
    }
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def write_manifest(workspace: Path, results: List[VisionLegendResult]) -> Path:
    man = vision_dir(workspace) / "_manifest.json"
    rows = []
    for r in results:
        rows.append(
            {
                "figure_id": r.figure_id,
                "figure_number": r.figure_number,
                "n_labels": len(r.labels),
                "validation_status": getattr(r, "_validation_status", "PASS"),
                "error": r.error,
                "skipped": r.skipped,
                "skip_reason": r.skip_reason,
                "file": result_path(workspace, r.figure_id).name,
            }
        )
    man.write_text(json.dumps(rows, ensure_ascii=False, indent=2), encoding="utf-8")

    # Print sparse-figure summary to stdout for visibility
    sparse = [row for row in rows if row["validation_status"].startswith("FAIL")]
    if sparse:
        print(f"\n⚠  {len(sparse)} figure(s) still below label threshold after retry:")
        for row in sparse:
            print(f"   {row['figure_id']:20s}  {row['n_labels']:3d} labels  {row['file']}")
    else:
        print(f"\n✓ All processed figures passed label validation (≥{MIN_LABELS_THRESHOLD} labels for images ≥{SPARSE_BYTES//1000}KB)")

    return man


def load_vision_legend(workspace: Path, figure_id: str) -> Optional[Dict[str, Any]]:
    path = result_path(workspace, figure_id)
    if not path.exists():
        return None
    data = json.loads(path.read_text(encoding="utf-8"))
    if data.get("error") and not data.get("labels"):
        return None
    return data


# Real numbered figures only (skip orphans like fig-?-2 / fig-_-2).
_REAL_FIG_ID = re.compile(r"^fig-\d+-\d+$", re.I)
_PAGE_TOKEN = re.compile(r"(?:^|/)[^/]*_p(\d+)_(?:fig|full)", re.I)


def page_token_from_path(image_path: str) -> Optional[str]:
    """Extract PDF page stem from asset path, e.g. sec23_p201_fig1.png → '201'."""
    m = _PAGE_TOKEN.search((image_path or "").replace("\\", "/"))
    return m.group(1) if m else None


def _vision_page_token(workspace: Path, figure_id: str, data: Optional[Dict[str, Any]] = None) -> Optional[str]:
    data = data if data is not None else load_vision_legend(workspace, figure_id)
    if data:
        tok = page_token_from_path(str(data.get("image_path") or ""))
        if tok:
            return tok
    try:
        doc = load_document(workspace)
        entry = doc.figures_registry.get(figure_id)
        if not entry:
            return None
        for rel in entry.image_paths or []:
            tok = page_token_from_path(rel)
            if tok:
                return tok
        if entry.pdf_pages:
            return str(entry.pdf_pages[0])
    except Exception:
        pass
    return None


def list_co_page_vision_ids(workspace: Path, figure_id: str) -> List[str]:
    """Real-numbered vision legends that share the same source-page image stem.

    Used when multiple figures live on one manual page (e.g. Fig 23-11 + 23-12
    on page 23-15 / PDF 201) but only one has a figure block in assemble.
    """
    if not _REAL_FIG_ID.match(figure_id):
        return [figure_id] if result_path(workspace, figure_id).exists() else []

    token = _vision_page_token(workspace, figure_id)
    if not token:
        return [figure_id] if result_path(workspace, figure_id).exists() else []

    ids: List[str] = []
    for path in sorted(vision_dir(workspace).glob("fig-*.json")):
        fid = path.stem
        if not _REAL_FIG_ID.match(fid):
            continue
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            continue
        if data.get("error") and not data.get("labels"):
            continue
        if _vision_page_token(workspace, fid, data) == token:
            ids.append(fid)

    if figure_id not in ids and result_path(workspace, figure_id).exists():
        ids.append(figure_id)

    def _sort_key(fid: str) -> Tuple[int, int]:
        m = re.match(r"fig-(\d+)-(\d+)$", fid, re.I)
        return (int(m.group(1)), int(m.group(2))) if m else (9999, 9999)

    return sorted(set(ids), key=_sort_key)


def load_merged_vision_legend(workspace: Path, figure_id: str) -> Optional[Dict[str, Any]]:
    """Load vision legend, merging co-page sibling labels (dedupe by EN key).

    Orphan figures (fig-?-N) stay single-file. Real figures on a shared page
    (e.g. fig-23-11 + fig-23-12) yield one combined label list so assemble
    shows the full callout/connector table after the page image.
    """
    if not _REAL_FIG_ID.match(figure_id):
        return load_vision_legend(workspace, figure_id)

    ids = list_co_page_vision_ids(workspace, figure_id)
    if len(ids) <= 1:
        return load_vision_legend(workspace, figure_id)

    primary = load_vision_legend(workspace, figure_id)
    seen: set[str] = set()
    merged: List[Dict[str, Any]] = []
    summaries: List[str] = []
    for fid in ids:
        data = load_vision_legend(workspace, fid)
        if not data:
            continue
        summary = str(data.get("figure_summary_vi") or "").strip()
        if summary:
            summaries.append(summary)
        for lb in data.get("labels") or []:
            en = str(lb.get("en") or "").strip()
            if not en:
                continue
            key = en.casefold()
            if key in seen:
                continue
            seen.add(key)
            merged.append(lb)

    if not merged:
        return primary

    out = dict(primary or load_vision_legend(workspace, ids[0]) or {})
    out["labels"] = merged
    out["merged_from"] = ids
    if summaries:
        out["figure_summary_vi"] = " ".join(summaries)
    return out


def run_batch(
    workspace: Path,
    *,
    model: str = "",
    force: bool = False,
    only: Optional[List[str]] = None,
    sleep_s: float = 0.8,
    min_bytes: int = 8_000,
    progress_cb=None,
    gateway: Optional[Gateway] = None,
) -> List[VisionLegendResult]:
    gateway = gateway or Gateway()
    jobs = list_figure_jobs(workspace, min_bytes=min_bytes)
    if only:
        want = set(only)
        jobs = [j for j in jobs if j["figure_id"] in want]
    results: List[VisionLegendResult] = []
    total = len(jobs)
    for i, job in enumerate(jobs, 1):
        if progress_cb:
            progress_cb(i, total, job)
        res = process_one(workspace, job, model=model, force=force, sleep_s=sleep_s, gateway=gateway)
        results.append(res)
    write_manifest(workspace, results)
    return results
