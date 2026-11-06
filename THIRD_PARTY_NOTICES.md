# Third-party notices

This repository is an open-source automation workspace. Third-party components
are not vendored as source; the TwinCAT template contains only project/source
files, not official compiled Beckhoff libraries.

## Python packages

| Package | Version | License | Use |
|---|---:|---|---|
| `pypdf` | 6.21.4 | BSD-3-Clause | Default PDF text extraction |
| `PyMuPDF` | 1.28.2 | AGPL-3.0 / commercial | **Optional** fast PDF parser |
| `pywin32` | 312 | PSF-2.0 | Windows COM / XAE DTE |
| `pyads` | 3.6.0 | MIT | ADS simulation tests |

`pypdf`, `pywin32`, and `pyads` are installed from `requirements.txt` at user
level with `python -m pip install --user`; no system PATH, service, registry, or
driver changes are made.

`PyMuPDF` is **not** in `requirements.txt` and is **not** distributed with this
repository. The code falls back to `pypdf` when `PyMuPDF` is absent. A user who
chooses to install `PyMuPDF` separately must comply with its AGPL-3.0 or
commercial license; that license applies to `PyMuPDF`, not to this repository's
original MIT-licensed code. See `docs/PUBLISHING.md` for the licensing rationale.

## TwinCAT template

`templates/minimal` was generated from the TwinCAT 3.1 4026.27 standard PLC
template. The public repository contains only the project/source files; the
official compiled library binaries under `_Libraries/` are intentionally
excluded and ignored. Building the template requires a licensed local TwinCAT
installation with the standard libraries. No Beckhoff binary library is
redistributed by this repository.

## External research

GitHub projects are read-only research inputs unless a separate, reviewed
change adds them. No external repository code is executed by the automation
runner.
