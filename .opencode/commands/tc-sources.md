---
description: Show the PDF source registry, reachability, and update state
---

Run:

```powershell
python -m app.cli sources
```

Add `--no-ping` for an offline view.

The registry is `config/sources.json`. Each entry has:

- `id`, `name`, `category`
- `kind` (`documents`, `github`, `local`)
- `url` / `check_url`
- `auth` (`none`, `webvpn`)
- `documents` tracked PDFs or local packages

Report the table as-is and do not expose credentials.
