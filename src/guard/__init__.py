"""Deterministic guard layer: masking, number policy, signal words, QA. No LLM calls."""
from .masking import PlaceholderReport, check_placeholders, load_patterns, mask, unmask
from .models import FixResult, Issue, Severity
from .numbers import NUMBER_POLICY, diff_numbers, extract_numbers
from .qa import MAX_FIX_ATTEMPTS, QAContext, fix_loop, qa_check
from .signal_words import is_signal, load_signal_table, signal_mismatches
from .script import script_issues
from .segment import GuardedResult, Prepared, run_guarded, split_signal
