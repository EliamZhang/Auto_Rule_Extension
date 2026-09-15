#!/usr/bin/env python3
"""ServiFlow-AI (GitHub) → raw/：直接 HTTPS 下载上游规则文件。

用法:
    python scripts/sync_upstream.py                    # 拉取并写入 raw/
    python scripts/sync_upstream.py --dry-run          # 只报告差异，不写任何文件
    python scripts/sync_upstream.py --branch Finv_category_v2
    python scripts/sync_upstream.py --accept-upstream  # 本地有改动时仍采用上游版本

链路:
    GitHub (ServiFlow-AI)  ──本脚本──→  raw/     ← 各引擎规则 CSV，直接下载

    finv_category_V2 只被**只读地**看一眼 HEAD，用来报告它落后上游多少 ——
    本脚本不 fetch / 不 checkout / 不写入它任何文件。

范围:
    raw/<引擎目录>/*.csv（26 个文件，含 config.json 漏登记的 income_config.csv、
    bnpl_maximum_limits.csv 和 4 个 transfer pattern 文件），外加
    raw/category_catalog.json（分类→引擎路由表，非规则但会进 gap_summary）。
    不碰 finv；不创建本地没有的引擎目录。

为什么不让 finv 中转:
    上一版走「GitHub → finv 工作副本快进 → sync_rules.py pull → raw/」。第一步
    必须 git fetch + 切分支（本地在 Finv_category_v2，上游是 main），会改变用户
    正在跑流水线的那个环境。规则 CSV 本身在两边是同构的，直接下载即可，没必要
    为此动用户的 git 工作区。

行尾:
    上游 GitHub 按索引内容发 LF，而本地 raw/ 与 finv 工作副本是 CRLF
    （finv 仓库 core.autocrlf 把 LF 检出成 CRLF，raw/ 又从 finv 复制而来）。
    因此**内容比对一律先做 CRLF→LF 归一**，否则 18 个文件会被误判成"有差异"；
    写入时再转回该文件既有的行尾风格 —— 保持 raw/ 与 finv 字节可比，
    免得 sync_rules.py 平白报出一堆漂移。

硬约束:
    - **只拉不推**。推送仍然只能走 apply_rules.py --sync_to 的审批门。
    - **覆盖前强制 `.bak` 备份**（与 sync_rules.py 同名同位置）。
    - **本地有未登记改动时拒绝覆盖该文件**；--accept-upstream 是显式逃生门。
    - **HTTP 条件请求**（If-None-Match）：上游未变的文件不下载正文，
      merchant_kb.csv（74 MB）因此几乎不产生流量。
    - 任何一步不满足 → 只报告该文件，其余文件照常处理，退出码非 0。
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import subprocess
import sys
import tempfile
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from common import load_config  # noqa: E402

PROJECT_ROOT = Path(__file__).resolve().parent.parent
STATE_FILE = ".upstream_state.json"
CACHE_DIR = ".upstream_cache"          # blobless 抓取用的裸仓库，无文件正文
USER_AGENT = "Auto-Rule-Extension/sync_upstream"
HTTP_TIMEOUT = 300
DEFAULT_BRANCH = "main"

# 非引擎目录的受跟踪文件：raw 内相对路径 → 仓库内相对路径。
# category_catalog.json 不是规则，但它会原样进 gap_summary 的 category_catalog
# 字段供 skill 读——陈旧的 owner_engine_id 会误导引擎归属判断。
EXTRA_FILES: dict[str, str] = {
    "category_catalog.json": "configs/category_catalog.json",
}

# 动作
UNCHANGED = "unchanged"   # 上游未变（304）
IDENTICAL = "identical"   # 下载了，但内容与本地一致
WRITTEN = "written"       # 已备份并覆盖
REFUSED = "refused"       # 本地有改动，拒绝覆盖
MISSING = "missing"       # 上游没有这个文件


# ── helpers ──────────────────────────────────────────────────────────────────

def normalize_newlines(data: bytes) -> bytes:
    """CRLF / 孤立 CR → LF。比对前必须过这一道（见模块 docstring 的「行尾」）。"""
    return data.replace(b"\r\n", b"\n").replace(b"\r", b"\n")


def detect_line_ending(data: bytes) -> bytes:
    """该文件既有的行尾风格，用于写回时保持一致。"""
    idx = data.find(b"\n")
    return b"\r\n" if idx > 0 and data[idx - 1:idx] == b"\r" else b"\n"


def to_line_ending(data: bytes, style: bytes) -> bytes:
    if style == b"\r\n":
        return normalize_newlines(data).replace(b"\n", b"\r\n")
    return normalize_newlines(data)


def content_sha(data: bytes) -> str:
    """归一化后的内容 sha256 —— 与行尾无关，可跨平台比对。"""
    return hashlib.sha256(normalize_newlines(data)).hexdigest()


def local_snapshot(path: Path) -> tuple[str | None, bytes]:
    """本地文件的 (归一化内容 sha256, 行尾风格)；文件不存在则 (None, LF)。"""
    if not path.is_file():
        return None, b"\n"
    data = path.read_bytes()
    return content_sha(data), detect_line_ending(data)


def derive_raw_base(url: str, branch: str) -> str:
    """https://github.com/o/r[.git] + main → https://raw.githubusercontent.com/o/r/main"""
    u = url.strip().rstrip("/")
    if u.endswith(".git"):
        u = u[:-4]
    u = re.sub(r"^https?://github\.com/", "https://raw.githubusercontent.com/", u)
    return f"{u}/{branch}"


