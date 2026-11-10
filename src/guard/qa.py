"""BL-US-18: deterministic QA checks plus a capped fix loop."""
from __future__ import annotations

import unicodedata
from collections import Counter
from dataclasses import dataclass, field
from typing import Callable

from .masking import PatternSpec, check_placeholders, load_patterns, mask, unmask
from .models import FixResult, Issue, Severity
from .numbers import diff_numbers
from .script import script_issues
from .signal_words import load_signal_table, signal_mismatches

MAX_FIX_ATTEMPTS = 2


@dataclass
class QAContext:
    patterns: list[PatternSpec] = field(default_factory=load_patterns)
    signal_table: dict[str, str] = field(default_factory=load_signal_table)
    # If set, `target` is a masked translation using this mapping.
    mapping: dict[str, str] | None = None


def qa_check(source: str, target: str, ctx: QAContext | None = None) -> list[Issue]:
    ctx = ctx or QAContext()
    target = unicodedata.normalize("NFC", target)
    issues: list[Issue] = []
    if ctx.mapping is not None:
        rep = check_placeholders(target, ctx.mapping)
        if rep.dropped:
            issues.append(Issue(rule_id="PH001", severity=Severity.error, message=f"placeholders dropped: {rep.dropped}",
                                fix_hint="Re-insert the dropped placeholders exactly once.", detail={"dropped": rep.dropped}))
        if rep.duplicated:
            issues.append(Issue(rule_id="PH002", severity=Severity.error, message=f"placeholders duplicated: {rep.duplicated}",
                                fix_hint="Keep each placeholder exactly once.", detail={"duplicated": rep.duplicated}))
        if rep.unknown:
            issues.append(Issue(rule_id="PH003", severity=Severity.error, message=f"unknown placeholders: {rep.unknown}",
                                fix_hint="Remove invented placeholders.", detail={"unknown": rep.unknown}))
        if rep.reordered:
            issues.append(Issue(rule_id="PH004", severity=Severity.warn, message="placeholders reordered",
                                fix_hint="Check the order of codes against the source."))
        target = unmask(target, ctx.mapping)

    s_codes = Counter(mask(source, ctx.patterns)[1].values())
    t_codes = Counter(mask(target, ctx.patterns)[1].values())
    lost = sorted((s_codes - t_codes).elements())
    extra = sorted((t_codes - s_codes).elements())
    if lost or extra:
        issues.append(Issue(rule_id="PROT001", severity=Severity.error,
                            message=f"protected codes changed: missing={lost} extra={extra}",
                            fix_hint="Copy protected codes from the source byte for byte.",
                            detail={"missing": lost, "extra": extra}))

    miss, add = diff_numbers(source, target, ctx.patterns)
    if miss or add:
        issues.append(Issue(rule_id="NUM001", severity=Severity.error,
                            message=f"numbers differ: missing={miss} extra={add}",
                            fix_hint="Numbers, decimal separators and units keep the source format.",
                            detail={"missing": miss, "extra": add}))

    for en, vi, sc, tc in signal_mismatches(source, target, ctx.signal_table):
        issues.append(Issue(rule_id="SIG001", severity=Severity.error,
                            message=f"signal word {en}: source x{sc}, target '{vi}' x{tc}",
                            fix_hint=f"Use exactly '{vi}' for {en}.", detail={"en": en, "vi": vi}))

    issues.extend(script_issues(source, target))

    if not issues:
        issues.append(Issue(rule_id="OK", severity=Severity.ok, message="all deterministic checks passed"))
    return issues


def _has_errors(issues: list[Issue]) -> bool:
    return any(i.severity == Severity.error for i in issues)


def fix_loop(source: str, target: str, ctx: QAContext | None,
             fixer: Callable[[str, list[Issue]], str],
             max_attempts: int = MAX_FIX_ATTEMPTS) -> FixResult:
    """Run QA; on errors call fixer(target, issues) at most 2 times, then flag needs_review."""
    ctx = ctx or QAContext()
    max_attempts = min(max_attempts, MAX_FIX_ATTEMPTS)
    issues = qa_check(source, target, ctx)
    attempts = 0
    while _has_errors(issues) and attempts < max_attempts:
        target = fixer(target, [i for i in issues if i.severity != Severity.ok])
        attempts += 1
        issues = qa_check(source, target, ctx)
    return FixResult(target=target, issues=issues, attempts=attempts, needs_review=_has_errors(issues))
