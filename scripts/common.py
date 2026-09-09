"""
Shared utilities for the Auto Rule Extension project.

All scripts import from this module instead of duplicating config loading,
path resolution, engine priorities, and metadata column definitions.
"""

from __future__ import annotations

import json
import logging
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
