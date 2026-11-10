"""One guarded translation of one segment, shared by the batch pipeline and `manuvi eval`.

    split leading signal word -> mask codes -> [LLM] -> re-attach fixed VI signal -> QA (+script guard)
        -> fix loop (max 2, repair prompt) -> accepted | routed to review

The signal word is handled deterministically: the model never sees it, so it cannot mistranslate or drop it. The
pipeline graph calls the primitives (`prepare`, `assemble`, `check`, `fix_messages`, `needs_fix`, `finalize_target`)
node by node (it needs checkpoints); `run_guarded` is the same sequence as one function for callers without
checkpoints (eval). Neither contains its own copy of the rules.
"""
from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass, field
from typing import Any, Callable, Iterable, Sequence

from .masking import PatternSpec, mask, unmask
from .models import Issue, Severity
from .qa import MAX_FIX_ATTEMPTS, QAContext, qa_check

_SKIP = ("YES", "NO")


@dataclass
class Prepared:
    source: str
    signal: str | None      # EN signal word (table key) or None
    prefix: str             # fixed VI signal + separator, "" when no signal
    body: str               # source without the signal word: what the model translates
    masked: str
    mapping: dict[str, str]


def split_signal(source: str, table: dict[str, str]) -> tuple[str | None, str, str]:
    """Leading DANGER/WARNING/CAUTION/NOTE/IMPORTANT (any case) followed by ':' or a newline.
    Returns (EN word, separator to use after the VI word, rest of the text)."""
    words = [w for w in table if w not in _SKIP]
    if not words:
        return None, "", source
    alt = "|".join(re.escape(unicodedata.normalize("NFC", w)) for w in sorted(words, key=len, reverse=True))
    m = re.match(rf"[ \t]*({alt})(?!\w)[ \t]*(?:(:)[ \t]*(\r?\n)?|(\r?\n))[ \t]*", unicodedata.normalize("NFC", source),
                 re.IGNORECASE)
    if not m:
        return None, "", source
    en = next(w for w in words if w.casefold() == m.group(1).casefold())
    sep = ":\n" if (m.group(2) and m.group(3)) else ": " if m.group(2) else "\n"
    return en, sep, unicodedata.normalize("NFC", source)[m.end():]


def prepare(source: str, patterns: Iterable[PatternSpec], table: dict[str, str]) -> Prepared:
    en, sep, body = split_signal(source, table)
    masked, mapping = mask(body, patterns)
    prefix = ""
    if en:
        prefix = unicodedata.normalize("NFC", table[en]) + (sep if body.strip() else "")
    return Prepared(source, en, prefix, body, masked, mapping)


def assemble(prep: Prepared, raw: str) -> str:
    """Masked final text: fixed VI signal + the model's translation of the body."""
    return prep.prefix + unicodedata.normalize("NFC", raw)


def check(prep: Prepared, raw: str, base: QAContext) -> list[Issue]:
    """Deterministic QA of the assembled text against the ORIGINAL source (so the signal metric is independent)."""
    ctx = QAContext(patterns=base.patterns, signal_table=base.signal_table, mapping=prep.mapping)
    return qa_check(prep.source, assemble(prep, raw), ctx)


def has_errors(issues: Sequence[Any]) -> bool:
    return any(_get(i, "severity") in (Severity.error, "error") for i in issues)


def needs_fix(issues: Sequence[Any], attempt: int) -> bool:
    return has_errors(issues) and attempt < MAX_FIX_ATTEMPTS


def _get(i: Any, k: str):
    return i[k] if isinstance(i, dict) else getattr(i, k)


def fix_messages(messages: list[dict], raw: str, issues: Sequence[Any], attempt: int) -> list[dict]:
    hints = "\n".join(f"- {_get(i, 'message')} Hint: {_get(i, 'fix_hint')}" for i in issues
                      if _get(i, "severity") not in (Severity.ok, "ok"))
    return [*messages, {"role": "assistant", "content": raw},
            {"role": "user", "content": f"FIX (attempt {attempt}): the translation above failed deterministic checks.\n"
             + hints + "\nReturn the corrected translation only."}]


def finalize_target(issues: Sequence[Any], text: str) -> str:
    """Never ship output that failed the script guard (nor a truncated version of it): blank, for review."""
    if any(str(_get(i, "rule_id")).startswith("SCR") and has_errors([i]) for i in issues):
        return ""
    return text


@dataclass
class GuardedResult:
    prep: Prepared
    raw: str                       # last model text (masked body), for debugging; may be bad
    text: str                      # unmasked final text; "" when the script guard failed
    issues: list[Issue]
    attempts: int
    repaired: bool                 # failed first, passed after the fix loop
    routed_to_review: bool         # still failing after the loop: not accepted
    calls: list[dict] = field(default_factory=list)

    @property
    def rules(self) -> list[str]:
        return [i.rule_id for i in self.issues if i.severity == Severity.error]


def run_guarded(source: str, *, patterns: Iterable[PatternSpec], table: dict[str, str],
                build_messages: Callable[[str], list[dict]],
                complete: Callable[[str, list[dict]], dict],
                qa_ctx: QAContext | None = None) -> GuardedResult:
    """`complete(stage, messages)` returns {"text", ...usage}; stage is 'translate', 'fix1', 'fix2'."""
    patterns = list(patterns)
    base = qa_ctx or QAContext(patterns=patterns, signal_table=table)
    prep = prepare(source, patterns, table)
    calls: list[dict] = []
    messages = build_messages(prep.masked) if prep.masked.strip() else []
    raw = ""
    if messages:
        res = complete("translate", messages)
        calls.append(res)
        raw = res["text"]
    issues = check(prep, raw, base)
    attempts = 0
    while messages and needs_fix(issues, attempts):
        attempts += 1
        res = complete(f"fix{attempts}", fix_messages(messages, raw, issues, attempts))
        calls.append(res)
        raw = res["text"]
        issues = check(prep, raw, base)
    routed = has_errors(issues)
    text = finalize_target(issues, unmask(assemble(prep, raw), prep.mapping))
    return GuardedResult(prep, raw, text, issues, attempts, attempts > 0 and not routed, routed, calls)
