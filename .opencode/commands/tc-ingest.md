---
description: Ingest an explicitly approved local PDF
---

Ingest only after explicit approval. Use:

```powershell
python -m app.cli ingest "path\to\manual.pdf"
```

Report the number of documents/chunks added. Do not ingest downloaded GitHub code
until its license and safety have been reviewed. Prefer adding official PDFs to
`config/sources.json` so `/tc-update` tracks versions.
