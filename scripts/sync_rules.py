#!/usr/bin/env python3
"""raw/ ↔ finv_category_V2 规则文件同步：三方对比，只拉不推。

用法:
    python scripts/sync_rules.py status                     # 漂移总览（默认动作）
    python scripts/sync_rules.py status --engine rent       # 只看某个引擎
    python scripts/sync_rules.py status --engine rent --diff
    python scripts/sync_rules.py pull                       # finv -> raw/，自动 .bak 备份
    python scripts/sync_rules.py pull --engine rent --dry-run
    python scripts/sync_rules.py pull --engine rent --accept-finv   # 看过 diff 后的显式决定
    python scripts/sync_rules.py adopt                      # 把当前状态登记为新基线

为什么需要它:
    raw/ 是本地规则工作副本，finv_category_V2 才是线上事实源。两边各自演进，
    靠人工记忆判断"哪边新"必然出错（实测 rent 已 15 行 vs 21809 行、catch_all
    本地多 16 行）。本脚本把"哪边新"变成可计算的判定。

判定依据 `.sync_state.json`（仓库根，纳入 git）:
    记录上次登记时**两侧各自**的内容 sha256。据此三方对比：只有一侧变了、
    还是两侧都变了。记录的是内容哈希而非时间戳，因此与机器无关，克隆到新机器
    后同步状态依然有效。

    条件                状态          动作
    两侧 hash 均未变     一致          —
    仅 finv 变          finv 领先      pull
    仅 raw 变           raw 领先       去跑 apply_rules.py --sync_to 推上去
    两侧均变            冲突           只报告，人工裁决
    清单中无记录         未登记        人工确认后 adopt
    adopt 时两侧就不同   已登记分叉     人工裁决（不自动 pull）

硬约束:
    - **只拉不推**。推送必须走 apply_rules.py --sync_to，那条路径在审批门之后；
      本脚本不提供 push，避免绕过门禁。
    - **pull 默认只处理安全情形**：状态是 finv 领先，**且登记基线本身是收敛的**
      （基线 raw_sha == finv_sha）。第二条是关键——它保证 raw 当前内容就是"上次
      同步后的 finv 内容"，覆盖它不会丢掉任何本地改动。基线本身就分叉的文件
      （例如 catch_all 本地多 16 条规则）默认拒绝 pull。
    - **`--accept-finv` 是显式逃生门**。人工看过 `--diff` 后，用它表达"我决定采用
      finv 侧"。此时未登记 / 冲突 / 已登记分叉都放行，覆盖前强制 .bak 备份，
      并把新指纹登记为基线。没有这个门，首次同步 4 个漂移文件仍然只能手工 cp。
    - **schema 漂移独立告警**。列数/列名变化显式报出，不静默复制（rent 的
      5 列 -> 7 列就是这样发现的）。
    - **merchant_kb.csv 默认不参与 pull**（74 MB，且 §2.2 记录它是三方分叉、
      需人工裁决）。要拉必须显式加 --include-large。
"""

from __future__ import annotations

import argparse
import csv
import difflib
import hashlib
import json
import shutil
import sys
import tempfile
import unicodedata
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from common import (  # noqa: E402
    load_config,
    resolve_finv_path,
    resolve_rules_base,
    setup_logging,
)

log = setup_logging("sync_rules")

PROJECT_ROOT = Path(__file__).resolve().parent.parent

STATE_FILE_NAME = ".sync_state.json"
STATE_VERSION = 1

# 单行 field 超过 csv 模块默认 128KB 上限时会抛 _csv.Error（KB 里有 137KB 的行）
csv.field_size_limit(10_000_000)

# ── 状态 ─────────────────────────────────────────────────────────────────────

IN_SYNC = "一致"
FINV_AHEAD = "finv 领先"
RAW_AHEAD = "raw 领先"
CONFLICT = "冲突"
UNREGISTERED = "未登记"
DIVERGED_AT_ADOPT = "已登记分叉"
MISSING = "文件缺失"

# 允许 pull 的状态：只有 finv 领先才可能安全，且 do_pull 里还要再验一次
# "登记基线本身是收敛的"（见下方 _baseline_is_converged）
PULLABLE = {FINV_AHEAD}

STATUS_ORDER = [CONFLICT, MISSING, FINV_AHEAD, RAW_AHEAD, DIVERGED_AT_ADOPT, UNREGISTERED, IN_SYNC]


# ── 指纹 ─────────────────────────────────────────────────────────────────────

