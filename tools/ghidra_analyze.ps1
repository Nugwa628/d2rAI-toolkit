# Import and auto-analyse a D2R image dump (tools/dump_image.py) in a headless Ghidra project "D2R".
# Runs detached (analysis of the ~30 MB code section takes hours); progress goes to the log file.
#   powershell -ExecutionPolicy Bypass -File tools/ghidra_analyze.ps1 [-Dump <path>] [-MaxMem 8G] [-Ghidra <dir>] [-Work <dir>]
# -Ghidra: Ghidra install dir (env MODKIT_GHIDRA, default C:\ghidra\ghidra_12.1.4_PUBLIC)
# -Work:   projects/dumps dir (env MODKIT_GHIDRA_WORK, default C:\ghidra-work)
# Project: <Work>\project\D2R.gpr (open it in the Ghidra GUI when analysis is done; close the GUI before queries).
# -Dump defaults to the newest <Work>\D2RLoader_*_dump.exe.
param(
    [string]$Dump = "",
    [string]$MaxMem = "8G",
    [string]$Ghidra = "",
    [string]$Work = ""
)
$ErrorActionPreference = 'Stop'
if (-not $Ghidra) { $Ghidra = if ($env:MODKIT_GHIDRA) { $env:MODKIT_GHIDRA } else { "C:\ghidra\ghidra_12.1.4_PUBLIC" } }
if (-not $Work) { $Work = if ($env:MODKIT_GHIDRA_WORK) { $env:MODKIT_GHIDRA_WORK } else { "C:\ghidra-work" } }
$headless = Join-Path $Ghidra "support\analyzeHeadless.bat"
if (-not (Test-Path $headless)) { throw "Ghidra not found: $headless (set -Ghidra or MODKIT_GHIDRA)" }
New-Item -ItemType Directory -Force $Work | Out-Null
if (-not $Dump) {
    $latest = Get-ChildItem $Work -Filter "D2RLoader_*_dump.exe" | Sort-Object LastWriteTime -Descending | Select-Object -First 1
    if ($latest) { $Dump = $latest.FullName }
}
if (-not $Dump -or -not (Test-Path $Dump)) { throw "no dump found in $Work; run python tools/dump_image.py while the game is running" }
New-Item -ItemType Directory -Force (Join-Path $Work "project") | Out-Null
$log = Join-Path $Work ("analyze_" + (Get-Date -Format "yyyyMMdd_HHmmss") + ".log")
$env:GHIDRA_HEADLESS_MAXMEM = $MaxMem
$argList = @("`"$(Join-Path $Work 'project')`"", "D2R", "-import", "`"$Dump`"", "-overwrite",
             "-log", "`"$log`"")  # no -analysisTimeoutPerFile: 0 means zero seconds, not unlimited
Start-Process -FilePath $headless -ArgumentList $argList `
    -WindowStyle Hidden -RedirectStandardOutput "$log.out" -RedirectStandardError "$log.err"
Write-Output "started: $Dump"
Write-Output "log: $log"
