"""assessment 模块的路径解析 —— reviews/ 与 reports/ 的唯一定位点。

数据流契约（见 docs/superpowers/specs/2026-09-14-monorepo-merge-design.md §5）:

    输入  reviews/<YYYY-MM-DD_HHMM>/label_compare_report.xlsx
          ← ARE 的 scripts/label_compare.py 产出
    输出  reports/<YYYY-MM-DD_HHMM>/

设计要点:

- ``default_review_report()`` 优先取 reviews/ 下最新一次的产物；没有 reviews/
  时回退到模块内 input/ 的历史底稿，使脚本仍可脱离 ARE 主链路独立运行。
- 回退而不是抛错，是为了让 ``--help`` 与 ``--input`` 显式指定的调用不受影响。
- 各生成器在写文件前都会自行 ``mkdir(parents=True, exist_ok=True)``，
  因此本模块不产生任何目录副作用。
"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path

MODULE_DIR = Path(__file__).resolve().parents[1]  # modules/assessment/
REPO_ROOT = MODULE_DIR.parents[1]                 # Auto_Rule_Extension/

REVIEWS_DIR = REPO_ROOT / "reviews"
REPORTS_DIR = REPO_ROOT / "reports"

# ARE 的 label_compare.py 产出的文件名（历史上 BPA 找的是
# category_difference_report*.xlsx，同内容两个名字，已统一）
REVIEW_REPORT_NAME = "label_compare_report.xlsx"

# reviews/ 尚不存在时的回退底稿：模块内唯一实际存在的历史输入
LEGACY_INPUT = MODULE_DIR / "input" / "category_difference_report_100.xlsx"

# 进程启动时间戳：同一次运行内所有产物落在同一个 reports/<stamp>/ 下
_RUN_STAMP = datetime.now().strftime("%Y-%m-%d_%H%M")


def _review_sort_key(path: Path) -> tuple[str, float]:
    """目录名 → (数字归一化 key, mtime)，用于倒序取最新。

    历史目录名格式并不统一——`2026-09-09_1025`、`2026-08-11`、`20260811` 都出现过。
    纯字符串序会因 `'-'`(0x2D) < `'0'`(0x30) 把 `20260811` 排到 `2026-09-09_1025` 之前，
    从而选中过期底稿（35 类、缺 02 表）。故先抽掉非数字补足到 12 位（YYYYMMDDHHMM）。
    不足 8 位数字的目录名不含时间信息，key 退化为空串、实际按 mtime 排序。
    """
    digits = "".join(char for char in path.name if char.isdigit())
    key = digits[:12].ljust(12, "0") if len(digits) >= 8 else ""
    try:
        mtime = path.stat().st_mtime
    except OSError:
        mtime = 0.0
    return (key, mtime)


def _review_dirs_newest_first() -> list[Path]:
    if not REVIEWS_DIR.is_dir():
        return []
    return sorted(
        (path for path in REVIEWS_DIR.iterdir() if path.is_dir()),
        key=_review_sort_key,
        reverse=True,
    )


def latest_review_dir() -> Path | None:
    """reviews/ 下最新的审核目录（按目录名中的日期时间归一化排序）。
    无任何目录时返回 None，由调用方决定提示策略。"""
    directories = _review_dirs_newest_first()
    return directories[0] if directories else None


def latest_review_report() -> Path | None:
    """最新一个含 REVIEW_REPORT_NAME 的审核目录中的底稿文件；都没有则 None。

    逐个回退而不是只看最新目录：最新目录有可能是只写了候选 CSV 的半成品
    （例如 label_compare.py 中途失败），此时应继续往前找可用的底稿。
    """
    for directory in _review_dirs_newest_first():
        report = directory / REVIEW_REPORT_NAME
        if report.is_file():
            return report
    return None


def default_review_report() -> Path:
    """生成器的默认 --input。优先最新可用 review，其次模块内历史底稿。"""
    return latest_review_report() or LEGACY_INPUT


def report_output_dir() -> Path:
    """本次运行的输出目录 reports/<YYYY-MM-DD_HHMM>/（不创建）。"""
    return REPORTS_DIR / _RUN_STAMP
