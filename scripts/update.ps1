param(
    [ValidateSet("all", "docs", "github")]
    [string]$Scope = "all"
)

$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
$env:PYTHONPATH = $root

Push-Location $root
try {
    python -m app.cli update --scope $Scope
} finally {
    Pop-Location
}
