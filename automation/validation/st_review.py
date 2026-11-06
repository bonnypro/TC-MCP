from __future__ import annotations

import re

from automation.ports.validator import ValidationIssue, ValidationReport


def strip_st_comments(text: str) -> str:
    text = re.sub(r"\(\*.*?\*\)", " ", text, flags=re.S)
    text = re.sub(r"//[^\n]*", " ", text)
    text = re.sub(r"'[^']*'", "''", text)
    text = re.sub(r'"[^"]*"', '""', text)
    return text


def review_st(declaration: str, implementation: str = "") -> ValidationReport:
    issues: list[ValidationIssue] = []
    decl = strip_st_comments(declaration)
    impl = strip_st_comments(implementation)

    # TwinCAT attributes/pragmas may precede the POU header, e.g.
    # {attribute 'c++_compatible'} before a derived function block.
    header = re.sub(r"^\s*(?:\{[^}]*\}\s*)+", "", decl)
    if not re.match(r"^\s*(PROGRAM|FUNCTION_BLOCK|FUNCTION)\b", header, flags=re.I):
        issues.append(
            ValidationIssue(
                "error",
                "st.header",
                "declaration must start with PROGRAM, FUNCTION_BLOCK, or FUNCTION",
            )
        )

    pairs = (
        ("IF", "END_IF"),
        ("CASE", "END_CASE"),
        ("FOR", "END_FOR"),
        ("WHILE", "END_WHILE"),
        ("REPEAT", "END_REPEAT"),
    )
    for opening, closing in pairs:
        opens = len(re.findall(rf"\b{opening}\b", decl + "\n" + impl, flags=re.I))
        closes = len(re.findall(rf"\b{closing}\b", decl + "\n" + impl, flags=re.I))
        if opens != closes:
            issues.append(
                ValidationIssue(
                    "error",
                    f"st.balance.{opening.lower()}",
                    f"{opening} count {opens} does not match {closing} count {closes}",
                )
            )

    if re.search(r"\bWHILE\s+TRUE\b", impl, flags=re.I) and not re.search(r"\bEXIT\b", impl, flags=re.I):
        issues.append(
            ValidationIssue(
                "error",
                "st.while_true",
                "WHILE TRUE requires an EXIT path",
            )
        )

    if re.search(r"\bRETURN\b", impl, flags=re.I):
        issues.append(
            ValidationIssue(
                "warning",
                "st.return",
                "RETURN is used; verify that all required outputs are assigned",
            )
        )

    ok = not any(issue.level == "error" for issue in issues)
    return ValidationReport(ok=ok, issues=tuple(issues))