def discover_files(config: dict) -> list[tuple[Path, str]]:
    """枚举 (本地 raw 路径, 仓库内相对路径)。

    以 raw/ 目录本身为准而非 config 的 rule_files —— 后者是「规则创作清单」，
    实测漏掉 income_config.csv、bnpl_maximum_limits.csv 和 4 个 transfer 文件。

    只收 `raw/<引擎目录>/*.csv`，外加 EXTRA_FILES 里单独登记的少数非规则文件
    （category_catalog.json）。它历史上正是因为「只跟 .csv」而静默漂移过：
    Gambling 的 owner_engine_id 停在 initial,catch_all 无人发现。
    """
    rules_base = PROJECT_ROOT / config.get("rules_base_dir", "raw")
    by_engine_dir: dict[str, dict] = {}
    for engine_id, engine_config in config.get("engines", {}).items():
        by_engine_dir[engine_config.get("engine_dir", f"{engine_id}_rule")] = engine_config

    found: list[tuple[Path, str]] = []
    for local in sorted(rules_base.rglob("*.csv")):
        rel = local.relative_to(rules_base).as_posix()
        subdir, sep, name = rel.partition("/")
        if not sep:
            continue
        engine_config = by_engine_dir.get(subdir)
        if engine_config is None:
            continue
        parts = [
            engine_config.get("finv_engine_dir", f"{subdir[:-5]}_engine"),
            engine_config.get("finv_rule_dir", "resources"),
            name,
        ]
        found.append((local, "/".join(p for p in parts if p)))

    for local_rel, repo_rel in EXTRA_FILES.items():
        local = rules_base / local_rel
        if local.is_file():
            found.append((local, repo_rel))

    return found


def fetch(url: str, etag: str | None) -> tuple[int, bytes | None, str | None]:
    """返回 (status, body, new_etag)。304 时 body 为 None。"""
    req = urllib.request.Request(url)
    req.add_header("User-Agent", USER_AGENT)
    if etag:
        req.add_header("If-None-Match", etag)
    try:
        with urllib.request.urlopen(req, timeout=HTTP_TIMEOUT) as resp:
            if resp.status == 304:
                return 304, None, etag
            return resp.status, resp.read(), resp.headers.get("ETag")
    except urllib.error.HTTPError as e:
        if e.code == 304:
            return 304, None, etag
        if e.code == 404:
            return 404, None, None
        raise


def write_atomic(local: Path, data: bytes) -> None:
    local.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(dir=str(local.parent), prefix=f"{local.name}.", suffix=".tmp")
    try:
        with os.fdopen(fd, "wb") as f:
            f.write(data)
        os.replace(tmp_name, local)
    except BaseException:
        Path(tmp_name).unlink(missing_ok=True)
        raise


