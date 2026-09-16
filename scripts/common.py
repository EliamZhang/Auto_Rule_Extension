"""
Shared utilities for the Auto Rule Extension project.

All scripts import from this module instead of duplicating config loading,
path resolution, engine priorities, and metadata column definitions.
"""

from __future__ import annotations

import json
import logging
import re
from pathlib import Path
from typing import Any

# ── Logging ──────────────────────────────────────────────────────────────────

def setup_logging(name: str | None = None, level: int = logging.INFO) -> logging.Logger:
    """Create a logger with a consistent format. Use in all scripts."""
    logger = logging.getLogger(name or __name__)
    if not logger.handlers:
        handler = logging.StreamHandler()
        handler.setFormatter(logging.Formatter(
            "[%(name)s] %(levelname)s: %(message)s"
        ))
        logger.addHandler(handler)
        logger.setLevel(level)
    return logger


log = setup_logging("auto_rule")


# ── Config ───────────────────────────────────────────────────────────────────

def load_config(project_root: Path | None = None) -> dict[str, Any]:
    """Load config.json from the project root.

    Args:
        project_root: Path to project root. Auto-detected if None.

    Returns:
        Parsed config dictionary.
    """
    if project_root is None:
        # Auto-detect: go up from this file's location
        project_root = Path(__file__).resolve().parent.parent

    config_path = project_root / "config.json"
    if not config_path.exists():
        raise FileNotFoundError(f"config.json not found at {config_path}")

    with open(config_path, encoding="utf-8") as f:
        return json.load(f)


# ── Text normalization ───────────────────────────────────────────────────────

def normalize_pattern_text(text: str, config: dict[str, Any]) -> str:
    """Normalize a transaction description for frequency clustering.

    Strips dates, amounts and long reference numbers, uppercases, and collapses
    whitespace. Driven by ``config["analysis"]["normalization"]``; each toggle
    defaults to True. Used by analyze_gaps and modules/liability_enrich.
    """
    norm_cfg = config.get("analysis", {}).get("normalization", {})
    text = str(text)

    if norm_cfg.get("remove_dates", True):
        text = re.sub(r"\b\d{1,2}[/-]\d{1,2}(?:[/-]\d{2,4})?\b", " ", text)
        text = re.sub(r"\b\d{4}-\d{2}-\d{2}\b", " ", text)

    if norm_cfg.get("remove_amounts", True):
        text = re.sub(r"\$\s*\d+(?:[.,]\d{2})?", " ", text)
        text = re.sub(r"\b\d+\.\d{2}\b", " ", text)

    if norm_cfg.get("remove_numbers", True):
        text = re.sub(r"\b\d{4,}\b", " ", text)
        text = re.sub(r"\bV\d{4}\b", " ", text)

    if norm_cfg.get("uppercase", True):
        text = text.upper()

    return re.sub(r"\s+", " ", text).strip()


# ── Engine metadata ──────────────────────────────────────────────────────────

# Columns that are metadata (not part of rule definition).
# Single source of truth — used by apply_rules, validate_candidates, and baseline.
META_COLUMNS: set[str] = {
    "status",
    "hit_count",
    "risk_level",
    "illion_category",
    "samples",
    "target_file",
    # Source URL recorded by modules/liability_enrich when a candidate came from
    # web verification. Must be stripped before writing, or it pollutes raw/.
    "evidence_source",
}


# The column holding a candidate's match text is engine-dependent: initial
# (merchant_kb) uses `keywords`, transfer / liability use `keyword`, everything
# else uses `pattern`. Reading only one of them silently yields "" — which does
# not fail loudly, it turns its caller into a no-op (see candidate_pattern).
_PATTERN_COLUMNS: tuple[str, ...] = ("pattern", "keywords", "keyword")


def candidate_pattern(row: Any) -> str:
    """Extract a candidate's match text whatever column the engine puts it in.

    Accepts a pandas Series (candidate CSV rows) or a plain dict
    (confirmed_rules.json entries) — both answer ``.get()``.

    Why this exists: reading ``row.get("pattern", row.get("keyword", ""))``
    returns "" for every initial-engine candidate, because merchant_kb's column
    is named ``keywords``. Callers that then do ``if not pattern: continue``
    drop those rows in silence instead of reporting them.
    """
    for col in _PATTERN_COLUMNS:
        value = row.get(col)
        if value is None:
            continue
        # str() rather than a truthiness test: pandas NaN / pd.NA are not
        # None and evaluating them in a boolean context either lies (NaN is
        # truthy) or raises (pd.NA is ambiguous).
        text = str(value).strip()
        if text and text.lower() not in ("nan", "<na>", "none"):
            return text
    return ""


