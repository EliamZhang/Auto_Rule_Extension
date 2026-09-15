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
