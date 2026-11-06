from __future__ import annotations

from pathlib import Path
from typing import Protocol

from automation.core.models import ActivateResult, BuildResult, ProjectSpec


class XaePort(Protocol):
    """Boundary for TwinCAT XAE Automation Interface operations.

    Implementations must be single-instance and must never activate hardware
    unless the caller has passed an explicit approval.
    """

    def prepare_project(self, spec: ProjectSpec) -> Path:
        """Copy/prepare the project and return the solution path."""

    def open_solution(self, solution: Path) -> None:
        """Open a solution in the XAE instance."""

    def write_plc(self, solution: Path, spec: ProjectSpec) -> Path:
        """Write the generated PLC source and return the source path."""

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
        """Create or update one POU and return the solution path."""

    def build(self, solution: Path, spec: ProjectSpec) -> BuildResult:
        """Build the solution and return a structured result."""

    def verify_symbol(self, solution: Path, spec: ProjectSpec, symbol: str | None = None) -> None:
        """Verify that the built TMC contains the requested ADS symbol."""

    def list_pous(self, solution: Path, spec: ProjectSpec) -> list[dict]:
        """List POU names and subtypes in the PLC project."""

    def read_pou(self, solution: Path, spec: ProjectSpec, name: str) -> dict:
        """Read one POU declaration and implementation."""

    def write_dut(
        self,
        solution: Path,
        spec: ProjectSpec,
        name: str,
        declaration: str,
        kind: str = "struct",
    ) -> Path:
        """Create or update a DUT."""

    def write_gvl(
        self,
        solution: Path,
        spec: ProjectSpec,
        name: str,
        declaration: str,
    ) -> Path:
        """Create or update a GVL."""

    def project_tree(self, solution: Path, spec: ProjectSpec, max_depth: int = 5) -> dict:
        """Return a bounded PLC project tree."""

    def list_symbols(self, solution: Path, spec: ProjectSpec) -> list[dict]:
        """List compiled TMC symbols."""

    def add_library(
        self,
        solution: Path,
        spec: ProjectSpec,
        name: str,
        version: str = "*",
        company: str = "Beckhoff Automation GmbH",
    ) -> None:
        """Add a PLC library reference."""

    def list_libraries(self, solution: Path, spec: ProjectSpec) -> list[dict]:
        """List PLC library references."""

    def export_archive(self, solution: Path, spec: ProjectSpec, path: Path, scope: str = "solution") -> Path:
        """Export a .tnzip/.tszip solution or .tpzip PLC archive."""

    def activate_simulation(self, solution: Path, spec: ProjectSpec) -> ActivateResult:
        """Activate the project on the local simulation runtime."""

    def close(self) -> None:
        """Close the XAE instance and release COM objects."""
