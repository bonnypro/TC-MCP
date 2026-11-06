from __future__ import annotations

from pathlib import Path

from automation.core.config import load_config

WORKSPACE_ROOT = Path(__file__).resolve().parent.parent.parent
try:
    _CONFIG = load_config()
    WRITE_ROOT = _CONFIG.security.write_root
    FORBIDDEN_NAME_PARTS = _CONFIG.security.forbidden_name_parts
except Exception:
    WRITE_ROOT = WORKSPACE_ROOT / "runtime"
    FORBIDDEN_NAME_PARTS = ("safety", "twinsafe", "safeplc")


class SafetyError(RuntimeError):
    pass


def _resolved(path: Path) -> Path:
    return path.expanduser().resolve(strict=False)


def ensure_within_workspace(path: Path, workspace: Path = WORKSPACE_ROOT) -> Path:
    target = _resolved(path)
    root = _resolved(workspace)
    try:
        target.relative_to(root)
    except ValueError as exc:
        raise SafetyError(f"path outside workspace: {target}") from exc
    return target


class SafetyPolicy:
    def __init__(
        self,
        workspace: Path = WRITE_ROOT,
        hardware_allowed: bool = False,
        forbidden_name_parts: tuple[str, ...] | None = None,
    ) -> None:
        self.workspace = _resolved(workspace)
        self.hardware_allowed = hardware_allowed
        self.forbidden_name_parts = forbidden_name_parts or FORBIDDEN_NAME_PARTS

    def assert_write(self, path: Path) -> None:
        ensure_within_workspace(path, self.workspace)

    def assert_simulation(self, simulation_only: bool) -> None:
        if not simulation_only and not self.hardware_allowed:
            raise SafetyError("hardware activation requires explicit approval")

    def assert_no_safety_project(self, path: Path) -> None:
        lowered = str(path).casefold()
        if any(part.casefold() in lowered for part in self.forbidden_name_parts):
            raise SafetyError("safety project writes are forbidden")
