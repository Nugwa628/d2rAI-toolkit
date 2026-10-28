# Run a Ghidra script from tools/ghidra_scripts against the analysed D2R project (read-only, no re-analysis) and
# print its output lines.
#   powershell -ExecutionPolicy Bypass -File tools/ghidra_query.ps1 <Script.java> [-Ghidra <dir>] [-Work <dir>] [args...]
#   e.g.  tools/ghidra_query.ps1 Decompile.java 0x3e3f60      (writes <Work>\decomp\0x3e3f60.c)
# -Ghidra: Ghidra install dir (env MODKIT_GHIDRA, default C:\ghidra\ghidra_12.1.4_PUBLIC)
# -Work:   projects/dumps dir (env MODKIT_GHIDRA_WORK, default C:\ghidra-work); the project is
#          <Work>\project\D2R.gpr made by ghidra_analyze.ps1, the program the newest <Work>\D2RLoader_*_dump.exe.
# Full Ghidra output: <Work>\query.log. The project must not be open in the Ghidra GUI (it is locked then).
param([Parameter(Mandatory = $true, Position = 0)][string]$Script,
      [string]$Ghidra = "",
      [string]$Work = "",
      [Parameter(ValueFromRemainingArguments = $true)][string[]]$ScriptArgs)
$ErrorActionPreference = 'Stop'
if (-not $Ghidra) { $Ghidra = if ($env:MODKIT_GHIDRA) { $env:MODKIT_GHIDRA } else { "C:\ghidra\ghidra_12.1.4_PUBLIC" } }
if (-not $Work) { $Work = if ($env:MODKIT_GHIDRA_WORK) { $env:MODKIT_GHIDRA_WORK } else { "C:\ghidra-work" } }
$headless = Join-Path $Ghidra "support\analyzeHeadless.bat"
if (-not (Test-Path $headless)) { throw "Ghidra not found: $headless (set -Ghidra or MODKIT_GHIDRA)" }
$projDir = Join-Path $Work "project"
if (-not (Test-Path (Join-Path $projDir "D2R.gpr"))) { throw "no D2R project in $projDir; run tools/ghidra_analyze.ps1 first" }
$dump = Get-ChildItem $Work -Filter "D2RLoader_*_dump.exe" | Sort-Object LastWriteTime -Descending | Select-Object -First 1
if (-not $dump) { throw "no D2RLoader_*_dump.exe in $Work" }
$log = Join-Path $Work "query.log"
$env:GHIDRA_HEADLESS_MAXMEM = "6G"
$argLine = @("`"$projDir`"", "D2R", "-process", $dump.Name, "-noanalysis", "-readOnly",
             "-scriptPath", "`"$(Join-Path $PSScriptRoot 'ghidra_scripts')`"", "-postScript", $Script) + $ScriptArgs
$p = Start-Process -FilePath $headless -ArgumentList $argLine -NoNewWindow `
    -Wait -PassThru -RedirectStandardOutput $log -RedirectStandardError "$log.err"
Get-Content $log | Where-Object { $_ -match "\.java> " } | ForEach-Object { $_ -replace '^.*?\.java> ', '' -replace '\s+\(GhidraScript\)\s*$', '' }
Get-Content $log | Where-Object { $_ -match "ERROR|Exception" } | Select-Object -First 5
exit $p.ExitCode
