#!/usr/bin/env python3
"""CI guard: fail if the LLM call log (JSONL) contains any paid, uncached call.

Usage: MANUVI_LLM_CALL_LOG=calls.jsonl pytest ... ; python scripts/check_no_uncached_paid_calls.py calls.jsonl
A missing log file means no calls were recorded and passes.
"""
import json
import sys
from pathlib import Path


def find_violations(path: Path) -> list[dict]:
    if not path.exists():
        return []
    bad = []
    for line in path.read_text().splitlines():
        if line.strip():
            rec = json.loads(line)
            if rec.get("paid") and not rec.get("cache_hit"):
                bad.append(rec)
    return bad


def main(argv: list[str]) -> int:
    import os
    arg = argv[1] if len(argv) > 1 else os.environ.get("MANUVI_LLM_CALL_LOG", "")
    if not arg:
        print("usage: check_no_uncached_paid_calls.py <call_log.jsonl>", file=sys.stderr)
        return 2
    bad = find_violations(Path(arg))
    for rec in bad:
        print(f"PAID UNCACHED CALL: {rec}", file=sys.stderr)
    if bad:
        print(f"{len(bad)} paid uncached call(s) found", file=sys.stderr)
        return 1
    print("ok: no paid uncached calls")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
