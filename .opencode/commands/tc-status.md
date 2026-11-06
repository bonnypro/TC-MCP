---
description: Show local knowledge base status and evolution readiness
---

Run:

```powershell
python -m app.cli status
```

Report document/chunk counts, last ingest time, and pending changes. Do not modify anything.

## Evolution readiness

```powershell
python scripts/evolution.py status
python scripts/evolution.py readiness
python scripts/evolution.py channels
python scripts/mcp_launcher.py --print
python scripts/mcp_launcher.py --channel canary --print
```

`readiness` checks the hidden vault manifest, trusted contracts, protected
evaluator path, release channels, TcSysSrv, and the PLC RUN state. A true
`ready_for_supervised_evolution` does not imply code auto-promotion is enabled.
If the PLC is in CONFIG or the service is stopped, `readiness` returns false;
this is an environment signal, not a knowledge-base error.
