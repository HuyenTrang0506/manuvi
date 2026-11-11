"""Format-level QA on top of the guard: inline tags, and the founder rule 'no English left except names/codes'."""
from __future__ import annotations

from ..guard.models import Issue, Severity
from .tags import strip_tags, tag_issues, unesc


def leakage_issue(source: str, target: str) -> list[Issue]:
    """LEAK001 (warn): untranslated English words, using the same leakage metric as `manuvi eval`.
    Imported lazily from src.eval (read only); silently skipped if unavailable."""
    try:
        from ..eval.cases import EvalCase
        from ..eval.metrics import leakage
    except Exception:  # pragma: no cover - eval package optional
        return []
    s, t = unesc(strip_tags(source)), unesc(strip_tags(target))
    if not t.strip():
        return []
    res = leakage(EvalCase(id="fmt", source=s, domains=["document"]), t)
    leaked = res.detail.get("leaked", [])
    if not leaked:
        return []
    return [Issue(rule_id="LEAK001", severity=Severity.warn,
                  message=f"untranslated English words left: {leaked[:8]}",
                  fix_hint="Translate every English word except names and codes.", detail={"leaked": leaked})]


def format_issues(source: str, target: str) -> list[Issue]:
    return [*tag_issues(source, target), *leakage_issue(source, target)]