@dataclass
class Fingerprint:
    """一个规则文件的内容指纹。sha256 是判定依据，其余字段只用于展示与告警。"""

    sha256: str
    size: int
    lines: int
    columns: list[str] = field(default_factory=list)

    def brief(self, limit: int = 12) -> str:
        head = ", ".join(self.columns[:limit])
        if len(self.columns) > limit:
            head += ", ..."
        return f"{self.lines} 行 / {len(self.columns)} 列 ({head})"


def fingerprint(path: Path) -> Fingerprint | None:
    """一次顺序读完成 sha256 + 行数 + 首行列名。文件不存在返回 None。"""
    if not path.is_file():
        return None

    digest = hashlib.sha256()
    size = 0
    newlines = 0
    with path.open("rb") as handle:
        while chunk := handle.read(1024 * 1024):
            digest.update(chunk)
            size += len(chunk)
            newlines += chunk.count(b"\n")

    columns: list[str] = []
    try:
        with path.open("r", encoding="utf-8-sig", errors="replace", newline="") as handle:
            first = next(csv.reader(handle), [])
        columns = [cell.strip() for cell in first]
    except (OSError, csv.Error) as exc:
        log.warning("读取 %s 首行失败: %s", path, exc)

    return Fingerprint(sha256=digest.hexdigest(), size=size, lines=newlines, columns=columns)


# ── 文件清单发现 ─────────────────────────────────────────────────────────────

@dataclass
class FileRef:
    engine_id: str
    name: str
    raw_path: Path
    finv_path: Path

    @property
    def key(self) -> str:
        return f"{self.engine_id}/{self.name}"


def finv_engine_dir(finv_root: Path, engine_id: str, engine_config: dict) -> Path:
    """finv 侧引擎规则目录。resolve_finv_path 用空文件名取目录。"""
    return resolve_finv_path(finv_root, engine_id, engine_config, "")


def discover_files(config: dict, finv_root: Path) -> list[FileRef]:
    """枚举要对比的文件：config 登记的 rule_files ∪ 两侧目录里实际存在的 *.csv。

    只信 config 是不够的——实测 config 的 rule_files 是"规则创作清单"而非文件全量，
    liability 少 bnpl_maximum_limits.csv、transfer 少 4 个 pattern/exclusion 文件、
    income 少 income_config.csv，而这些都会实质影响流水线行为。
    """
    rules_base = resolve_rules_base(PROJECT_ROOT, config)
    refs: list[FileRef] = []

    for engine_id, engine_config in config.get("engines", {}).items():
        raw_dir = rules_base / engine_config.get("engine_dir", f"{engine_id}_rule")
        finv_dir = finv_engine_dir(finv_root, engine_id, engine_config)

        names: set[str] = set(engine_config.get("rule_files", []))
        for directory in (raw_dir, finv_dir):
            if directory.is_dir():
                names.update(path.name for path in directory.glob("*.csv"))

        for name in sorted(names):
            refs.append(FileRef(engine_id, name, raw_dir / name, finv_dir / name))

    return refs


# ── 状态文件 ─────────────────────────────────────────────────────────────────

def state_path() -> Path:
    return PROJECT_ROOT / STATE_FILE_NAME


def load_state() -> dict:
    path = state_path()
    if not path.is_file():
        return {"version": STATE_VERSION, "updated_at": "", "files": {}}
    with path.open("r", encoding="utf-8") as handle:
        state = json.load(handle)
    state.setdefault("version", STATE_VERSION)
    state.setdefault("files", {})
    return state


def save_state(state: dict) -> None:
    """原子写：先写临时文件再 replace，避免中途失败留下半个 JSON。"""
    state["version"] = STATE_VERSION
    state["updated_at"] = datetime.now().strftime("%Y-%m-%dT%H:%M:%S")

    path = state_path()
    handle_fd, temp_name = tempfile.mkstemp(dir=str(path.parent), suffix=".tmp")
    try:
        with open(handle_fd, "w", encoding="utf-8", newline="\n") as handle:
            json.dump(state, handle, ensure_ascii=False, indent=2, sort_keys=True)
            handle.write("\n")
        Path(temp_name).replace(path)
    except BaseException:
        Path(temp_name).unlink(missing_ok=True)
        raise


# ── 判定 ─────────────────────────────────────────────────────────────────────

