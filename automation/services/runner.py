from __future__ import annotations

import json
from dataclasses import asdict
from pathlib import Path

from automation.core.graph import topological_order
from automation.core.models import Plan, RunReport, RunStep, TaskKind
from automation.ports.ads import AdsPort
from automation.ports.xae import XaePort
from automation.security.audit import AuditLog
from automation.security.policy import SafetyPolicy


class Runner:
    """Execute a task DAG against an XAE/ADS port pair.

    The runner is intentionally deterministic and single-instance. It never
    decides on its own to activate hardware; the safety policy rejects that.
    """

    def __init__(
        self,
        xae: XaePort,
        ads: AdsPort,
        policy: SafetyPolicy | None = None,
        write_report: bool = True,
        audit: AuditLog | None = None,
    ) -> None:
        self.xae = xae
        self.ads = ads
        self.policy = policy or SafetyPolicy()
        self.write_report = write_report
        self.audit = audit

    def run(self, plan: Plan) -> RunReport:
        spec = plan.spec
        self.policy.assert_simulation(spec.simulation_only)
        self.policy.assert_write(spec.workspace)
        self.policy.assert_write(spec.output_dir)
        self.policy.assert_no_safety_project(spec.workspace)

        state: dict[str, object] = {
            "solution": None,
            "built": False,
            "simulated": False,
            "verified": False,
            "target_netid": "",
            "ads_value": None,
        }
        steps: list[RunStep] = []
        self._audit(
            "run_start",
            project=spec.name,
            workspace=str(spec.workspace),
            source=str(spec.source_solution),
            target=spec.ads_netid,
        )

        try:
            for task in topological_order(plan.tasks):
                try:
                    message = self._execute(task, plan, state)
                    step = RunStep(task.id, task.kind, True, message)
                    steps.append(step)
                    self._audit("step", task_id=step.task_id, kind=step.kind.value, ok=True)
                except Exception as exc:  # keep the failing task in the report
                    step = RunStep(task.id, task.kind, False, str(exc))
                    steps.append(step)
                    self._audit("step", task_id=step.task_id, kind=step.kind.value, ok=False, message=str(exc))
                    break
        finally:
            self._close_quietly()

        report = RunReport(
            project=spec.name,
            ok=bool(steps) and all(step.ok for step in steps),
            steps=tuple(steps),
            built=bool(state["built"]),
            simulated=bool(state["simulated"]),
            verified=bool(state["verified"]),
            target_netid=str(state["target_netid"]),
            ads_value=state["ads_value"],
        )
        if self.write_report:
            self._write_report(report, spec.output_dir)
        self._audit(
            "run_end",
            project=spec.name,
            ok=report.ok,
            built=report.built,
            simulated=report.simulated,
            verified=report.verified,
            target=report.target_netid,
        )
        return report

    def _execute(self, task, plan: Plan, state: dict[str, object]) -> str:
        spec = plan.spec
        solution = state["solution"]

        if task.kind is TaskKind.COPY_PROJECT:
            state["solution"] = self.xae.prepare_project(spec)
            return "project prepared"

        if task.kind is TaskKind.OPEN_SOLUTION:
            if solution is None:
                raise RuntimeError("no solution prepared")
            self.xae.open_solution(solution)
            return "solution opened"

        if task.kind is TaskKind.WRITE_PLC:
            if solution is None:
                raise RuntimeError("no solution prepared")
            self.xae.write_plc(solution, spec)
            return "PLC source written"

        if task.kind is TaskKind.BUILD:
            if solution is None:
                raise RuntimeError("no solution prepared")
            result = self.xae.build(solution, spec)
            if not result.ok or result.errors:
                raise RuntimeError(result.message or f"build failed with {result.errors} errors")
            state["built"] = True
            return result.message or "build succeeded"

        if task.kind is TaskKind.VERIFY_SYMBOL:
            if solution is None:
                raise RuntimeError("no solution prepared")
            self.xae.verify_symbol(solution, spec)
            state["verified"] = True
            return f"verified {spec.ads_symbol}"

        if task.kind is TaskKind.ACTIVATE_SIMULATION:
            if solution is None:
                raise RuntimeError("no solution prepared")
            result = self.xae.activate_simulation(solution, spec)
            if not result.ok or result.mode != "simulation":
                raise RuntimeError(f"activation rejected: {result}")
            state["simulated"] = True
            state["target_netid"] = result.target_netid
            return f"simulation activated on {result.target_netid}"

        if task.kind is TaskKind.ADS_TEST:
            self.ads.ensure_running()
            value = self.ads.read_symbol(spec.ads_symbol, spec.ads_type)
            if spec.expected_ads_value is not None and value != spec.expected_ads_value:
                raise RuntimeError(
                    f"ADS assertion failed: {spec.ads_symbol}={value!r}, "
                    f"expected {spec.expected_ads_value!r}"
                )
            state["ads_value"] = value
            return f"read {spec.ads_symbol}"

        if task.kind is TaskKind.SNAPSHOT:
            return "snapshot scheduled"

        raise RuntimeError(f"unsupported task kind: {task.kind}")

    def _write_report(self, report: RunReport, output_dir: Path) -> Path:
        output_dir.mkdir(parents=True, exist_ok=True)
        path = output_dir / "run-report.json"
        path.write_text(
            json.dumps(asdict(report), indent=2, ensure_ascii=False, default=str),
            encoding="utf-8",
        )
        return path

    def _close_quietly(self) -> None:
        for close in (self.xae.close, self.ads.close):
            try:
                close()
            except Exception:
                pass

    def _audit(self, event: str, **fields) -> None:
        if self.audit is None:
            return
        try:
            self.audit.record(event, **fields)
        except Exception:
            # The deterministic run report remains authoritative.
            pass