# ── Per-engine text normalization & match semantics ──────────────────────────
#
# `baseline.py` answers "what would this candidate actually match?", so it has to
# reproduce each engine's preprocessing and matching — not just compare strings.
# The candidate CSVs cannot tell it how: initial's column is `keywords` and it
# has no match_type column at all, and transfer's patterns are regexes but its
# candidates carry no match_type either. Reading everything as a literal keyword
# substring therefore reported gain=0 for both — 329 initial hits and 1056
# transfer hits both came out as 0, so the human approval gate was being shown
# roughly 10% of the true impact.
#
# The normalizers below mirror finv_category_V2/classification_core/text.py,
# because that is the code sitting on the other side of that gate.

_CLEAN_RE = re.compile(r"[^A-Z0-9]+")


def _is_missing(value: Any) -> bool:
    """True for None / NaN / pd.NA, without importing pandas (see read_transactions)."""
    if value is None:
        return True
    try:
        return bool(value != value)   # NaN compares unequal to itself
    except (TypeError, ValueError):
        return True                   # pd.NA: `!=` yields NA and bool() raises


def clean_text(value: Any) -> str:
    """Uppercase, turn every non-[A-Z0-9] run into a space, collapse spaces.

    Mirrors finv's ``classification_core/text.py:clean_text``. Used by initial /
    rent / gambling / catch_all, all of which match on this form.
    """
    if _is_missing(value):
        return ""
    return " ".join(_CLEAN_RE.sub(" ", str(value).upper()).split())


# Which preprocessing each engine applies to transaction text before matching.
# Only the engines that genuinely differ are listed; anything absent gets "raw".
_ENGINE_TEXT: dict[str, str] = {
    "initial": "clean",
    "rent": "clean",
    "gambling": "clean",
    "catch_all": "clean",
    "transfer": "lower",     # normalize_text(): collapse spaces + .lower()
    "liability": "upper",    # normalize_match_text(): strip + upper + collapse
    # income / fee / dishonour / all_other_credit keep "raw": matching raw text
    # reproduces their generator hit_count exactly (129/129, 17/17, 15/15), so
    # there is nothing to fix and every reason not to disturb it.
}


def normalize_engine_text(engine_id: str, value: Any) -> str:
    """Transaction text as `engine_id` sees it. See _ENGINE_TEXT."""
    kind = _ENGINE_TEXT.get(engine_id, "raw")
    if kind == "clean":
        return clean_text(value)
    if kind == "lower":
        return re.sub(r"\s+", " ", str(value).lower()).strip()
    if kind == "upper":
        return re.sub(r"\s+", " ", str(value).strip().upper())
    return "" if _is_missing(value) else str(value)


# How each engine decides a match, after normalization. An explicit
# `match_type == "regex"` on the candidate always overrides this — catch_all and
# both layers of rent/gambling declare it; the engines below often cannot.
_MATCH_MODES: dict[str, str] = {
    "initial": "whole_word_variants",
    "rent": "whole_word_variants",
    "gambling": "whole_word_variants",
    "catch_all": "whole_word_variants",
    "transfer": "regex_lower",
    "liability": "alpha_edge",
    "income": "regex",
    "fee": "regex",
    "dishonour": "substring",
    "all_other_credit": "substring",
}


def match_mode(engine_id: str, match_type: str) -> str:
    """Resolve how `engine_id` matches this candidate.

    `match_type` defaults to "keyword" in every candidate reader, so an absent
    column silently means "keyword" — which is why the per-engine table exists.
    """
    if str(match_type).strip().lower() == "regex":
        return "regex"
    return _MATCH_MODES.get(engine_id, "substring")


def split_keyword_variants(pattern: Any, sep: str = "|") -> list[str]:
    """Split a variant list on `sep`, dropping empty entries.

    merchant_kb's ``keywords`` column and the institution rows of rent /
    gambling use ``|``; liability's counterparty keywords use ``;``. The
    separator is not cosmetic: treating the raw string as one literal keyword
    matches nothing, because no transaction text contains a literal separator.
    """
    return [v.strip() for v in str(pattern).split(sep) if v.strip()]


def whole_word_regex(keyword: str) -> str:
    """Whole-word matcher regex for text already passed through clean_text().

    On collapsed uppercase text the engine's boundary test (``text[pos-1] != " "``)
    is exactly a space/edge boundary, so this reproduces it. Keywords containing
    characters outside [A-Z0-9] can never match — that is real engine behaviour
    (keyword loading stopped auto-cleaning in 2026-08), not a bug here.
    """
    return rf"(?:^| ){re.escape(keyword)}(?: |$)"


