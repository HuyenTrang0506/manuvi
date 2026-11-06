"""CPU-only OCR label extraction using RapidOCR (ONNX runtime).

Supplements vision legend JSONs with text found on diagram images.
Vision translation quality is preferred; OCR seeds labels for figures
without vision results or adds missed on-image strings.

CLI integration:
  python -m src.cli ocr-supplement            # all figures
  python -m src.cli ocr-supplement --only fig-23-6,fig-23-2
  python -m src.cli ocr-supplement --force    # overwrite existing OCR entries

Module entry-points:
  ocr_figure(img_path)             -> list[str]  raw OCR texts (conf-filtered)
  ocr_labels_for_figure(img_path)  -> list[dict] {en, vi, kind}
  supplement_vision(workspace, figure_id)  -> updated vision JSON dict
  run_ocr_supplement_batch(workspace)      -> list[dict] updated manifests
"""
from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any, Dict, List, Optional

_NOISE_RE = re.compile(
    r"^[\d\s\-_.:,/\\()\[\]{}|@#$%&*+~^<>=!?'\"]+$"
)
_CONNECTOR_RE = re.compile(r"^CN[A-Z]?-\d+[A-Z]*$", re.I)
_PART_RE = re.compile(r"^YT[0-9A-Z\-]+$", re.I)
_WIRE_COLOR_RE = re.compile(r"^[WRGYBLO]{1,2}[A-Z]{0,3}$")
_COMPONENT_WORDS = re.compile(
    r"\b(RELAY|MOTOR|SENSOR|SWITCH|LIGHT|HARNESS|ASSY|SOLENOID"
    r"|CONTROLLER|ECU|ECM|PUMP|VALVE|BATTERY|ALTERNATOR"
    r"|STARTER|FUSE|BREAKER|DIODE|RESISTOR|SPEAKER|WIPER)\b",
    re.I,
)
_CALLOUT_WORDS = re.compile(r"\b(TO|FROM|TAPING|GROUND|SUPPLY|CABLE)\b", re.I)


def _classify_label(text: str) -> str:
    t = text.strip().upper()
    if _CONNECTOR_RE.match(t):
        return "connector"
    if _PART_RE.match(t):
        return "part_number"
    if _WIRE_COLOR_RE.match(t) and len(t) <= 4:
        return "wire"
    if _COMPONENT_WORDS.search(t):
        return "component"
    if _CALLOUT_WORDS.search(t):
        return "callout"
    if t == t.upper() and len(t) > 8 and re.search(r"[A-Z]{3,}", t):
        return "title"
    return "other"


def _is_noise(text: str) -> bool:
    t = text.strip()
    if len(t) <= 2:
        return True
    if _NOISE_RE.match(t):
        return True
    # Page-footer / part number only patterns that add no meaning
    if re.match(r"^\d+\.\d+$", t):  # e.g. "23.2"
        return True
    if re.match(r"^\d{4,}$", t):  # long bare digits
        return True
    return False


def _translate_ocr(text: str) -> str:
    """Translate via phrase maps + label_translator. Returns "" if untranslated."""
    from src.translate.harness_callouts import HARNESS_PHRASES

    key = text.strip().lower()
    if key in HARNESS_PHRASES:
        return HARNESS_PHRASES[key]
    # substring match for callout phrases
    for phrase, vi in HARNESS_PHRASES.items():
        if len(phrase) > 4 and phrase in key:
            return vi
    # label_translator requires a Glossary object; use empty glossary for OCR
    try:
        from src.translate.label_translator import translate_label_text
        from src.glossary.manager import Glossary

        result = translate_label_text(text, Glossary())
        if result and result.strip() != text.strip():
            return result
    except Exception:
        pass
    return ""


def ocr_figure(
    img_path: "str | Path",
    min_conf: float = 0.75,
) -> List[str]:
    """Run RapidOCR on *img_path* and return confident text strings.

    Returns an empty list if RapidOCR is not installed or the image is empty.
    """
    try:
        from rapidocr_onnxruntime import RapidOCR
    except ImportError:
        return []

    ocr = RapidOCR()
    result, _elapse = ocr(str(img_path))
    if not result:
        return []
    texts: List[str] = []
    seen: set = set()
    for det in result:
        text, conf = det[1], det[2]
        text = text.strip()
        if conf < min_conf:
            continue
        if _is_noise(text):
            continue
        # Light dedup (identical strings)
        key = re.sub(r"\s+", " ", text.upper())
        if key in seen:
            continue
        seen.add(key)
        texts.append(text)
    return texts


