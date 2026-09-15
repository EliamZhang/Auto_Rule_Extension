#!/usr/bin/env python3
"""Shared helpers for the liability_enrich pipeline.

Kept deliberately small and dependency-light: gap_source.py (Step 1) and
evidence.py (Step 3) must agree on what "a liability category" is and on
exactly how the liability engine matches a keyword. Any divergence here shows
up as candidates that look fine in evidence.py but never fire in production.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path
from typing import Any

import pandas as pd

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(REPO_ROOT / "scripts"))

from common import load_category_catalog  # noqa: E402

# Fallback if raw/category_catalog.json is missing. Kept in sync with it by hand
# only as a safety net — the catalog is the source of truth.
FALLBACK_LIABILITY_CATEGORIES = frozenset({
    "Non SACC Loans",
    "SACC Loans",
    "Unknown Loans",
    "Credit Card Repayments",
    "Debt Collection",
    "Debt Consolidation",
})

# Written by finv's generic-loan backstop (liability_engine/pipeline.py).
GENERIC_LOAN_COUNTERPARTY = "Generic Loans"

# The rule file these candidates are destined for.
COUNTERPARTY_RULE_FILE = "counterparty_keyword_rules.csv"


def primary_engine(owner_engine_id: Any) -> str:
    """First engine id in a comma-separated owner list, or '' if empty."""
    return str(owner_engine_id).split(",")[0].strip()


def liability_categories(project_root: Path | None = None) -> set[str]:
    """Loan-type categories whose *primary* owner is the liability engine.

    Primary means first in the catalog's ``owner_engine_id`` list. That
    deliberately excludes three categories liability can also emit:

    - ``Retail``   — ``initial,liability,catch_all``; only reached from
      ``special_rules.py``'s Cash Converters Retail correction. illion calling
      something Retail while finv didn't is not evidence of a missing lender.
    - ``Dishonours``  (``dishonour,liability``) — a failed payment, not a loan.
    - ``Overdrawn``   (``fee,liability``) — a fee, not a loan.

    A plain substring test on ``owner_engine_id`` pulls all three in and floods
    the illion-mismatch class with noise, so this asks the stricter question.
    """
    root = project_root or REPO_ROOT
    catalog = load_category_catalog(root)
    cats = {
        name
        for name, info in catalog.get("categories", {}).items()
        if primary_engine(info.get("owner_engine_id", "")) == "liability"
    }
    return cats or set(FALLBACK_LIABILITY_CATEGORIES)


def normalize_match_text(series: pd.Series) -> pd.Series:
    """Reproduce liability_engine's text normalization for a whole column.

    Mirrors ``counterparty.py``: collapse whitespace, strip, uppercase.
    Punctuation is preserved — this is *not* ``clean_text()``, so a keyword
    must be written exactly as it appears in the raw text.
    """
    return (
        series.fillna("").astype(str)
        .str.replace(r"\s+", " ", regex=True)
        .str.strip()
        .str.upper()
    )


def keyword_pattern(keyword: str) -> str:
    """Build the exact regex liability_engine uses for one keyword.

    ``(?<![A-Za-z])`` / ``(?![A-Za-z])`` — note this is *not* ``\\b``. Digits
    pass straight through, so a keyword starting with a digit over-matches:
    "360 CASH LOANS" also matches inside "1360 CASH LOANS".
    """
    return r"(?<![A-Za-z])" + re.escape(keyword) + r"(?![A-Za-z])"


def split_variants(raw: str) -> list[str]:
    """Split a candidate's ``keyword`` cell into normalized variants.

    The engine splits on ``;`` (``split_upper_terms``) and uppercases each
    variant, so we must too — otherwise a lowercase candidate in the CSV looks
    like a miss here and still fires in production.
    """
    out: list[str] = []
    for part in str(raw or "").split(";"):
        part = re.sub(r"\s+", " ", part).strip().upper()
        if part:
            out.append(part)
    return out


def validate_keyword(keyword: str) -> str | None:
    """Return a human-readable problem with a keyword, or None if it is fine.

    Only the failure modes that make a rule silently dead or actively
    dangerous. Kept here (not in validate_candidates.py) because the skill
    needs to reject these *before* writing the candidate CSV.
    """
    if not keyword:
        return "keyword 为空"
    if keyword[0].isdigit():
        return (f"以数字开头（{keyword!r}）—— 边界是 (?<![A-Za-z]) 而非 \\b，"
                f"会误伤 '1{keyword}' 这类文本")
    if not re.search(r"[A-Z]", keyword):
        return f"不含任何字母（{keyword!r}）"
    return None
