from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from automation.services.licensing import (
    DEFAULT_TRIAL_LICENSE,
    MC3_REQUIRED_LICENSES,
    LicenseError,
    read_license_status,
)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Check the local TwinCAT trial license before an MC3 test."
    )
    parser.add_argument(
        "--license",
        type=Path,
        default=DEFAULT_TRIAL_LICENSE,
        help="path to TrialLicense.tclrs",
    )
    parser.add_argument(
        "--require",
        action="append",
        default=None,
        help="required license name; may be repeated (default: MC3 set)",
    )
    args = parser.parse_args(argv)
    required = tuple(args.require) if args.require else MC3_REQUIRED_LICENSES

    try:
        status = read_license_status(args.license)
    except LicenseError as exc:
        print(json.dumps({"ok": False, "error": str(exc)}, ensure_ascii=False, indent=2))
        return 2

    missing = status.missing(required)
    expired = status.expired()
    result = {
        "ok": not missing and not expired,
        "path": str(status.path),
        "licenses": list(status.names),
        "required": list(required),
        "missing": list(missing),
        "expires_at": status.expires_at.isoformat() if status.expires_at else None,
        "expired": expired,
    }
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result["ok"] else 1


if __name__ == "__main__":
    sys.exit(main())
