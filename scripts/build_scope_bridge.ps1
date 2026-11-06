$ErrorActionPreference = "Stop"

$root = Split-Path $PSScriptRoot -Parent
$workspace = Split-Path $root -Parent
$outDir = Join-Path $workspace "TEST\bin"
if ($env:TC_TEST_DIR) { $outDir = Join-Path $env:TC_TEST_DIR "bin" }
$scopeDir = "C:\Program Files (x86)\Beckhoff\TwinCAT\Functions\TE130x-Scope-View"
if ($env:TC_SCOPE_DIR) { $scopeDir = $env:TC_SCOPE_DIR }

$cscCandidates = @(
    "C:\Windows\Microsoft.NET\Framework64\v4.0.30319\csc.exe",
    "C:\Windows\Microsoft.NET\Framework\v4.0.30319\csc.exe"
)
$csc = $cscCandidates | Where-Object { Test-Path $_ } | Select-Object -First 1
if (-not $csc) { throw "csc.exe not found (install .NET Framework 4.x)" }

$iface = Join-Path $scopeDir "TwinCAT.Measurement.AutomationInterface.dll"
$ads = Join-Path $scopeDir "TwinCAT.Ads.dll"
if (-not (Test-Path $iface)) { throw "TwinCAT Measurement Automation Interface not found: $iface" }
if (-not (Test-Path $ads)) { throw "TwinCAT.Ads.dll not found: $ads" }

New-Item -ItemType Directory -Path $outDir -Force | Out-Null
Copy-Item $iface (Join-Path $outDir "TwinCAT.Measurement.AutomationInterface.dll") -Force
Copy-Item $ads (Join-Path $outDir "TwinCAT.Ads.dll") -Force

$out = Join-Path $outDir "scope_bridge.exe"
$source = Join-Path $root "scripts\scope_bridge.cs"
& $csc /nologo /target:exe "/out:$out" `
    "/r:$iface" "/r:$ads" `
    /r:System.dll /r:System.Core.dll /r:System.Drawing.dll /r:System.Windows.Forms.dll /r:Microsoft.CSharp.dll `
    $source

if ($LASTEXITCODE -ne 0) { throw "scope_bridge build failed" }
Write-Host "Built $out"
