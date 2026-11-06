from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from automation.core.models import ActivateResult, BuildResult, ProjectSpec


@dataclass
class DryRunXae:
    """In-memory XAE adapter used by tests and the default CLI mode.

    It never opens COM, never touches TwinCAT, and never writes files.
    """

    calls: list[str] = field(default_factory=list)
    solution: Path | None = None

    def prepare_project(self, spec: ProjectSpec) -> Path:
        self.calls.append("prepare_project")
        self.solution = spec.source_solution
        return self.solution

    def open_solution(self, solution: Path) -> None:
        self.calls.append("open_solution")
        self.solution = solution

    def write_plc(self, solution: Path, spec: ProjectSpec) -> Path:
        self.calls.append("write_plc")
        return solution

    def write_pou(
        self,
        solution: Path,
        spec: ProjectSpec,
        name: str,
        declaration: str,
        implementation: str,
        kind: str = "function_block",
        methods: list[dict] | None = None,
    ) -> Path:
        self.calls.append(f"write_pou:{name}:{kind}:methods={len(methods or ())}")
        return solution

    def build(self, solution: Path, spec: ProjectSpec) -> BuildResult:
        self.calls.append("build")
        return BuildResult(ok=True, errors=0, warnings=0, message="dry-run build")

    def verify_symbol(self, solution: Path, spec: ProjectSpec, symbol: str | None = None) -> None:
        self.calls.append(f"verify_symbol:{symbol or spec.ads_symbol}")

    def list_pous(self, solution: Path, spec: ProjectSpec) -> list[dict]:
        return [{"name": "MAIN", "subtype": 602}]

    def read_pou(self, solution: Path, spec: ProjectSpec, name: str) -> dict:
        return {"name": name, "declaration": spec.plc_declaration, "implementation": spec.plc_implementation}

    def write_dut(self, solution: Path, spec: ProjectSpec, name: str, declaration: str, kind: str = "struct") -> Path:
        self.calls.append(f"write_dut:{name}:{kind}")
        return solution

    def write_gvl(self, solution: Path, spec: ProjectSpec, name: str, declaration: str) -> Path:
        self.calls.append(f"write_gvl:{name}")
        return solution

    def project_tree(self, solution: Path, spec: ProjectSpec, max_depth: int = 5) -> dict:
        return {"name": spec.plc_project, "children": []}

    def list_symbols(self, solution: Path, spec: ProjectSpec) -> list[dict]:
        return [{"name": spec.ads_symbol, "type": spec.ads_type}]

    def add_library(self, solution: Path, spec: ProjectSpec, name: str, version: str = "*", company: str = "Beckhoff Automation GmbH") -> None:
        self.calls.append(f"add_library:{name}")

    def list_libraries(self, solution: Path, spec: ProjectSpec) -> list[dict]:
        return []

    def export_archive(self, solution: Path, spec: ProjectSpec, path: Path, scope: str = "solution") -> Path:
        return path

    def activate_simulation(self, solution: Path, spec: ProjectSpec) -> ActivateResult:
        self.calls.append("activate_simulation")
        return ActivateResult(ok=True, target_netid=spec.ads_netid, platform=spec.target_platform)

    def close(self) -> None:
        self.calls.append("close")


@dataclass
class DryRunAds:
    """In-memory ADS adapter that satisfies the port contract."""

    value: object = True
    calls: list[str] = field(default_factory=list)

    def ensure_running(self) -> None:
        self.calls.append("ensure_running")

    def read_symbol(self, symbol: str, value_type: str = "BOOL") -> object:
        self.calls.append(f"read_symbol:{symbol}")
        return self.value

    def write_symbol(self, symbol: str, value: object, value_type: str = "BOOL") -> None:
        self.calls.append(f"write_symbol:{symbol}")

    def close(self) -> None:
        self.calls.append("close")
