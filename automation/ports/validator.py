from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Protocol


@dataclass(frozen=True)
class ValidationIssue:
    level: str
    code: str
    message: str
    path: str = ""


@dataclass(frozen=True)
class ValidationReport:
    ok: bool
    issues: tuple[ValidationIssue, ...] = ()

    def as_dict(self) -> dict:
        return {
            "ok": self.ok,
            "issues": [
                {
                    "level": issue.level,
                    "code": issue.code,
                    "message": issue.message,
                    "path": issue.path,
                }
                for issue in self.issues
            ],
        }


class PlcValidatorPort(Protocol):
    def validate_text(self, kind: str, text: str, name: str = "") -> ValidationReport:
        ...

    def validate_project(self, root: Path) -> ValidationReport:
        ...
