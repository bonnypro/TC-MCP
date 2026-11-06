$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
$env:PYTHONPATH = $root

Push-Location $root
try {
    python -c "from datetime import datetime; from app import db; from app.config import BACKUPS; print(db.publish_snapshot(dest=BACKUPS / ('knowledge-' + datetime.now().strftime('%Y%m%d-%H%M%S') + '.sqlite')))"
} finally {
    Pop-Location
}
