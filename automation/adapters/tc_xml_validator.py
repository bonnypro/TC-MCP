from __future__ import annotations

import re
import xml.etree.ElementTree as ET
from pathlib import Path

from automation.ports.validator import ValidationIssue, ValidationReport
from automation.validation.st_review import review_st

GUID_RE = re.compile(r"^\{[0-9A-Fa-f-]{36}\}$")


class TcXmlValidator:
    """Small, deterministic PLC XML validator used before XAE import."""

    def validate_text(self, kind: str, text: str, name: str = "") -> ValidationReport:
        suffix = kind.casefold()
        issues: list[ValidationIssue] = []
        try:
            root = ET.fromstring(text)
        except ET.ParseError as exc:
            return ValidationReport(
                ok=False,
                issues=(ValidationIssue("error", "xml.parse", str(exc), name),),
            )

        local = self._local(root.tag)
        if suffix == ".tcpou":
            self._check_pou(root, local, issues, name)
        elif suffix == ".tcdut":
            self._check_named(root, local, "DUT", issues, name)
        elif suffix == ".tcgvl":
            self._check_named(root, local, "GVL", issues, name)
        elif suffix == ".tcio":
            if local not in {"TcPlcObject", "TcIo"}:
                issues.append(ValidationIssue("error", "xml.root", f"unexpected root {local}", name))
        elif suffix == ".plcproj":
            self._check_plcproj(root, local, issues, name)
        else:
            issues.append(ValidationIssue("error", "xml.kind", f"unsupported kind: {kind}", name))

        ok = not any(issue.level == "error" for issue in issues)
        return ValidationReport(ok=ok, issues=tuple(issues))

    def validate_project(self, root: Path) -> ValidationReport:
        root = root.resolve()
        issues: list[ValidationIssue] = []
        files = [path for path in root.rglob("*") if path.is_file()]
        for path in files:
            suffix = path.suffix.casefold()
            if suffix not in {".tcpou", ".tcdut", ".tcgvl", ".tcio", ".plcproj"}:
                continue
            report = self.validate_text(suffix, path.read_text(encoding="utf-8-sig"), str(path))
            for issue in report.issues:
                issues.append(
                    ValidationIssue(issue.level, issue.code, issue.message, str(path.relative_to(root)))
                )
            if suffix == ".plcproj":
                issues.extend(self._check_plcproj_files(path, root))
        ok = not any(issue.level == "error" for issue in issues)
        return ValidationReport(ok=ok, issues=tuple(issues))

    def _check_pou(self, root, local: str, issues: list[ValidationIssue], name: str) -> None:
        if local != "TcPlcObject":
            issues.append(ValidationIssue("error", "xml.root", f"unexpected root {local}", name))
            return
        pou = next((child for child in root if self._local(child.tag) == "POU"), None)
        if pou is None:
            issues.append(ValidationIssue("error", "pou.missing", "POU element is missing", name))
            return
        pou_name = pou.get("Name", "")
        if not pou_name:
            issues.append(ValidationIssue("error", "pou.name", "POU Name is missing", name))
        declaration = pou.findtext("Declaration", default="")
        implementation = pou.findtext("Implementation/ST", default="")
        st_report = review_st(declaration, implementation)
        issues.extend(st_report.issues)

    def _check_named(self, root, local: str, tag: str, issues: list[ValidationIssue], name: str) -> None:
        if local != "TcPlcObject":
            issues.append(ValidationIssue("error", "xml.root", f"unexpected root {local}", name))
            return
        item = next((child for child in root if self._local(child.tag) == tag), None)
        if item is None:
            issues.append(ValidationIssue("error", f"{tag.lower()}.missing", f"{tag} element is missing", name))
            return
        if not item.get("Name"):
            issues.append(ValidationIssue("error", f"{tag.lower()}.name", f"{tag} Name is missing", name))

    def _check_plcproj(self, root, local: str, issues: list[ValidationIssue], name: str) -> None:
        if local != "Project":
            issues.append(ValidationIssue("error", "plcproj.root", f"unexpected root {local}", name))
            return
        seen: set[str] = set()
        for element in root.iter():
            if self._local(element.tag) != "Compile":
                continue
            include = element.get("Include", "")
            if not include:
                issues.append(ValidationIssue("error", "plcproj.include", "empty Compile Include", name))
                continue
            key = include.casefold()
            if key in seen:
                issues.append(ValidationIssue("error", "plcproj.duplicate", f"duplicate Compile Include {include}", name))
            seen.add(key)
            if Path(include).is_absolute() or ".." in Path(include).parts:
                issues.append(ValidationIssue("error", "plcproj.path", f"unsafe Compile Include {include}", name))

    def _check_plcproj_files(self, path: Path, root: Path) -> list[ValidationIssue]:
        issues: list[ValidationIssue] = []
        tree = ET.parse(path)
        for element in tree.getroot().iter():
            if self._local(element.tag) != "Compile":
                continue
            include = element.get("Include", "")
            if include:
                candidate = path.parent / include
                if not candidate.is_file():
                    issues.append(
                        ValidationIssue(
                            "error",
                            "plcproj.missing_file",
                            f"Compile Include does not exist: {include}",
                            str(path.relative_to(root)),
                        )
                    )
        return issues

    @staticmethod
    def _local(tag: str) -> str:
        return tag.rsplit("}", 1)[-1]
