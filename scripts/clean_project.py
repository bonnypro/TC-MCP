from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import time
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
WORKSPACE = ROOT.parent
TEST_ROOT = WORKSPACE / "TEST"
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from automation.context import RetentionPolicy, RoundStore

PROTECTED_NAMES = {
    ".git",
    ".opencode",
    "app",
    "automation",
    "config",
    "data",
    "docs",
    "scripts",
    "templates",
    "tests",
    "AGENTS.md",
    "LICENSE",
    "README.md",
    "THIRD_PARTY_NOTICES.md",
    "automation_mcp_server.py",
    "mcp_server.py",
    "opencode.jsonc",
    "requirements.txt",
}

TC_ARTIFACT_DIRS = ("runtime", "tmp", "updates", "vendor", ".pytest_cache")
TEST_RUN_DIRS = ("scope-runs", "scope-research", "evolution/scope-runs")
TEST_EVIDENCE_PREFIXES = ("scope-acceptance-", "scope-raw-")
ROUNDS_DIR = TEST_ROOT / "rounds"
ROUNDS_DIRS = (ROUNDS_DIR, TEST_ROOT / "evolution" / "rounds")


@dataclass(frozen=True)
class Candidate:
    path: str
    category: str
    bytes: int
    age_days: float
    pinned: bool
    reason: str


@dataclass(frozen=True)
class CleanupPolicy:
    tc_root: Path = ROOT
    test_root: Path = TEST_ROOT
    older_than_days: float = 7.0
    all_generated: bool = False
    include_evidence: bool = False
    include_pinned: bool = False


def _size(path: Path) -> int:
    if path.is_file():
        return path.stat().st_size
    total = 0
    for current, _dirs, files in os.walk(path):
        for name in files:
            try:
                total += (Path(current) / name).stat().st_size
            except OSError:
                pass
    return total


def _age_days(path: Path) -> float:
    try:
        return max(0.0, (time.time() - path.stat().st_mtime) / 86400.0)
    except OSError:
        return 0.0


def _is_pinned(path: Path, test_root: Path) -> bool:
    if (path / "KEEP").exists() or (path / "PIN").exists():
        return True
    marker = test_root / "cleanup" / "pinned.json"
    if marker.is_file():
        try:
            data = json.loads(marker.read_text(encoding="utf-8"))
            names = {str(item) for item in data.get("keep", [])}
            return path.name in names
        except Exception:
            pass
    return False


def _within(path: Path, root: Path) -> bool:
    try:
        path.resolve().relative_to(root.resolve())
        return True
    except ValueError:
        return False


def _candidate(path: Path, category: str, policy: CleanupPolicy) -> Candidate | None:
    if path.is_symlink() or not path.exists():
        return None
    if not _within(path, policy.tc_root) and not _within(path, policy.test_root):
        return None
    pinned = _is_pinned(path, policy.test_root) if _within(path, policy.test_root) else False
    if pinned and not policy.include_pinned:
        return None
    age = _age_days(path)
    if not policy.all_generated and age < policy.older_than_days:
        return None
    return Candidate(
        path=str(path),
        category=category,
        bytes=_size(path),
        age_days=round(age, 2),
        pinned=pinned,
        reason="pinned evidence" if pinned else f"{age:.1f} days old",
    )


def build_plan(policy: CleanupPolicy) -> list[Candidate]:
    candidates: list[Candidate] = []
    for name in TC_ARTIFACT_DIRS:
        path = policy.tc_root / name
        item = _candidate(path, "tc-artifact", policy)
        if item:
            candidates.append(item)
    for path in policy.tc_root.rglob("__pycache__"):
        if not _within(path, policy.tc_root):
            continue
        item = _candidate(path, "python-cache", policy)
        if item and not any(Path(existing.path) in path.parents for existing in candidates):
            candidates.append(item)
    for path in policy.tc_root.rglob("*.pyc"):
        if "__pycache__" in path.parts:
            continue
        item = _candidate(path, "python-cache", policy)
        if item and not any(Path(existing.path) in path.parents for existing in candidates):
            candidates.append(item)
    if policy.test_root.is_dir():
        for name in TEST_RUN_DIRS:
            base = policy.test_root / name
            if not base.is_dir():
                continue
            for child in base.iterdir():
                item = _candidate(child, "test-run", policy)
                if item:
                    candidates.append(item)
        for child in policy.test_root.iterdir():
            if not child.is_dir():
                continue
            if not any(child.name.startswith(prefix) for prefix in TEST_EVIDENCE_PREFIXES):
                continue
            if not policy.include_evidence:
                continue
            item = _candidate(child, "test-evidence", policy)
            if item:
                candidates.append(item)
    return sorted(candidates, key=lambda item: item.path)