@dataclass
class Result:
    ref: FileRef
    status: str
    raw: Fingerprint | None
    finv: Fingerprint | None
    schema_note: str = ""
    reason: str = ""

    @property
    def counts(self) -> str:
        raw_lines = "—" if self.raw is None else str(self.raw.lines)
        finv_lines = "—" if self.finv is None else str(self.finv.lines)
        if raw_lines == finv_lines:
            return raw_lines
        return f"{raw_lines} -> {finv_lines}"

    @property
    def advice(self) -> str:
        if self.status == FINV_AHEAD:
            return "pull"
        if self.status == RAW_AHEAD:
            return "apply_rules.py --sync_to"
        if self.status in (CONFLICT, DIVERGED_AT_ADOPT):
            return "人工裁决（--diff 看差异）"
        if self.status == UNREGISTERED:
            return "确认后 adopt"
        if self.status == MISSING:
            return "只在单侧存在，不自动处理"
        return "-"


def classify(ref: FileRef, raw: Fingerprint | None, finv: Fingerprint | None,
             state: dict) -> Result:
    if raw is None or finv is None:
        return Result(ref, MISSING, raw, finv)

    schema_note = ""
    if raw.columns != finv.columns:
        schema_note = f"schema {len(raw.columns)} -> {len(finv.columns)} 列"

    if raw.sha256 == finv.sha256:
        return Result(ref, IN_SYNC, raw, finv, schema_note)

    entry = state["files"].get(ref.key)
    if entry is None:
        return Result(ref, UNREGISTERED, raw, finv, schema_note,
                      "基线清单里没有这个文件")

    raw_changed = raw.sha256 != entry.get("raw_sha256")
    finv_changed = finv.sha256 != entry.get("finv_sha256")

    if finv_changed and not raw_changed:
        return Result(ref, FINV_AHEAD, raw, finv, schema_note, "finv 变了，raw 未动")
    if raw_changed and not finv_changed:
        return Result(ref, RAW_AHEAD, raw, finv, schema_note, "raw 变了，finv 未动")
    if raw_changed and finv_changed:
        return Result(ref, CONFLICT, raw, finv, schema_note, "两侧都变了")
    return Result(ref, DIVERGED_AT_ADOPT, raw, finv, schema_note,
                  "两侧都没动，但登记时两侧内容就不同——无法判定谁新")


def _baseline_is_converged(entry: dict | None) -> bool:
    """登记基线是否收敛（raw_sha == finv_sha）。

    这是 pull 安全性的充要条件：只有基线收敛，才能证明 raw 当前内容就是
    "上次同步后的 finv 内容"，用新 finv 覆盖它不会丢掉任何 raw 侧的本地改动。
    基线本身分叉时（例如 catch_all 本地多 16 条规则），pull 会静默删除本地规则。
    """
    return bool(entry) and entry.get("raw_sha256") == entry.get("finv_sha256")


def collect(config: dict, finv_root: Path, engine_filter: str | None) -> list[Result]:
    state = load_state()
    results = []
    for ref in discover_files(config, finv_root):
        if engine_filter and ref.engine_id != engine_filter:
            continue
        results.append(classify(ref, fingerprint(ref.raw_path), fingerprint(ref.finv_path), state))
    return results


# ── 表格输出（CJK 双宽对齐） ─────────────────────────────────────────────────

def display_width(text: str) -> int:
    return sum(2 if unicodedata.east_asian_width(char) in "WF" else 1 for char in text)


def pad(text: str, width: int) -> str:
    return text + " " * max(0, width - display_width(text))


def print_table(results: list[Result]) -> None:
    headers = ["引擎", "文件", "状态", "行数", "建议"]
    rows = [[r.ref.engine_id, r.ref.name, r.status, r.counts, r.advice] for r in results]
    rows.sort(key=lambda row: (STATUS_ORDER.index(row[2]), row[0], row[1]))

    widths = [display_width(headers[i]) for i in range(len(headers))]
    for row in rows:
        for i, cell in enumerate(row):
            widths[i] = max(widths[i], display_width(cell))

    print("  ".join(pad(headers[i], widths[i]) for i in range(len(headers))))
    print("  ".join("-" * widths[i] for i in range(len(headers))))
    for row in rows:
        print("  ".join(pad(row[i], widths[i]) for i in range(len(headers))))

    notes = [r for r in results if r.schema_note or r.status in (CONFLICT, DIVERGED_AT_ADOPT, UNREGISTERED)]
    if notes:
        print()
        for result in notes:
            parts = [part for part in (result.schema_note, result.reason) if part]
            print(f"  [{result.ref.key}] {' | '.join(parts)}")