def git_out(args: list[str], cwd: Path | None = None) -> str | None:
    try:
        proc = subprocess.run(
            ["git", *args],
            cwd=str(cwd) if cwd else None,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=120,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    return proc.stdout.strip() if proc.returncode == 0 else None


def upstream_snapshot(url: str, branch: str) -> tuple[str | None, list[str]]:
    """(上游 commit sha, 上游全部文件路径)。失败返回 (None, [])。

    用 blobless + depth=1 的浅抓取，只取 commit 和 tree、**不下载任何文件正文**
    （几十 KB），因此无速率限制问题 —— GitHub REST API 的匿名额度只有 60 次/小时，
    用它会让这个检查在限流时静默消失。也不用 finv 工作副本，那需要先 git fetch，
    正是本脚本要避开的动作。

    得到文件清单的用途只有一个：发现「上游有、本地根本没跟踪」的规则文件 ——
    例如上游新增了一整个引擎。本脚本以 raw/ 为准枚举待同步文件，不照这份清单建文件。
    """
    cache = PROJECT_ROOT / CACHE_DIR
    if not (cache / "HEAD").exists():
        cache.mkdir(parents=True, exist_ok=True)
        if git_out(["init", "--bare", "--quiet", str(cache)]) is None:
            return None, []
    if git_out(["fetch", "--quiet", "--filter=blob:none", "--depth=1",
                url, f"+refs/heads/{branch}:refs/heads/{branch}"], cwd=cache) is None:
        return None, []
    sha = git_out(["rev-parse", f"refs/heads/{branch}"], cwd=cache)
    listing = git_out(["ls-tree", "-r", "--name-only", f"refs/heads/{branch}"], cwd=cache)
    return sha, (listing.splitlines() if listing else [])


def finv_head(finv_root: Path) -> str | None:
    if not (finv_root / ".git").exists():
        return None
    return git_out(["rev-parse", "HEAD"], cwd=finv_root)


# ── main ─────────────────────────────────────────────────────────────────────

def sync(raw_base: str, url: str, branch: str, finv_root: Path | None,
         dry_run: bool, accept_upstream: bool) -> int:
    config = load_config(PROJECT_ROOT)
    rules_base = PROJECT_ROOT / config.get("rules_base_dir", "raw")

    state_path = PROJECT_ROOT / STATE_FILE
    state: dict = {"files": {}}
    if state_path.exists():
        try:
            state = json.loads(state_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            print(f"[warn] {STATE_FILE} 无法解析，按空状态处理", file=sys.stderr)
    files_state: dict = state.setdefault("files", {})

    targets = discover_files(config)
    print(f"上游   : {raw_base}")
    print(f"本地   : {rules_base}")
    print(f"文件   : {len(targets)} 个" + ("（dry-run，不写任何文件）" if dry_run else ""))
    print()

    upstream_sha, upstream_files = upstream_snapshot(url, branch)

    counts = {UNCHANGED: 0, IDENTICAL: 0, WRITTEN: 0, REFUSED: 0, MISSING: 0}
    notes: list[str] = []

    for local, repo_rel in targets:
        key = local.relative_to(rules_base).as_posix()
        display = key
        record = files_state.get(key)
        old_etag = record.get("etag") if record else None
        local_sha, line_ending = local_snapshot(local)

        try:
            status, body, new_etag = fetch(f"{raw_base}/{repo_rel}", old_etag)
        except Exception as exc:  # noqa: BLE001 — 网络层任何异常都只影响这一个文件
            print(f"[err ] {display}  下载失败: {exc}")
            notes.append(f"{display}: 下载失败（{exc}）")
            continue

        if status == 404:
            counts[MISSING] += 1
            print(f"[miss] {display}  上游不存在: {repo_rel}")
            continue

        if status == 304:
            # 上游未变。本地若与上次记录不一致，说明是本地改的 —— 那是「raw 领先」，归 push 方向
            if record and local_sha != record.get("content_sha256"):
                print(f"[warn] {display}  上游未变，本地已改动（待推送到 finv）")
            else:
                counts[UNCHANGED] += 1
            continue

        remote_sha = content_sha(body)

        if local_sha == remote_sha:
            counts[IDENTICAL] += 1
            print(f"[ok  ] {display}  已是最新")
            files_state[key] = {"repo_path": repo_rel, "etag": new_etag,
                                "content_sha256": remote_sha}
            continue

        # 内容不同 → 准备覆盖。先判断本地是不是「我们自己改的」
        if record is None:
            reason = "未登记（首次同步）且与上游不同"
        elif local_sha != record.get("content_sha256"):
            reason = "本地自上次同步后被改动"
        else:
            reason = None

        if reason and not accept_upstream:
            counts[REFUSED] += 1
            print(f"[skip] {display}  {reason}，拒绝覆盖")
            notes.append(f"{display}: {reason}")
            continue

        size_note = f"{len(body):,} B"
        if dry_run:
            counts[WRITTEN] += 1
            print(f"[diff] {display}  内容不同（{size_note}），将备份为 .bak 后覆盖")
            continue

        if local_sha is not None:
            backup = local.parent / f"{local.name}.bak"
            try:
                with open(local, "rb") as src, open(backup, "wb") as dst:
                    for chunk in iter(lambda: src.read(1 << 20), b""):
                        dst.write(chunk)
            except OSError as exc:
                counts[REFUSED] += 1
                print(f"[skip] {display}  备份失败，未覆盖: {exc}")
                notes.append(f"{display}: 备份失败（{exc}）")
                continue

        write_atomic(local, to_line_ending(body, line_ending))
        counts[WRITTEN] += 1
        print(f"[pull] {display}  {size_note}  ← 已更新（旧版备份为 .bak）")
        files_state[key] = {"repo_path": repo_rel, "etag": new_etag,
                            "content_sha256": remote_sha}

    if not dry_run:
        state["upstream"] = {
            "url": url,
            "branch": branch,
            "commit": upstream_sha,
            "synced_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        }
        state_path.write_text(
            json.dumps(state, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )

    print()
    print(
        f"合计: 已最新 {counts[IDENTICAL]} · 上游未变 {counts[UNCHANGED]} · "
        f"更新 {counts[WRITTEN]} · 拒绝 {counts[REFUSED]} · 缺失 {counts[MISSING]}"
    )

    if dry_run:
        print("(dry-run：以上未写入任何文件)")
    elif counts[WRITTEN]:
        print(f"上游快照已记录到 {STATE_FILE}")

    # 上游有、本地未跟踪的规则文件（例如上游新增的引擎）—— 只报告，绝不自动创建
    if upstream_files:
        covered = {repo_rel for _, repo_rel in targets}
        untracked = sorted(
            p for p in upstream_files
            if p.endswith(".csv") and "_engine/" in p and p not in covered
        )
        if untracked:
            print()
            print(f"上游有、本地未跟踪的规则文件（{len(untracked)} 个）：")
            for p in untracked:
                print(f"  - {p}")
            print("  这些不属于 config.json 里任何引擎，本脚本不会自动创建 ——")
            print("  是否纳入本项目需要人工决定。")

    # finv 只读报告 —— 它跑流水线，规则和引擎代码都可能落后
    if finv_root and finv_root.is_dir():
        head = finv_head(finv_root)
        upstream_sha = state.get("upstream", {}).get("commit") or upstream_sha
        if head and upstream_sha:
            if head == upstream_sha:
                print(f"finv   : {finv_root} 与上游一致（{head[:12]}）")
            else:
                print()
                print(f"finv   : {finv_root}")
                print(f"         本地 {head[:12]}  ≠  上游 {upstream_sha[:12]}")
                print("         规则文件已由本脚本对齐，但引擎代码没有 —— 判断规则行为时注意。")
                print("         需要更新它请自行在该仓库 git pull（本脚本不代劳）。")

    if notes:
        print()
        print("需要人工处理：")
        for note in notes:
            print(f"  - {note}")
        print("  处理方式：看过差异后，确认采用上游版本再加 --accept-upstream 重跑。")
        return 1

    return 0


def main() -> int:
    parser = argparse.ArgumentParser(
        description="从 GitHub 上游直接下载规则文件到 raw/（不经过 finv_category_V2）",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--branch", default=None,
                        help=f"上游分支（默认取 config.json 的 upstream.branch，再默认 {DEFAULT_BRANCH}）")
    parser.add_argument("--url", default=None, help="覆盖 config.json 的 upstream.url")
    parser.add_argument("--finv-root", default=None, help="覆盖 config.json 的 finv_root（仅用于只读报告）")
    parser.add_argument("--dry-run", action="store_true", help="只报告差异，不写任何文件")
    parser.add_argument("--accept-upstream", action="store_true",
                        help="本地有未登记改动时仍采用上游版本（覆盖前照常 .bak 备份）")
    args = parser.parse_args()

    config = load_config(PROJECT_ROOT)
    upstream_cfg = config.get("upstream", {})

    url = args.url or upstream_cfg.get("url")
    if not url:
        print("[ERR] config.json 缺少 upstream.url，请补上或用 --url 指定", file=sys.stderr)
        return 2
    branch = args.branch or upstream_cfg.get("branch") or DEFAULT_BRANCH

    finv_raw = args.finv_root or config.get("finv_root")
    finv_root = None
    if finv_raw:
        finv_root = Path(finv_raw)
        if not finv_root.is_absolute():
            finv_root = (PROJECT_ROOT / finv_root).resolve()

    return sync(
        raw_base=derive_raw_base(url, branch),
        url=url,
        branch=branch,
        finv_root=finv_root,
        dry_run=args.dry_run,
        accept_upstream=args.accept_upstream,
    )


if __name__ == "__main__":
    sys.exit(main())