def alpha_edge_regex(keyword: str) -> str:
    """liability counterparty boundary: ``(?<![A-Za-z])kw(?![A-Za-z])``.

    Deliberately not ``\\b`` — digits may pass through, so "1360 CASH LOANS"
    matches the keyword "360 CASH LOANS". Applied to uppercased text.
    """
    return rf"(?<![A-Za-z]){re.escape(keyword)}(?![A-Za-z])"


# ── Vectorized candidate matching ────────────────────────────────────────────
#
# One implementation, shared by baseline.py and test_rules.py. Both used to carry
# their own copy of this matcher, and both copies had the same blind spot: they
# compared the raw pattern against raw text as a case-insensitive literal. So
# initial's `keywords` variants, transfer's lowercase regexes and liability's
# alpha-edge boundaries all came out as gain=0 — two copies of one subtly-wrong
# matcher is how that stayed invisible for so long.

def _all_false(series: Any) -> Any:
    """bool-dtype mask of False with the same index, without importing pandas.

    ``series.map(lambda _: False)`` would also work, but ``isna()`` is guaranteed
    to be bool-dtype, so the ``|=`` accumulation below can't hit an object-dtype
    surprise. Written as a helper purely so the pandas-free constraint (see
    read_transactions) is stated once.
    """
    return series.isna() & False


def _safe_contains(series: Any, pattern: str, case: bool = True) -> Any:
    """Regex match that degrades to "no match" instead of aborting the run.

    An uncompilable pattern is reported as a warning and counted as zero gain,
    which is the honest answer — it matches nothing — and keeps one bad candidate
    from taking down the impact report for every other engine.
    """
    try:
        return series.str.contains(pattern, case=case, regex=True, na=False)
    except re.error as exc:
        log.warning("  跳过非法 regex %r: %s", pattern[:60], exc)
        return _all_false(series)


def match_series(engine_id: str, pattern: str, match_type: str, series: Any) -> Any:
    """Match one candidate against text already normalized for `engine_id`.

    `series` must come from `engine_texts`, which applies that engine's own
    preprocessing. Getting the pairing wrong is silent: a mismatch yields an
    all-False mask, so the candidate is simply reported as gain=0 rather than
    raising anything.
    """
    if not pattern or series.empty:
        return _all_false(series)

    mode = match_mode(engine_id, match_type)

    if mode == "regex":
        return _safe_contains(series, pattern, case=False)

    if mode == "regex_lower":
        # transfer lowercases its text, so its patterns only match in that form
        # and must not be re-cased here.
        return _safe_contains(series, pattern)

    if mode == "whole_word_variants":
        mask = _all_false(series)
        for keyword in split_keyword_variants(pattern):
            mask = mask | _safe_contains(series, whole_word_regex(keyword))
        return mask

    if mode == "alpha_edge":
        # liability splits its keyword column on ";" (`counterparty.py` does
        # `keyword.split(";")`), not on "|". Splitting on the wrong separator
        # inverts the verdict for every multi-variant candidate: the correct
        # semicolon form scores 0 while the pipe form — a dead rule in
        # production — scores full marks.
        mask = _all_false(series)
        for keyword in split_keyword_variants(pattern, sep=";"):
            mask = mask | _safe_contains(series, alpha_edge_regex(keyword), case=False)
        return mask

    # substring: dishonour / all_other_credit — a case-insensitive literal, and
    # the fallback for an engine absent from _MATCH_MODES.
    return series.str.contains(pattern, case=False, regex=False, na=False)


def engine_texts(
    engine_id: str,
    unclassified: Any,
    classified: Any,
    cache: dict[str, tuple[Any, Any]],
) -> tuple[Any, Any]:
    """(unclassified, classified) text as `engine_id` sees it, memoized per engine.

    Both series are large, so each engine's normalization is computed lazily and
    only once — an engine that never appears in the run never pays for it.
    """
    if engine_id not in cache:
        cache[engine_id] = (
            unclassified.map(lambda v: normalize_engine_text(engine_id, v)),
            classified.map(lambda v: normalize_engine_text(engine_id, v)),
        )
    return cache[engine_id]


def get_engine_priority(engine_id: str, config: dict[str, Any] | None = None) -> int:
    """Get the execution priority for an engine.

    Falls back to the built-in mapping if not in config.
    Lower number = runs earlier = can be overwritten by later engines.
    """
    # Try config first
    if config:
        priorities = config.get("engine_priorities", {})
        if engine_id in priorities:
            return priorities[engine_id]

    # Built-in fallback (matches finv_category_V2 configs/pipeline.json as of 2026-08-27:
    # transfer=1, initial=10 — the two were swapped on 2026-08-27 commit 30a8da3)
    _DEFAULT_PRIORITIES: dict[str, int] = {
        "transfer": 1,
        "initial": 10,
        "dishonour": 150,
        "gambling": 180,
        "income": 200,
        "liability": 300,
        "all_other_credit": 400,
        "fee": 500,
        "rent": 800,
        "catch_all": 999,
    }
    return _DEFAULT_PRIORITIES.get(engine_id, 500)