def _xae_running() -> bool:
    try:
        result = subprocess.run(
            ["tasklist", "/FI", "IMAGENAME eq TcXaeShell.exe"],
            capture_output=True,
            text=True,
            encoding="mbcs",
            errors="ignore",
            timeout=10,
        )
        return "TcXaeShell.exe" in result.stdout
    except Exception:
        return False


def apply_plan(plan: list[Candidate], policy: CleanupPolicy) -> dict:
    if _xae_running() and any(item.category in {"tc-artifact", "test-run"} for item in plan):
        raise RuntimeError("TcXaeShell is running; close it before deleting runtime/test-run artifacts")
    deleted: list[dict] = []
    skipped: list[dict] = []
    for item in plan:
        path = Path(item.path)
        if not _within(path, policy.tc_root) and not _within(path, policy.test_root):
            skipped.append({**asdict(item), "reason": "outside allowed roots"})
            continue
        if path.name in PROTECTED_NAMES:
            skipped.append({**asdict(item), "reason": "protected name"})
            continue
        try:
            if path.is_dir():
                shutil.rmtree(path)
            else:
                path.unlink()
            deleted.append(asdict(item))
        except Exception as exc:
            skipped.append({**asdict(item), "reason": f"{type(exc).__name__}: {exc}"})
    return {"deleted": deleted, "skipped": skipped, "bytes": sum(item["bytes"] for item in deleted)}


def write_manifest(result: dict, policy: CleanupPolicy, dry_run: bool, round_result: dict | None = None) -> Path:
    out = policy.test_root / "cleanup"
    out.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    path = out / f"cleanup-{stamp}.json"
    path.write_text(
        json.dumps(
            {
                "dry_run": dry_run,
                "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                "policy": {
                    "older_than_days": policy.older_than_days,
                    "all_generated": policy.all_generated,
                    "include_evidence": policy.include_evidence,
                    "include_pinned": policy.include_pinned,
                },
                "rounds": round_result or {},
                **result,
            },
            indent=2,
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    return path


def main() -> int:
    parser = argparse.ArgumentParser(description="Preview or remove generated TC-MCP/TEST artifacts.")
    parser.add_argument("--apply", action="store_true", help="delete; default is dry-run")
    parser.add_argument("--all", action="store_true", help="include recent generated artifacts")
    parser.add_argument("--include-evidence", action="store_true", help="include scope-acceptance/scope-raw evidence")
    parser.add_argument("--include-pinned", action="store_true", help="include pinned evidence (dangerous)")
    parser.add_argument("--older-than", type=float, default=7.0, help="days; default 7")
    parser.add_argument("--no-rounds", action="store_true", help="skip round raw/summary retention")
    args = parser.parse_args()

    policy = CleanupPolicy(
        older_than_days=max(0.0, args.older_than),
        all_generated=args.all,
        include_evidence=args.include_evidence,
        include_pinned=args.include_pinned,
    )
    round_result = {"raw_pruned": [], "rounds_deleted": [], "bytes": 0, "tasks": {}}
    if not args.no_rounds:
        for rounds_dir in ROUNDS_DIRS:
            if not rounds_dir.is_dir():
                continue
            round_store = RoundStore(rounds_dir)
            result = round_store.prune(RetentionPolicy(), dry_run=not args.apply)
            for key in ("raw_pruned", "rounds_deleted"):
                round_result[key].extend(result.get(key, []))
            round_result["bytes"] += int(result.get("bytes", 0))
            for task, detail in result.get("tasks", {}).items():
                round_result["tasks"][f"{rounds_dir.name}/{task}"] = detail
        print(
            "rounds: raw_pruned={} summaries_deleted={} bytes={:.2f} MiB".format(
                len(round_result["raw_pruned"]),
                len(round_result["rounds_deleted"]),
                round_result["bytes"] / 1024 / 1024,
            )
        )
    plan = build_plan(policy)
    total = sum(item.bytes for item in plan)
    print(f"{'DRY RUN' if not args.apply else 'DELETE'} candidates: {len(plan)}, {total / 1024 / 1024:.2f} MiB")
    for item in plan:
        print(f"  {item.category:14} {item.bytes / 1024 / 1024:9.2f} MiB  {item.path}  ({item.reason})")
    if not plan:
        if args.apply and (round_result["raw_pruned"] or round_result["rounds_deleted"]):
            manifest = write_manifest(
                {"deleted": [], "skipped": [], "bytes": 0},
                policy,
                dry_run=False,
                round_result=round_result,
            )
            print(f"round retention applied; manifest: {manifest}")
        else:
            print("nothing to do")
        return 0
    if not args.apply:
        print("run again with --apply to delete")
        return 0
    result = apply_plan(plan, policy)
    manifest = write_manifest(result, policy, dry_run=False, round_result=round_result)
    print(f"deleted {len(result['deleted'])} items, {result['bytes'] / 1024 / 1024:.2f} MiB")
    print(f"manifest: {manifest}")
    return 0 if not result["skipped"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
