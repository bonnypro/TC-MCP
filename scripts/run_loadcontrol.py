from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def main() -> int:
    parser = argparse.ArgumentParser(description="Run LoadControl and write a compact result JSON.")
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--setpoint", type=float, default=1000.0)
    parser.add_argument("--hold", type=float, default=1.0)
    parser.add_argument("--timeout", type=float, default=30.0)
    parser.add_argument("--keep-runtime", action="store_true")
    args = parser.parse_args()

    query = subprocess.run(
        ["sc", "query", "TcSysSrv"],
        capture_output=True,
        text=True,
        encoding="mbcs",
        errors="replace",
    )
    if query.returncode != 0 or "RUNNING" not in query.stdout.upper():
        print(
            "TcSysSrv is not running. Start it as administrator or reboot, then retry.",
            file=sys.stderr,
        )
        return 2

    from automation.evolution.runtime_guard import RuntimeGuard, RuntimeGuardError
    from automation.services.session import AutomationSession

    guard = RuntimeGuard(task="run_loadcontrol")
    try:
        guard.wait()
    except RuntimeGuardError as exc:
        print(f"runtime guard: {exc}", file=sys.stderr)
        return 2

    started = time.monotonic()
    session = AutomationSession()
    workspace = None
    result: dict = {}
    success = False
    try:
        result = session.run_loadcontrol(args.setpoint, args.hold, args.timeout)
        workspace = session.workspace
        success = bool(result.get("ok"))
    finally:
        session.close()
        guard.after(success=success, note="run_loadcontrol")

    checks = dict(result.get("checks", {}))
    retry_s = float(result.get("infrastructure_retry_s", 0.0) or 0.0)
    wall_s = round(time.monotonic() - started, 3)
    payload = {
        "schema_version": 1,
        "ok": bool(result.get("ok", False)),
        "duration_s": wall_s,
        "effective_duration_s": round(max(0.0, wall_s - retry_s), 3),
        "task_duration_s": float(result.get("task_duration_s", 0.0) or 0.0),
        "infrastructure_retry_s": retry_s,
        "retry_used": bool(result.get("retry_used", False)),
        "checks": checks,
        "metrics": {
            "max_actual_lbs": float(result.get("max_actual_lbs", 0.0)),
            "hold_elapsed_s": float(result.get("hold_elapsed_s", 0.0)),
            "final_actual_lbs": float(result.get("final_actual_lbs", 0.0)),
        },
        "activation": result.get("activation"),
    }
    args.out.mkdir(parents=True, exist_ok=True)
    (args.out / "result.json").write_text(
        json.dumps(payload, indent=2, ensure_ascii=False, default=str),
        encoding="utf-8",
    )
    if workspace is not None and not args.keep_runtime:
        shutil.rmtree(workspace, ignore_errors=True)
    print(json.dumps(payload, ensure_ascii=False, default=str))
    return 0 if payload["ok"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