def ocr_labels_for_figure(
    img_path: "str | Path",
    min_conf: float = 0.75,
) -> List[Dict[str, Any]]:
    """OCR an image and return labels ``{en, vi, kind}`` ready for vision JSON.

    Labels where no VI translation is available are included with ``vi=""``
    so callers can decide whether to include them in the output (the assemble
    path filters rows without diacritics, so untranslated labels are silently
    dropped from the PDF legend — consistent with existing project rules).
    """
    texts = ocr_figure(img_path, min_conf=min_conf)
    labels: List[Dict[str, Any]] = []
    for t in texts:
        vi = _translate_ocr(t)
        kind = _classify_label(t)
        labels.append({"en": t, "vi": vi, "kind": kind})
    return labels


def supplement_vision(
    workspace: Path,
    figure_id: str,
    *,
    min_conf: float = 0.75,
    force: bool = False,
) -> Optional[Dict[str, Any]]:
    """Merge OCR labels into the vision JSON for *figure_id*.

    - Vision labels (already translated) are preserved as-is.
    - OCR labels whose EN text is not already present (case-insensitive) are
      appended at the end of the ``labels`` list.
    - Sets ``ocr_supplemented: true`` in the JSON so callers can tell.
    - Returns the updated dict or None if the figure JSON does not exist.
    """
    from src.translate.vision_legend import result_path

    jpath = result_path(workspace, figure_id)
    if not jpath.exists():
        return None

    data: Dict[str, Any] = json.loads(jpath.read_text(encoding="utf-8"))
    if data.get("ocr_supplemented") and not force:
        return data  # already done

    img_rel = data.get("image_path") or ""
    if not img_rel:
        return data
    img_abs = workspace / img_rel
    if not img_abs.exists():
        return data

    # On force re-run, trim back to original Vision labels before re-supplementing
    prev_added = data.get("ocr_labels_added", 0)
    if force:
        # Remove any previously OCR-appended labels (tagged with source="ocr")
        data["labels"] = [
            lb for lb in data.get("labels", []) if lb.get("source") != "ocr"
        ]

    ocr_labs = ocr_labels_for_figure(img_abs, min_conf=min_conf)
    if not ocr_labs:
        return data

    existing_en = {
        re.sub(r"\s+", " ", lb.get("en", "").strip().upper())
        for lb in (data.get("labels") or [])
    }

    added = 0
    for lab in ocr_labs:
        key = re.sub(r"\s+", " ", lab["en"].upper())
        if key in existing_en:
            continue
        # Skip connectors/wire-colors from OCR supplement (vision already tracks them
        # and OCR produces many noisy variants for pin tables)
        if lab["kind"] in ("connector", "wire", "part_number"):
            continue
        # Only include if there is a clean Vietnamese translation
        vi = lab.get("vi", "").strip()
        if not vi:
            continue
        # Drop if translation still contains long English words (mixed/failed translation)
        if re.search(r"[A-Za-z]{5,}", vi):
            continue
        # Drop if vi is identical to en (pass-through / untranslated)
        if vi.upper() == lab["en"].strip().upper():
            continue
        data.setdefault("labels", []).append({**lab, "source": "ocr"})
        existing_en.add(key)
        added += 1

    data["ocr_supplemented"] = True
    data["ocr_labels_added"] = added
    jpath.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    return data


def run_ocr_supplement_batch(
    workspace: Path,
    *,
    only: Optional[List[str]] = None,
    force: bool = False,
    min_conf: float = 0.75,
    progress_cb=None,
) -> List[Dict[str, Any]]:
    """Run OCR supplement on all vision legend JSONs in *workspace*.

    Skips figures with no JSON, no image, or already supplemented (unless
    ``force=True``).  Returns a list of result dicts (figure_id, added, status).
    """
    from src.translate.vision_legend import vision_dir

    vdir = vision_dir(workspace)
    jsons = sorted(p for p in vdir.glob("*.json") if not p.name.startswith("_"))
    if only:
        want = set(only)
        jsons = [p for p in jsons if p.stem in want]

    results = []
    for i, jpath in enumerate(jsons, 1):
        fid = jpath.stem
        if progress_cb:
            progress_cb(i, len(jsons), fid)
        try:
            updated = supplement_vision(
                workspace, fid, min_conf=min_conf, force=force
            )
            added = (updated or {}).get("ocr_labels_added", 0)
            results.append({"figure_id": fid, "added": added, "status": "ok"})
        except Exception as exc:
            results.append({"figure_id": fid, "added": 0, "status": str(exc)})
    return results
