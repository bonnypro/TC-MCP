from __future__ import annotations

import hashlib
import json
import re
import shutil
import time
import zipfile
from dataclasses import asdict
from datetime import datetime
from pathlib import Path

from automation.adapters.ads_pyads import PyadsAdapter
from automation.adapters.tc_xml_validator import TcXmlValidator
from automation.adapters.xae_com import XaeDteAdapter
from automation.core.config import load_config
from automation.core.models import ProjectSpec
from automation.examples.loadcontrol import loadcontrol_scope_gvl, loadcontrol_sources
from automation.services.licensing import require_licenses
from automation.services.revisions import project_revision
from automation.security.audit import AuditLog
from automation.security.lock import RunLock
from automation.security.policy import SafetyPolicy, WRITE_ROOT
from automation.validation.st_review import review_st


class AutomationSession:
    """Persistent XAE/ADS session owned by the automation MCP server."""

    def __init__(self) -> None:
        self.config = load_config()
        self.validator = TcXmlValidator()
        self.xae: XaeDteAdapter | None = None
        self.ads: PyadsAdapter | None = None
        self.spec: ProjectSpec | None = None
        self.solution: Path | None = None
        self.workspace: Path | None = None
        self.lock: RunLock | None = None
        self.policy = SafetyPolicy()
        self.audit = AuditLog()
        self.requires_mc3_licenses = False

    def create(
        self,
        name: str = "session",
        template: str | None = None,
        platform: str | None = None,
        configuration: str | None = None,
    ) -> dict:
        self.close()
        lock = RunLock()
        lock.__enter__()
        try:
            template = template or self.config.session.template
            self.requires_mc3_licenses = self.needs_mc3_licenses(template)
            platform = platform or self.config.session.platform
            configuration = configuration or self.config.session.configuration
            stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
            workspace = self.config.session.output_root / "sessions" / f"{stamp}-{name}"
            workspace.mkdir(parents=True, exist_ok=True)
            self.policy.assert_simulation(True)
            self.policy.assert_write(workspace)
            self.policy.assert_no_safety_project(workspace)
            self.policy.assert_no_safety_project(Path(template))
            spec = ProjectSpec(
                name=name,
                source_solution=Path(template).resolve(),
                workspace=workspace,
                output_dir=workspace / "report",
                plc_project=self.config.session.plc_project,
                ads_symbol=self.config.session.ads_symbol,
                ads_netid=self.config.session.ads_netid,
                ads_port=self.config.session.ads_port,
                target_platform=platform,
                build_configuration=configuration,
            )
            xae = XaeDteAdapter()
            self.lock = lock
            self.xae = xae
            self.spec = spec
            self.workspace = workspace
            solution = xae.prepare_project(spec)
            self.solution = solution
            xae.open_solution(solution)
            self.ads = PyadsAdapter(spec.ads_netid, spec.ads_port)
            self._audit("session.create", project=name, solution=str(solution))
            return self.status()
        except Exception:
            try:
                lock.__exit__(None, None, None)
            finally:
                self.close()
            raise

    def write_pou(
        self,
        name: str,
        declaration: str,
        implementation: str = "",
        kind: str = "function_block",
        expected_revision: str | None = None,
        methods: list[dict] | None = None,
    ) -> dict:
        self._ensure_open()
        self._check_revision(expected_revision)
        report = review_st(declaration, implementation)
        if not report.ok:
            raise ValueError(f"PLC review rejected {name}: {[issue.message for issue in report.issues]}")
        self.xae.write_pou(
            self.solution,
            self.spec,
            name,
            declaration,
            implementation,
            kind,
            methods=methods,
        )
        self._audit("pou.write", name=name, kind=kind, methods=len(methods or ()))
        return report.as_dict()

    def revision(self) -> dict:
        self._ensure_open()
        return project_revision(self.solution.parent)

    def build(self) -> dict:
        self._ensure_open()
        before = project_revision(self.solution.parent)
        result = asdict(self.xae.build(self.solution, self.spec))
        after = project_revision(self.solution.parent)
        result["revision_before"] = before["revision"]
        result["revision_after"] = after["revision"]
        self._audit("build", ok=result["ok"], errors=result["errors"], revision=after["revision"])
        return result

    def verify_symbols(self, symbols: list[str]) -> dict:
        self._ensure_open()
        for symbol in symbols:
            self.xae.verify_symbol(self.solution, self.spec, symbol)
        return {"verified": symbols}

    def verify_symbol(self, symbol: str) -> dict:
        self._ensure_open()
        self.xae.verify_symbol(self.solution, self.spec, symbol)
        return {"symbol": symbol, "verified": True}

    def activate(self) -> dict:
        self._ensure_open()
        if self.requires_mc3_licenses:
            require_licenses()
        result = self.xae.activate_simulation(self.solution, self.spec)
        self.ads.ensure_running()
        self._audit("activate", target=result.target_netid)
        return asdict(result)

    @staticmethod
    def needs_mc3_licenses(template: str | Path | None) -> bool:
        """Return True for templates that start an MC3 axis (need TF5500).

        The check is intentionally conservative: only templates whose path
        mentions ``mc3`` are gated, so the plain minimal project keeps working
        with a PLC/ADS-only license.
        """
        if not template:
            return False
        return "mc3" in Path(template).as_posix().casefold()

    def activate_with_retry(self, attempts: int = 3) -> dict:
        """Activate the simulation, retrying the post-restart window.

        Activation restarts the TwinCAT system.  The PLC port can be missing
        for a while and the runtime can come up with an empty symbol table, so
        retry with an explicit RECONFIG before giving up.
        """
        last_error: Exception | None = None
        for attempt in range(1, attempts + 1):
            try:
                state = self.ads.wait_for_service(120.0)
                if state[0] == 5 and not self.ads.symbols_ready():
                    self._audit("activate.reconfigure", reason="plc symbols empty")
                    self.ads.reconfigure()
                result = self.activate()
                self.ads.wait_symbols(60.0)
                self.ads.wait_for_run(60.0)
                return result
            except RuntimeError as exc:
                last_error = exc
                self._audit("activate.retry", attempt=attempt, error=str(exc))
                if attempt >= attempts:
                    break
                try:
                    self.ads.reconfigure(timeout_s=90.0, settle_s=3.0)
                except Exception as reconfigure_error:
                    self._audit("activate.reconfigure", error=str(reconfigure_error))
                time.sleep(5.0)
        raise RuntimeError(
            f"TwinCAT activation failed after {attempts} attempts: {last_error}"
        )

    def ads_state(self) -> dict:
        self._ensure_open()
        self.ads.ensure_running()
        return self.ads.state()

    def ads_read(self, symbol: str, value_type: str = "BOOL") -> dict:
        self._ensure_open()
        self.ads.ensure_running()
        return {"symbol": symbol, "type": value_type, "value": self.ads.read_symbol(symbol, value_type)}

    def ads_write(self, symbol: str, value: object, value_type: str = "BOOL") -> dict:
        self._ensure_open()
        self.ads.ensure_running()
        self.ads.write_symbol(symbol, value, value_type)
        self._audit("ads.write", symbol=symbol, type=value_type, value=value)
        return {"symbol": symbol, "type": value_type, "value": value}

    def ads_read_many(self, symbols: list[dict]) -> dict:
        self._ensure_open()
        self.ads.ensure_running()
        values = []
        for item in symbols:
            symbol = str(item["symbol"])
            value_type = str(item.get("type", "BOOL"))
            values.append(
                {
                    "symbol": symbol,
                    "type": value_type,
                    "value": self.ads.read_symbol(symbol, value_type),
                }
            )
        return {"values": values}

    def ads_write_many(self, writes: list[dict]) -> dict:
        self._ensure_open()
        self.ads.ensure_running()
        results = []
        for item in writes:
            symbol = str(item["symbol"])
            value_type = str(item.get("type", "BOOL"))
            value = item["value"]
            self.ads.write_symbol(symbol, value, value_type)
            results.append({"symbol": symbol, "type": value_type, "value": value})
        return {"writes": results}

    def ads_wait(
        self,
        symbol: str,
        expected: object,
        value_type: str = "BOOL",
        timeout_s: float = 10.0,
        interval_ms: int = 50,
        tolerance: float = 0.0,
    ) -> dict:
        self._ensure_open()
        self.ads.ensure_running()
        started = time.monotonic()
        observed = None
        while time.monotonic() - started <= timeout_s:
            observed = self.ads.read_symbol(symbol, value_type)
            matched = (
                abs(float(observed) - float(expected)) <= tolerance
                if tolerance and isinstance(observed, (int, float))
                else observed == expected
            )
            if matched:
                return {
                    "ok": True,
                    "symbol": symbol,
                    "expected": expected,
                    "observed": observed,
                    "elapsed_s": round(time.monotonic() - started, 3),
                }
            time.sleep(max(interval_ms, 10) / 1000.0)
        return {
            "ok": False,
            "symbol": symbol,
            "expected": expected,
            "observed": observed,
            "elapsed_s": round(time.monotonic() - started, 3),
        }

    def archive_export(self, filename: str, scope: str = "solution", overwrite: bool = False) -> dict:
        self._ensure_open()
        if not re.fullmatch(r"[A-Za-z0-9._-]{1,128}\.(tnzip|tszip|tpzip)", filename, flags=re.I):
            raise ValueError("archive filename must be a safe .tnzip/.tszip/.tpzip name")
        path = self.config.session.archive_root / filename
        path.parent.mkdir(parents=True, exist_ok=True)
        if path.exists() and not overwrite:
            raise FileExistsError(path)
        if path.exists():
            path.unlink()
        result = self.xae.export_archive(self.solution, self.spec, path, scope)
        digest = hashlib.sha256(result.read_bytes()).hexdigest()
        self._audit("archive.export", path=str(result), scope=scope)
        return {"path": str(result), "scope": scope, "sha256": digest, "size": result.stat().st_size}

    def snapshot(self, label: str = "snapshot") -> dict:
        self._ensure_open()
        if not re.fullmatch(r"[A-Za-z0-9._-]{1,64}", label):
            raise ValueError("snapshot label contains unsafe characters")
        root = self.solution.parent
        stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
        target_dir = self.config.session.snapshot_root
        target_dir.mkdir(parents=True, exist_ok=True)
        path = target_dir / f"{stamp}-{label}.zip"
        skipped_dirs = {"_Boot", "_CompileInfo", ".vs", "__pycache__"}
        with zipfile.ZipFile(path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
            for file in root.rglob("*"):
                if not file.is_file():
                    continue
                if any(part in skipped_dirs for part in file.parts):
                    continue
                if file.suffix.casefold() in {".tmc", ".bak", ".tclrs"}:
                    continue
                archive.write(file, file.relative_to(root).as_posix())
        digest = hashlib.sha256(path.read_bytes()).hexdigest()
        self._audit("snapshot.create", path=str(path), label=label)
        return {"path": str(path), "sha256": digest, "size": path.stat().st_size}

    def restore(self, snapshot_path: str) -> dict:
        self._ensure_open()
        archive = Path(snapshot_path).expanduser().resolve()
        snapshot_root = (WRITE_ROOT / "snapshots").resolve()
        try:
            archive.relative_to(snapshot_root)
        except ValueError as exc:
            raise ValueError("restore snapshot must be under runtime/snapshots") from exc
        if not archive.is_file():
            raise FileNotFoundError(archive)

        root = self.solution.parent
        solution_name = self.solution.name
        workspace = self.workspace
        spec = self.spec
        self.close()
        XaeDteAdapter.wait_for_exit(15.0)
        lock = RunLock()
        lock.__enter__()
        temp = workspace / "restore-temp"
        try:
            if temp.exists():
                shutil.rmtree(temp)
            temp.mkdir(parents=True)
            with zipfile.ZipFile(archive) as zf:
                for member in zf.infolist():
                    member_path = Path(member.filename)
                    if member_path.is_absolute() or ".." in member_path.parts:
                        raise ValueError(f"unsafe snapshot member: {member.filename}")
                zf.extractall(temp)
            restored_solution = temp / solution_name
            if not restored_solution.is_file():
                raise FileNotFoundError(f"snapshot does not contain {solution_name}")
            if root.exists():
                shutil.rmtree(root)
            shutil.copytree(temp, root)
            xae = XaeDteAdapter()
            solution = root / solution_name
            self.lock = lock
            self.xae = xae
            self.spec = spec
            self.solution = solution
            self.workspace = workspace
            xae.open_solution(solution)
            self.ads = PyadsAdapter(spec.ads_netid, spec.ads_port)
            return {"restored": True, "snapshot": str(archive), **self.status()}
        except Exception:
            try:
                lock.__exit__(None, None, None)
            finally:
                self.close()
            raise
        finally:
            if temp.exists():
                shutil.rmtree(temp, ignore_errors=True)

    def write_dut(
        self,
        name: str,
        declaration: str,
        kind: str = "struct",
        expected_revision: str | None = None,
    ) -> dict:
        self._ensure_open()
        self._check_revision(expected_revision)
        if not declaration.strip():
            raise ValueError("DUT declaration must not be empty")
        self.xae.write_dut(self.solution, self.spec, name, declaration, kind)
        self._audit("dut.write", name=name, kind=kind)
        return {"name": name, "kind": kind, "written": True}

    def write_gvl(
        self,
        name: str,
        declaration: str,
        expected_revision: str | None = None,
    ) -> dict:
        self._ensure_open()
        self._check_revision(expected_revision)
        if not declaration.strip():
            raise ValueError("GVL declaration must not be empty")
        self.xae.write_gvl(self.solution, self.spec, name, declaration)
        self._audit("gvl.write", name=name)
        return {"name": name, "written": True}

    def project_tree(self, max_depth: int = 5) -> dict:
        self._ensure_open()
        return self.xae.project_tree(self.solution, self.spec, max_depth)

    def add_library(
        self,
        name: str,
        version: str = "*",
        company: str = "Beckhoff Automation GmbH",
    ) -> dict:
        self._ensure_open()
        self.xae.add_library(self.solution, self.spec, name, version, company)
        self._audit("library.add", name=name, version=version, company=company)
        return {"name": name, "version": version, "company": company, "added": True}

    def list_libraries(self) -> dict:
        self._ensure_open()
        return {"libraries": self.xae.list_libraries(self.solution, self.spec)}

    def project_info(self, include_symbols: bool = False) -> dict:
        self._ensure_open()
        result = {
            "status": self.status(),
            "tree": self.project_tree(4),
            "libraries": self.list_libraries()["libraries"],
            "pous": self.list_pous()["pous"],
        }
        if include_symbols:
            result["symbols"] = self.list_symbols()["symbols"]
        return result

    def list_pous(self) -> dict:
        self._ensure_open()
        return {"pous": self.xae.list_pous(self.solution, self.spec)}

    def read_pou(self, name: str) -> dict:
        self._ensure_open()
        return self.xae.read_pou(self.solution, self.spec, name)

    def list_symbols(self) -> dict:
        self._ensure_open()
        return {"symbols": self.xae.list_symbols(self.solution, self.spec)}

    def ads_discover(self) -> dict:
        self._ensure_open()
        self.ads.ensure_running()
        symbols = self.ads.discover_symbols()
        return {"symbols": symbols, "count": len(symbols)}

    def validate_project(self) -> dict:
        self._ensure_open()
        return self.validator.validate_project(self.workspace / self.spec.name).as_dict()

    def status(self) -> dict:
        revision = project_revision(self.solution.parent) if self.solution else {"revision": "", "files": 0}
        return {
            "open": self.spec is not None,
            "project": self.spec.name if self.spec else None,
            "solution": str(self.solution) if self.solution else None,
            "workspace": str(self.workspace) if self.workspace else None,
            "target_platform": self.spec.target_platform if self.spec else None,
            "revision": revision["revision"],
            "files": revision["files"],
            "ads": {
                "netid": self.spec.ads_netid if self.spec else None,
                "port": self.spec.ads_port if self.spec else None,
            },
        }

    def close(self) -> dict:
        errors: list[str] = []
        if self.ads is not None:
            try:
                self.ads.close()
            except Exception as exc:
                errors.append(f"ads: {exc}")
            self.ads = None
        if self.xae is not None:
            try:
                self.xae.close()
            except Exception as exc:
                errors.append(f"xae: {exc}")
            self.xae = None
        if self.lock is not None:
            try:
                self.lock.__exit__(None, None, None)
            except Exception as exc:
                errors.append(f"lock: {exc}")
            self.lock = None
        if errors:
            self._audit("session.close", errors=errors)
        else:
            self._audit("session.close")
        self.spec = None
        self.solution = None
        self.workspace = None
        self.requires_mc3_licenses = False
        return {"closed": True, "errors": errors}

    def run_loadcontrol(
        self,
        setpoint_lbs: float = 1000.0,
        hold_seconds: float = 1.0,
        timeout_s: float = 30.0,
    ) -> dict:
        fb_decl, fb_impl, main_decl, main_impl = loadcontrol_sources(setpoint_lbs, hold_seconds)
        self.create("loadcontrol")
        self.write_gvl("GVL_Scope", loadcontrol_scope_gvl())
        self.write_pou("FB_LoadControl", fb_decl, fb_impl, "function_block")
        self.write_pou("MAIN", main_decl, main_impl, "program")
        build = self.build()
        if not build["ok"]:
            raise RuntimeError(f"LoadControl build failed: {build}")
        self.verify_symbol("MAIN.bComplete")
        self.verify_symbol("MAIN.rActualLbs")
        self.verify_symbol("GVL_Scope.fLoadActualForce")
        self.verify_symbol("GVL_Scope.fSimAxisActualPos")
        retry_s = 0.0
        retry_used = False
        attempts = 3
        last_error: Exception | None = None
        for attempt in range(1, attempts + 1):
            try:
                state = self.ads.wait_for_service(120.0)
                if state[0] == 5 and not self.ads.symbols_ready():
                    self._audit("activate.reconfigure", reason="plc symbols empty")
                    self.ads.reconfigure()
                activation = self.activate()
                self.ads.ensure_running()
                self.ads.wait_symbols(60.0)
                break
            except RuntimeError as exc:
                last_error = exc
                retry_used = True
                self._audit("activate.retry", attempt=attempt, error=str(exc))
                if attempt >= attempts:
                    raise
                retry_started = time.monotonic()
                try:
                    self.ads.reconfigure(timeout_s=90.0, settle_s=3.0)
                except Exception as reconfigure_error:
                    self._audit("activate.reconfigure", error=str(reconfigure_error))
                time.sleep(5.0)
                retry_s += time.monotonic() - retry_started
        else:
            raise RuntimeError(f"TwinCAT activation failed after {attempts} attempts: {last_error}")

        task_started = time.monotonic()
        self.ads.write_symbol("MAIN.bStart", False, "BOOL")
        self.ads.write_symbol("MAIN.bReset", True, "BOOL")
        time.sleep(0.2)
        self.ads.write_symbol("MAIN.bReset", False, "BOOL")
        self.ads.write_symbol("MAIN.bStart", True, "BOOL")

        started = time.monotonic()
        samples: list[dict] = []
        max_actual = 0.0
        release_seen = False
        complete = False
        hold_elapsed = 0.0
        next_sample = 0.0
        while time.monotonic() - started < timeout_s:
            state = int(self.ads.read_symbol("MAIN.nState", "INT"))
            actual = float(self.ads.read_symbol("MAIN.rActualLbs", "REAL"))
            complete = bool(self.ads.read_symbol("MAIN.bComplete", "BOOL"))
            release = bool(self.ads.read_symbol("MAIN.bRelease", "BOOL"))
            hold_elapsed = float(self.ads.read_symbol("MAIN.rHoldElapsed", "REAL"))
            max_actual = max(max_actual, actual)
            release_seen = release_seen or release
            elapsed = time.monotonic() - started
            if elapsed >= next_sample:
                samples.append(
                    {
                        "t": round(elapsed, 3),
                        "state": state,
                        "actual_lbs": round(actual, 3),
                        "release": release,
                        "hold_elapsed": round(hold_elapsed, 3),
                    }
                )
                next_sample += 0.2
            if complete:
                break
            time.sleep(0.05)

        time.sleep(0.1)
        final_actual = float(self.ads.read_symbol("MAIN.rActualLbs", "REAL"))
        self.ads.write_symbol("MAIN.bStart", False, "BOOL")
        self.ads.write_symbol("MAIN.bReset", True, "BOOL")
        time.sleep(0.1)
        self.ads.write_symbol("MAIN.bReset", False, "BOOL")

        checks = {
            "complete": complete,
            "release_seen": release_seen,
            "reached_setpoint": max_actual >= setpoint_lbs - 10.0,
            "hold_time": hold_elapsed >= hold_seconds - 0.05,
            "hold_time_bounded": hold_elapsed <= hold_seconds + 0.3,
            "released_to_zero": final_actual <= 0.01,
        }
        return {
            "ok": all(checks.values()),
            "activation": activation,
            "setpoint_lbs": setpoint_lbs,
            "hold_seconds": hold_seconds,
            "max_actual_lbs": round(max_actual, 3),
            "hold_elapsed_s": round(hold_elapsed, 3),
            "final_actual_lbs": round(final_actual, 3),
            "checks": checks,
            "samples": samples[:50],
            "task_duration_s": round(time.monotonic() - task_started, 3),
            "infrastructure_retry_s": round(retry_s, 3),
            "retry_used": retry_used,
            "session": self.status(),
        }

    def _check_revision(self, expected: str | None) -> None:
        if not expected:
            return
        current = project_revision(self.solution.parent)["revision"]
        if current != expected:
            raise RuntimeError(f"revision mismatch: expected {expected}, current {current}")

    def _ensure_open(self) -> None:
        if self.xae is None or self.ads is None or self.spec is None or self.solution is None:
            raise RuntimeError("no automation session is open")

    def _audit(self, event: str, **fields) -> None:
        try:
            self.audit.record(event, **fields)
        except Exception:
            pass