def print_summary(results: list[Result], state: dict) -> None:
    counts: dict[str, int] = {}
    for result in results:
        counts[result.status] = counts.get(result.status, 0) + 1
    summary = "，".join(f"{status} {counts[status]}" for status in STATUS_ORDER if status in counts)
    print()
    print(f"共 {len(results)} 个文件：{summary or '（无）'}")

    if state["files"]:
        print(f"基线: {STATE_FILE_NAME}（更新于 {state.get('updated_at') or '未知'}）")
    else:
        print(f"基线: 尚未建立——先跑 `sync_rules.py status` 确认现状，再跑 `adopt` 登记")


# ── 行级 diff ────────────────────────────────────────────────────────────────

def show_diff(result: Result, max_lines: int = 400) -> None:
    if result.raw is None or result.finv is None:
        print(f"[{result.ref.key}] 单侧缺失，无法 diff")
        return

    if result.raw.size > 20_000_000 or result.finv.size > 20_000_000:
        print(f"[{result.ref.key}] 文件过大（{result.raw.size} / {result.finv.size} 字节），"
              f"跳过行级 diff——用 `pull --dry-run` 看摘要即可")
        return

    def read_lines(path: Path) -> list[str]:
        with path.open("r", encoding="utf-8-sig", errors="replace", newline="") as handle:
            return handle.readlines()

    diff = list(difflib.unified_diff(
        read_lines(result.ref.raw_path),
        read_lines(result.ref.finv_path),
        fromfile=f"raw/{result.ref.key}",
        tofile=f"finv/{result.ref.key}",
        n=1,
    ))
    print(f"\n===== {result.ref.key}  raw -> finv =====")
    if not diff:
        print("（内容相同，仅换行/编码层差异）")
        return
    for line in diff[:max_lines]:
        print("  " + line.rstrip("\n"))
    if len(diff) > max_lines:
        print(f"  ... 还有 {len(diff) - max_lines} 行，已截断")


# ── pull ─────────────────────────────────────────────────────────────────────

def do_pull(results: list[Result], state: dict, config: dict,
            apply: bool, include_large: bool, accept_finv: bool = False) -> int:
    large_files = set(config.get("large_files", {}))
    pulled = skipped = 0

    for result in results:
        key = result.ref.key
        if result.status == IN_SYNC:
            continue

        if result.status == MISSING:
            print(f"[skip] {key}: 只在单侧存在，不自动处理")
            skipped += 1
            continue

        if result.ref.name in large_files and not include_large:
            print(f"[skip] {key}: 大文件默认不参与 pull（--include-large 可强制）")
            skipped += 1
            continue

        # 安全 pull 的充要条件：状态是 finv 领先，且登记基线收敛。
        # 不满足时只有 --accept-finv（人工看过 diff 后的显式决定）才放行。
        entry = state["files"].get(key)
        safe = result.status in PULLABLE and _baseline_is_converged(entry)
        if not safe and not accept_finv:
            print(f"[skip] {key}: {result.status}——{result.reason or '不在可拉取范围'}")
            if not _baseline_is_converged(entry):
                print(f"       基线本身是分叉的，直接 pull 会覆盖掉 raw 侧本地内容")
            print(f"       确认要采用 finv 侧 -> pull --accept-finv（先 .bak 备份）")
            print(f"       确认要保留 raw 侧 -> apply_rules.py --sync_to 推上去")
            skipped += 1
            continue

        if apply:
            backup = result.ref.raw_path.with_suffix(result.ref.raw_path.suffix + ".bak")
            if result.ref.raw_path.is_file():
                shutil.copy2(result.ref.raw_path, backup)
            _atomic_copy(result.ref.finv_path, result.ref.raw_path)

        before_lines = result.raw.lines if result.raw else 0
        detail = f"{before_lines} -> {result.finv.lines} 行"
        if result.schema_note:
            detail += f"，{result.schema_note}"
        marks = ""
        if not safe:
            marks = "  [accept-finv: 已 .bak 备份]"
        elif not apply:
            marks = "  (dry-run)"
        print(f"[pull] {key}: {detail}{marks}")

        if apply:
            state["files"][key] = _state_entry(result.ref)
        pulled += 1

    if apply and pulled:
        save_state(state)

    print()
    print(f"{'将拉取' if not apply else '已拉取'} {pulled} 个，跳过 {skipped} 个")
    if not apply and pulled:
        print("这是 dry-run，未写入任何文件。去掉 --dry-run 生效。")
    return 0


