---
description: Check PDF sources and update the knowledge base
---

Run:

```powershell
pwsh -NoProfile -File scripts/update.ps1 -Scope all
```

This command:

1. prints the source-registry table with reachability and latency,
2. checks the official Beckhoff PDF catalog for changed/new files,
3. downloads changed PDFs into `data/pdfs/`,
4. increments the PDF text index,
5. updates the DB,
6. writes `data/pdf_versions.json`,
7. writes `updates/latest/digest.md`.

The source list is `config/sources.json`; inspect it with `/tc-sources`.

Then report:

- source reachability and update status
- PDF version changes (compare `data/pdf_versions.json`)
- GitHub candidates as absorb / evaluate / ignore
- any parse errors

Do not download or execute new GitHub repositories until I explicitly approve.
