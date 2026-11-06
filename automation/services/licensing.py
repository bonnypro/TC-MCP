from __future__ import annotations

import xml.etree.ElementTree as ET
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

DEFAULT_TRIAL_LICENSE = Path(
    r"C:\ProgramData\Beckhoff\TwinCAT\3.1\Runtimes\UmRT_Default\3.1"
    r"\Target\License\TrialLicense.tclrs"
)

# Licenses needed by the local MC3 external-setpoint experiment.  Keep this
# list explicit: a missing license otherwise only shows up as a modal dialog
# after activation, when the automation is already blocked.
MC3_REQUIRED_LICENSES = (
    "TC3 ADS",
    "TC3 MC3 Axis",
    "TC3 MC3 Base",
    "TC3 PLC",
)


class LicenseError(RuntimeError):
    """Raised when a required TwinCAT license is missing or expired."""


@dataclass(frozen=True)
class LicenseStatus:
    path: Path
    names: tuple[str, ...]
    expires_at: datetime | None

    def missing(self, required: tuple[str, ...]) -> tuple[str, ...]:
        present = {name.casefold() for name in self.names}
        return tuple(name for name in required if name.casefold() not in present)

    def expired(self, now: datetime | None = None) -> bool:
        if self.expires_at is None:
            return False
        return self.expires_at < (now or datetime.now())


def read_license_status(path: Path | str = DEFAULT_TRIAL_LICENSE) -> LicenseStatus:
    license_path = Path(path)
    if not license_path.is_file():
        raise LicenseError(f"TwinCAT trial license not found: {license_path}")
    try:
        root = ET.parse(license_path).getroot()
    except ET.ParseError as exc:
        raise LicenseError(f"cannot parse TwinCAT trial license {license_path}: {exc}") from exc

    names = tuple(
        sorted(
            {
                (element.text or "").strip()
                for element in root.iter("Name")
                if (element.text or "").strip()
            }
        )
    )
    expires_text = (root.findtext(".//ExpireTime") or "").strip()
    expires_at = None
    if expires_text:
        try:
            expires_at = datetime.fromisoformat(expires_text)
        except ValueError:
            expires_at = None
    return LicenseStatus(path=license_path, names=names, expires_at=expires_at)


def require_licenses(
    required: tuple[str, ...] = MC3_REQUIRED_LICENSES,
    path: Path | str = DEFAULT_TRIAL_LICENSE,
    now: datetime | None = None,
) -> LicenseStatus:
    status = read_license_status(path)
    missing = status.missing(required)
    if missing:
        raise LicenseError(
            "missing TwinCAT licenses: "
            + ", ".join(missing)
            + f" (license file: {status.path})"
        )
    if status.expired(now):
        raise LicenseError(
            f"TwinCAT trial license expired at {status.expires_at.isoformat()} "
            f"({status.path})"
        )
    return status
