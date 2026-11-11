"""One Markdown table row per model for scripts/mac/compare_models.sh (reads report.json written by `manuvi eval run`)."""
from __future__ import annotations

import json
import sys
from pathlib import Path

COLUMNS = ["model", "protected", "numbers", "safety", "kept names", "routed", "repaired", "glossary hit",
           "consistency", "leakage", "s/segment"]


def _pct(m: dict, name: str) -> str:
    v = (m.get(name) or {}).get("value")
    return "n/a" if v is None else f"{v * 100:.1f}%"


def header() -> str:
    return "| " + " | ".join(COLUMNS) + " |\n|" + "---|" * len(COLUMNS)


def row(report: dict, model: str) -> str:
    m = report["metrics"]
    rv = report.get("review") or {}
    n = rv.get("n_cases") or len(report["cases"])
    routed = rv.get("routed_to_review", 0)
    cons = (report.get("consistency") or {}).get("score")
    secs = (report.get("timing") or {}).get("seconds_per_segment")
    cells = [f"`{model}`", _pct(m, "protected_codes"), _pct(m, "number_unit"), _pct(m, "safety_signals"),
             _pct(m, "kept_names"), f"{routed}/{n} ({routed / n * 100:.1f}%)" if n else "n/a", str(rv.get("repaired", 0)),
             _pct(m, "glossary_hit"), "n/a" if cons is None else f"{cons * 100:.1f}%", _pct(m, "leakage"),
             "n/a" if secs is None else f"{secs:.1f}"]
    return "| " + " | ".join(cells) + " |"


def skipped_row(model: str, reason: str) -> str:
    return "| " + " | ".join([f"`{model}`", f"skipped: {reason}", *[""] * (len(COLUMNS) - 2)]) + " |"


def main(argv: list[str]) -> int:
    if len(argv) >= 1 and argv[0] == "header":
        print(header())
        return 0
    if len(argv) == 3 and argv[0] == "row":
        print(row(json.loads(Path(argv[1]).read_text(encoding="utf-8")), argv[2]))
        return 0
    if len(argv) == 3 and argv[0] == "skipped":
        print(skipped_row(argv[1], argv[2]))
        return 0
    print("usage: compare header | row <report.json> <model> | skipped <model> <reason>", file=sys.stderr)
    return 2


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
