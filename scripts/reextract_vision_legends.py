#!/usr/bin/env python3
"""Re-extract vision legend labels for sparse/missing figures.

Real Python for-loop — processes ONE figure at a time, validates label count,
retries once if sparse, logs every step.  NOT an agent-loop that cheats.

Usage:
    PYTHONPATH=. python3 scripts/reextract_vision_legends.py [--doc-id sk70sr-1es] [--force] [--only fig-23-11,fig-23-12]

Requires OPENAI_API_KEY or ANTHROPIC_API_KEY in env or .env file.
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

# ── project root on path ─────────────────────────────────────────────────────
ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT))

from src.translate.vision_legend import (
    MIN_LABELS_THRESHOLD,
    SPARSE_BYTES,
    _load_dotenv,
    _save_result,
    list_figure_jobs,
    process_one,
    result_path,
    vision_dir,
    write_manifest,
)


def audit_existing(ws: Path) -> list[dict]:
    """Return label-count summary for all existing vision legend JSONs."""
    vdir = vision_dir(ws)
    rows = []
    for f in sorted(vdir.glob("fig-*.json")):
        data = json.loads(f.read_text(encoding="utf-8"))
        n = len(data.get("labels") or [])
        img = data.get("image_path", "")
        img_bytes = 0
        if img:
            p = ws / img
            if p.exists():
                img_bytes = p.stat().st_size
        rows.append(
            {
                "figure_id": data.get("figure_id", f.stem),
                "n_labels": n,
                "img_bytes": img_bytes,
                "sparse": img_bytes >= SPARSE_BYTES and n < MIN_LABELS_THRESHOLD,
                "file": f.name,
            }
        )
    return rows


def print_audit(rows: list[dict]) -> None:
    print("\n── Vision Legend Audit ──────────────────────────────────────────")
    print(f"{'figure_id':22s}  {'labels':>6}  {'img_KB':>6}  {'status'}")
    print("-" * 60)
    for row in sorted(rows, key=lambda r: r["n_labels"]):
        flag = "*** SPARSE ***" if row["sparse"] else ("LOW" if row["n_labels"] < 10 else "ok")
        print(
            f"{row['figure_id']:22s}  {row['n_labels']:6d}  "
            f"{row['img_bytes']//1024:6d}  {flag}"
        )
    sparse_count = sum(1 for r in rows if r["sparse"])
    low_count = sum(1 for r in rows if 5 <= r["n_labels"] < 10)
    print(f"\nTotal: {len(rows)} figures | SPARSE: {sparse_count} | LOW: {low_count}")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--doc-id", default="sk70sr-1es")
    parser.add_argument("--model", default="gpt-4o", help="Vision model to use")
    parser.add_argument(
        "--force",
        action="store_true",
        help="Re-extract even figures that already have ≥ threshold labels",
    )
    parser.add_argument(
        "--sparse-only",
        action="store_true",
        default=True,
        help="(default) Only re-extract figures with label count < threshold",
    )
    parser.add_argument(
        "--all",
        dest="process_all",
        action="store_true",
        help="Process ALL ready figures (overrides --sparse-only)",
    )
    parser.add_argument(
        "--only",
        default="",
        help="Comma-separated figure IDs to process (overrides other filters)",
    )
    parser.add_argument("--sleep", type=float, default=1.0, help="Sleep between figures")
    parser.add_argument("--audit-only", action="store_true", help="Just print audit, no extraction")
    args = parser.parse_args()

    ws = ROOT / "workspace" / args.doc_id
    if not ws.exists():
        print(f"ERROR: workspace not found: {ws}", file=sys.stderr)
        return 1

    _load_dotenv(ws)

    # ── Audit existing ────────────────────────────────────────────────────────
    existing_audit = audit_existing(ws)
    print_audit(existing_audit)

    if args.audit_only:
        return 0

    # ── Build job list ────────────────────────────────────────────────────────
    all_jobs = list_figure_jobs(ws)
    ready_jobs = [j for j in all_jobs if not j.get("skip")]

    only_ids: set[str] = set()
    if args.only:
        only_ids = {x.strip() for x in args.only.split(",") if x.strip()}

    if only_ids:
        target_jobs = [j for j in ready_jobs if j["figure_id"] in only_ids]
        print(f"\nFiltered to {len(target_jobs)} figure(s) by --only: {sorted(only_ids)}")
    elif args.process_all or args.force:
        target_jobs = ready_jobs
        print(f"\nProcessing ALL {len(target_jobs)} ready figures (--all/--force)")
    else:
        # default: sparse only
        sparse_ids = {r["figure_id"] for r in existing_audit if r["sparse"]}
        # also include figures with no JSON yet
        existing_ids = {r["figure_id"] for r in existing_audit}
        missing_ids = {j["figure_id"] for j in ready_jobs} - existing_ids
        want_ids = sparse_ids | missing_ids
        target_jobs = [j for j in ready_jobs if j["figure_id"] in want_ids]
        print(
            f"\nTargeting {len(target_jobs)} figures: "
            f"{len(sparse_ids)} sparse + {len(missing_ids)} missing"
        )

    if not target_jobs:
        print("Nothing to do — all figures already meet the label threshold.")
        return 0

    # ── Real for-loop: one figure at a time ──────────────────────────────────
    results_processed = []
    fail_list = []
    print(f"\n── Processing {len(target_jobs)} figure(s) ─────────────────────────────────")

    for i, job in enumerate(target_jobs, 1):
        fid = job["figure_id"]
        img_bytes = job.get("bytes", 0)
        print(f"\n[{i}/{len(target_jobs)}] {fid}  (image {img_bytes // 1024} KB)")

        out_path = result_path(ws, fid)
        if out_path.exists() and not args.force:
            prev_labels = json.loads(out_path.read_text(encoding="utf-8")).get("labels") or []
            if len(prev_labels) >= MIN_LABELS_THRESHOLD or img_bytes < SPARSE_BYTES:
                print(f"  → already has {len(prev_labels)} labels, skipping (use --force to re-run)")
                continue

        t0 = time.time()
        try:
            res = process_one(ws, job, model=args.model, force=True, sleep_s=args.sleep)
        except RuntimeError as e:
            print(f"  ✗ API error: {e}", file=sys.stderr)
            return 1

        elapsed = time.time() - t0
        n = len(res.labels)
        status = getattr(res, "_validation_status", "?")
        icon = "✓" if status == "PASS" else "✗"
        print(f"  {icon} {n} labels in {elapsed:.1f}s  [{status}]")
        if res.error:
            print(f"  ✗ error: {res.error}", file=sys.stderr)

        if status.startswith("FAIL"):
            fail_list.append((fid, n, img_bytes))

        results_processed.append(res)

    # ── Refresh manifest ──────────────────────────────────────────────────────
    print("\n── Refreshing manifest ──────────────────────────────────────────────")
    write_manifest(ws, results_processed)

    # ── Final summary ─────────────────────────────────────────────────────────
    print(f"\n═══ DONE: {len(results_processed)} figure(s) processed ═══════════════")
    if fail_list:
        print(f"  {len(fail_list)} still below threshold after retry:")
        for fid, n, sz in fail_list:
            print(f"    {fid}: {n} labels  ({sz // 1024} KB image)")
        print("  → Consider manual extraction or check if image is readable.")
        return 2
    else:
        print("  All processed figures meet label threshold ✓")
        return 0


if __name__ == "__main__":
    sys.exit(main())