def _atomic_copy(source: Path, target: Path) -> None:
    target.parent.mkdir(parents=True, exist_ok=True)
    handle_fd, temp_name = tempfile.mkstemp(dir=str(target.parent), suffix=".tmp")
    try:
        with open(handle_fd, "wb") as out_handle, source.open("rb") as in_handle:
            shutil.copyfileobj(in_handle, out_handle)
        Path(temp_name).replace(target)
    except BaseException:
        Path(temp_name).unlink(missing_ok=True)
        raise


def _state_entry(ref: FileRef) -> dict:
    raw = fingerprint(ref.raw_path)
    finv = fingerprint(ref.finv_path)
    return {
        "raw_sha256": raw.sha256 if raw else None,
        "finv_sha256": finv.sha256 if finv else None,
        "raw_lines": raw.lines if raw else None,
        "finv_lines": finv.lines if finv else None,
        "adopted_at": datetime.now().strftime("%Y-%m-%dT%H:%M:%S"),
    }


# ── adopt ────────────────────────────────────────────────────────────────────

def do_adopt(results: list[Result], state: dict) -> int:
    registered = 0
    diverged: list[str] = []

    for result in results:
        if result.status == MISSING:
            continue
        state["files"][result.ref.key] = _state_entry(result.ref)
        registered += 1
        if result.status not in (IN_SYNC, UNREGISTERED):
            diverged.append(f"{result.ref.key} ({result.status})")

    save_state(state)
    print(f"已登记 {registered} 个文件的当前指纹为基线 -> {STATE_FILE_NAME}")

    if diverged:
        print()
        print("注意：以下文件登记时两侧内容就不一致，之后会显示为「已登记分叉」，"
              "需要人工裁决：")
        for item in diverged:
            print(f"  - {item}")
    return 0


# ── main ─────────────────────────────────────────────────────────────────────

def resolve_finv_root(config: dict, override: str | None) -> Path:
    raw_value = override or config.get("finv_root")
    if not raw_value:
        raise SystemExit(
            "未指定 finv_root：请在 config.json 里设置 finv_root，或用 --finv-root 指定"
        )
    path = Path(raw_value)
    return path if path.is_absolute() else (PROJECT_ROOT / path).resolve()


def main() -> int:
    parser = argparse.ArgumentParser(
        description="raw/ 与 finv_category_V2 规则文件同步（三方对比，只拉不推）",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("action", nargs="?", default="status",
                        choices=["status", "pull", "adopt"], help="要执行的动作")
    parser.add_argument("--engine", default=None, help="只处理指定引擎")
    parser.add_argument("--diff", action="store_true", help="对选中的文件输出行级 diff")
    parser.add_argument("--dry-run", action="store_true", help="pull 只预览，不写文件")
    parser.add_argument("--include-large", action="store_true",
                        help="允许 pull 大文件（merchant_kb.csv 等）")
    parser.add_argument("--accept-finv", action="store_true",
                        help="人工看过 diff 后，显式决定采用 finv 侧覆盖 raw"
                             "（未登记/冲突/已登记分叉也放行；覆盖前先 .bak 备份）")
    parser.add_argument("--finv-root", default=None, help="覆盖 config.json 里的 finv_root")
    args = parser.parse_args()

    config = load_config(PROJECT_ROOT)
    finv_root = resolve_finv_root(config, args.finv_root)
    if not finv_root.is_dir():
        print(f"[ERR] finv 仓库不存在: {finv_root}", file=sys.stderr)
        return 2

    engines = config.get("engines", {})
    if args.engine and args.engine not in engines:
        print(f"[ERR] 未知引擎 {args.engine}，可选: {', '.join(sorted(engines))}", file=sys.stderr)
        return 2

    results = collect(config, finv_root, args.engine)
    state = load_state()

    if args.action == "status":
        print(f"raw : {resolve_rules_base(PROJECT_ROOT, config)}")
        print(f"finv: {finv_root}")
        print()
        print_table(results)
        print_summary(results, state)
        for result in results:
            if args.diff and result.status != IN_SYNC:
                show_diff(result)
        return 0

    if args.action == "pull":
        return do_pull(results, state, config, apply=not args.dry_run,
                       include_large=args.include_large, accept_finv=args.accept_finv)

    return do_adopt(results, state)


if __name__ == "__main__":
    raise SystemExit(main())
