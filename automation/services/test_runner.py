from __future__ import annotations

import json
import time
import uuid
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from pathlib import Path

from automation.security.policy import WRITE_ROOT


class TestPlanError(ValueError):
    pass


def _compare(actual, op: str, expected, tolerance: float = 0.0) -> bool:
    op = op.casefold()
    if op == "eq":
        if tolerance and isinstance(actual, (int, float)) and isinstance(expected, (int, float)):
            return abs(float(actual) - float(expected)) <= tolerance
        return actual == expected
    if op == "ne":
        return actual != expected
    if op == "gt":
        return actual > expected
    if op == "ge":
        return actual >= expected
    if op == "lt":
        return actual < expected
    if op == "le":
        return actual <= expected
    raise TestPlanError(f"unsupported condition operator: {op}")


class TestPlanRunner:
    """Small declarative test runner over an AutomationSession."""

    def __init__(self, session) -> None:
        self.session = session

    def run(self, plan: dict, name: str | None = None, timeout_s: float = 300.0) -> dict:
        if not isinstance(plan, dict):
            raise TestPlanError("plan must be an object")
        steps = list(plan.get("steps") or [])
        if not steps:
            raise TestPlanError("plan must contain at least one step")
        setup = list(plan.get("setup") or [])
        teardown = list(plan.get("teardown") or [])
        test_name = name or str(plan.get("name", "test"))
        stop_on_failure = bool(plan.get("stop_on_failure", True))
        start_wall = datetime.now(timezone.utc)
        started = time.monotonic()
        run_id = datetime.now().strftime("%Y%m%d-%H%M%S") + "-" + uuid.uuid4().hex[:8]
        report_root = getattr(getattr(self.session, "config", None), "session", None)
        report_root = report_root.report_root if report_root is not None else WRITE_ROOT / "reports"
        report_dir = report_root / run_id
        report_dir.mkdir(parents=True, exist_ok=True)
        results: list[dict] = []
        checks: list[dict] = []
        timed_out = False

        def execute(step: dict) -> dict:
            nonlocal timed_out
            if time.monotonic() - started > timeout_s:
                timed_out = True
                return {"id": step.get("id", ""), "kind": step.get("kind", ""), "ok": False, "error": "test plan timeout"}
            kind = str(step.get("kind", "")).casefold()
            step_id = str(step.get("id", kind))
            try:
                if kind == "build":
                    value = self.session.build()
                    return {"id": step_id, "kind": kind, "ok": bool(value.get("ok")), "details": value}
                if kind == "activate":
                    value = self.session.activate()
                    return {"id": step_id, "kind": kind, "ok": bool(value.get("ok")), "details": value}
                if kind == "write":
                    value = self.session.ads_write_many(list(step.get("writes") or []))
                    return {"id": step_id, "kind": kind, "ok": True, "details": value}
                if kind == "read":
                    value = self.session.ads_read_many(list(step.get("symbols") or []))
                    return {"id": step_id, "kind": kind, "ok": True, "details": value}
                if kind == "wait":
                    value = self.session.ads_wait(
                        str(step["symbol"]),
                        step.get("value"),
                        str(step.get("type", "BOOL")),
                        float(step.get("timeout_s", 10.0)),
                        int(step.get("interval_ms", 50)),
                        float(step.get("tolerance", 0.0)),
                    )
                    ok = bool(value.get("ok"))
                    checks.append({"id": step_id, "ok": ok, "expected": step.get("value"), "actual": value.get("observed")})
                    return {"id": step_id, "kind": kind, "ok": ok, "details": value}
                if kind == "assert":
                    value = self.session.ads_read(str(step["symbol"]), str(step.get("type", "BOOL")))
                    ok = _compare(value.get("value"), str(step.get("op", "eq")), step.get("value"), float(step.get("tolerance", 0.0)))
                    checks.append({"id": step_id, "ok": ok, "expected": step.get("value"), "actual": value.get("value")})
                    return {"id": step_id, "kind": kind, "ok": ok, "details": value}
                if kind == "snapshot":
                    value = self.session.snapshot(str(step.get("label", step_id)))
                    return {"id": step_id, "kind": kind, "ok": True, "details": value}
                if kind == "sleep":
                    time.sleep(float(step.get("seconds", 0.1)))
                    return {"id": step_id, "kind": kind, "ok": True}
                raise TestPlanError(f"unsupported step kind: {kind}")
            except Exception as exc:
                return {"id": step_id, "kind": kind, "ok": False, "error": str(exc)}

        for step in setup:
            result = execute(step)
            results.append(result)
            if not result["ok"]:
                break
        else:
            for step in steps:
                result = execute(step)
                results.append(result)
                if not result["ok"] and stop_on_failure:
                    break

        for step in teardown:
            results.append(execute(step))

        ok = all(result["ok"] for result in results) and not timed_out
        finished = datetime.now(timezone.utc).isoformat(timespec="seconds")
        report = {
            "run_id": run_id,
            "name": test_name,
            "ok": ok,
            "timed_out": timed_out,
            "started_at": start_wall.isoformat(timespec="seconds"),
            "finished_at": finished,
            "duration_ms": round((time.monotonic() - started) * 1000, 1),
            "steps": results,
            "checks": checks,
            "artifacts": [],
        }
        report_path = report_dir / "report.json"
        report_path.write_text(json.dumps(report, indent=2, ensure_ascii=False, default=str), encoding="utf-8")
        junit_path = report_dir / "junit.xml"
        self._write_junit(test_name, results, junit_path)
        report["artifacts"] = [
            {"name": "report.json", "path": str(report_path)},
            {"name": "junit.xml", "path": str(junit_path)},
        ]
        report_path.write_text(json.dumps(report, indent=2, ensure_ascii=False, default=str), encoding="utf-8")
        return report

    @staticmethod
    def _write_junit(name: str, results: list[dict], path: Path) -> None:
        suite = ET.Element("testsuite", name=name, tests=str(len(results)))
        failures = 0
        for result in results:
            case = ET.SubElement(suite, "testcase", name=f"{result['kind']}:{result['id']}")
            if not result.get("ok"):
                failures += 1
                failure = ET.SubElement(case, "failure", message=str(result.get("error", "failed")))
                failure.text = json.dumps(result.get("details", {}), ensure_ascii=False, default=str)
        suite.set("failures", str(failures))
        ET.ElementTree(suite).write(path, encoding="utf-8", xml_declaration=True)
