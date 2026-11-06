from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
import re

_SAFE_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")


DEFAULT_PLC_DECLARATION = """PROGRAM MAIN
VAR
    bReady : BOOL := TRUE;
END_VAR"""


class TaskKind(str, Enum):
    COPY_PROJECT = "copy_project"
    OPEN_SOLUTION = "open_solution"
    WRITE_PLC = "write_plc"
    BUILD = "build"
    VERIFY_SYMBOL = "verify_symbol"
    ACTIVATE_SIMULATION = "activate_simulation"
    ADS_TEST = "ads_test"
    SNAPSHOT = "snapshot"


@dataclass(frozen=True)
class Task:
    id: str
    kind: TaskKind
    depends_on: tuple[str, ...] = ()
    resource: str = ""
    description: str = ""

    def __post_init__(self) -> None:
        if not self.id.strip():
            raise ValueError("task id must not be empty")


@dataclass(frozen=True)
class ProjectSpec:
    name: str
    source_solution: Path
    workspace: Path
    output_dir: Path
    simulation_only: bool = True
    plc_project: str = "PLC1"
    plc_declaration: str = DEFAULT_PLC_DECLARATION
    plc_implementation: str = ""
    ads_symbol: str = "MAIN.bReady"
    ads_type: str = "BOOL"
    expected_ads_value: object | None = True
    ads_netid: str = "127.0.0.1.1.1"
    ads_port: int = 851
    target_platform: str = "TwinCAT OS (x64)"
    build_configuration: str = "Release"

    def __post_init__(self) -> None:
        if not _SAFE_NAME.fullmatch(self.name):
            raise ValueError("project name contains unsafe characters")
        if "safety" in self.name.casefold() or "twinsafe" in self.name.casefold():
            raise ValueError("safety project names are forbidden")
        if not self.simulation_only:
            raise ValueError("hardware activation is disabled in F2 baseline")
        if not _SAFE_NAME.fullmatch(self.plc_project):
            raise ValueError("plc_project contains unsafe characters")
        if any(word in self.plc_project.casefold() for word in ("safety", "twinsafe", "safep")):
            raise ValueError("safety PLC project names are forbidden")
        if not self.plc_declaration.strip():
            raise ValueError("plc_declaration must not be empty")
        if not self.ads_symbol.strip() or not self.ads_netid.strip() or not self.ads_type.strip():
            raise ValueError("ADS symbol, NetId, and type must not be empty")
        if self.ads_netid not in {"127.0.0.1.1.1", "localhost"}:
            raise ValueError("F2 ADS access is restricted to the local simulation NetId")
        if self.ads_port != 851:
            raise ValueError("F2 ADS access is restricted to the local PLC port 851")
        if not self.target_platform.strip():
            raise ValueError("target_platform must not be empty")
        if not self.build_configuration.strip():
            raise ValueError("build_configuration must not be empty")


@dataclass(frozen=True)
class Plan:
    spec: ProjectSpec
    tasks: tuple[Task, ...]


@dataclass(frozen=True)
class Diagnostic:
    level: str
    message: str
    file: str = ""
    line: int = 0
    column: int = 0
    project: str = ""


@dataclass(frozen=True)
class BuildResult:
    ok: bool
    errors: int = 0
    warnings: int = 0
    message: str = ""
    diagnostics: tuple[Diagnostic, ...] = ()


@dataclass(frozen=True)
class ActivateResult:
    ok: bool
    target_netid: str
    platform: str
    mode: str = "simulation"
    diagnostics: tuple[Diagnostic, ...] = ()


@dataclass(frozen=True)
class RunStep:
    task_id: str
    kind: TaskKind
    ok: bool
    message: str = ""


@dataclass(frozen=True)
class RunReport:
    project: str
    ok: bool
    steps: tuple[RunStep, ...] = field(default_factory=tuple)
    built: bool = False
    simulated: bool = False
    verified: bool = False
    target_netid: str = ""
    ads_value: object | None = None
