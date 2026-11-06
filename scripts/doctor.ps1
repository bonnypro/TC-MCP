$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
$env:PYTHONPATH = $root

Push-Location $root
try {
    python -m app.cli doctor
} finally {
    Pop-Location
}
