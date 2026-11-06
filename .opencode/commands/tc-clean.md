---
description: Preview or remove generated TC-MCP/TEST artifacts
---

Use the cleanup script, never delete source, Git metadata, `data/`, or docs.
It also applies `TEST/rounds` retention: raw artifacts for the newest 2 rounds,
structured summaries for the newest 20. Report the resulting manifest.

Default to a dry run:

```powershell
python scripts/clean_project.py --older-than 7
```

Report the candidate count, total MiB, and any pinned evidence. Do not run
`--apply` unless the user explicitly asks to delete.

When the user says "clean", "clean all", or "apply":

- normal generated artifacts, older than 7 days:
  `python scripts/clean_project.py --apply --older-than 7`
- all generated artifacts, including recent runs:
  `python scripts/clean_project.py --apply --all --older-than 0`
- include Scope evidence (`scope-acceptance-*`, `scope-raw-*`):
  `python scripts/clean_project.py --apply --all --older-than 0 --include-evidence`
- include explicitly pinned evidence:
  `python scripts/clean_project.py --apply --all --older-than 0 --include-evidence --include-pinned`

Before `--apply`, print exactly what will be deleted. After it, report the
manifest path under `TEST/cleanup/`.