# ── Path resolution ──────────────────────────────────────────────────────────

def resolve_rule_path(
    rules_base: Path,
    engine_id: str,
    engine_config: dict[str, Any],
    rule_file_name: str,
) -> Path:
    """Resolve a rule file path within the rules base directory.

    The engine directory is derived from engine_config's `engine_dir`
    (or computed as `{engine_id}_rule` by convention).
    """
    engine_dir = engine_config.get("engine_dir", f"{engine_id}_rule")
    rule_dir_rel = engine_config.get("rule_dir", "")
    return rules_base / engine_dir / rule_dir_rel / rule_file_name


def resolve_finv_path(
    finv_root: Path,
    engine_id: str,
    engine_config: dict[str, Any],
    rule_file_name: str,
) -> Path:
    """Resolve a rule file path within the finv_category_V2 repo.

    finv layout differs from the local raw/ layout:
      - initial:   ``<finv_root>/initial_engine/<file>``            (merchant_kb.csv lives in engine root)
      - all other: ``<finv_root>/<engine_id>_engine/resources/<file>``
    Configure per-engine overrides via ``finv_engine_dir`` / ``finv_rule_dir``
    in config.json (values verified against finv_category_V2 code, 2026-08-31).
    """
    finv_engine_dir = engine_config.get("finv_engine_dir", f"{engine_id}_engine")
    finv_rule_dir = engine_config.get("finv_rule_dir", "resources")
    return finv_root / finv_engine_dir / finv_rule_dir / rule_file_name


def resolve_rules_base(project_root: Path, config: dict[str, Any]) -> Path:
    """Get the rules base directory from config or default to 'raw'."""
    return project_root / config.get("rules_base_dir", "raw")


# ── Data loading ─────────────────────────────────────────────────────────────

def read_transactions(input_path: Path) -> "pd.DataFrame":
    """Read the transactions sheet from a pipeline report (.xlsx or .csv).

    Prefers a sheet literally named ``transactions``; falls back to the first
    sheet so a hand-trimmed export still loads. Raises ValueError on any other
    suffix rather than guessing.

    pandas is imported lazily: sync_rules.py imports common but must stay
    pandas-free so it can run in a bare environment.
    """
    import pandas as pd

    input_path = Path(input_path)
    suffix = input_path.suffix.lower()

    if suffix in (".xlsx", ".xlsm", ".xls"):
        xlsx = pd.ExcelFile(input_path)
        sheet = "transactions" if "transactions" in xlsx.sheet_names else 0
        df = pd.read_excel(xlsx, sheet_name=sheet)
        log.info("Loaded %s: %s rows, sheets: %s", suffix, f"{len(df):,}", xlsx.sheet_names)
        return df

    if suffix == ".csv":
        df = pd.read_csv(input_path, encoding="utf-8-sig")
        log.info("Loaded .csv: %s rows", f"{len(df):,}")
        return df

    raise ValueError(f"Unsupported input format: {suffix}. Expected .xlsx or .csv")


def load_category_catalog(project_root: Path | None = None) -> dict[str, Any]:
    """Load the category catalog that maps finv categories to owner engines.

    Returns:
        {
            "categories": {"Dining Out": {"owner_engine_id": "initial,catch_all"}, ...},
            "category_to_primary_engine": {"Dining Out": "initial", "Fees": "fee", ...},
        }
    """
    if project_root is None:
        project_root = Path(__file__).resolve().parent.parent

    config = load_config(project_root)
    rules_base = resolve_rules_base(project_root, config)
    catalog_path = rules_base / "category_catalog.json"

    if not catalog_path.exists():
        log.warning("category_catalog.json not found, using fallback")
        return {"categories": {}, "category_to_primary_engine": {}}

    with open(catalog_path, encoding="utf-8") as f:
        catalog = json.load(f)

    # Build category → primary engine mapping (first engine listed is primary)
    category_to_engine: dict[str, str] = {}
    for cat_name, cat_info in catalog.get("categories", {}).items():
        owners = cat_info.get("owner_engine_id", "")
        primary = owners.split(",")[0].strip() if owners else "catch_all"
        category_to_engine[cat_name] = primary

    catalog["category_to_primary_engine"] = category_to_engine
    return catalog
