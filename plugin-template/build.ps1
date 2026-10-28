# Configures and builds the D2RLoader plugin (Visual Studio generator, x64, Release).
#   powershell -ExecutionPolicy Bypass -File build.ps1 [-Source <dir>] [-Name mod-core] [-Mod <mod folder>] [-SoundRows <n>] [-Build <dir>] [-Sdk <dir>]
# Output: <Build>\Release\<Name>.dll. Copy it to <game>\mods\<Mod>\d2rloader\plugins\.
param(
    [string]$Source = $PSScriptRoot,               # directory holding this CMakeLists.txt
    [string]$Name = "mod-core",                    # MODKIT_PLUGIN_NAME: DLL name, plugin id, console command
    [string]$Mod = "",                             # MODKIT_MOD_NAME: mods\<Mod> the plugin ships in ("" = no mod check)
    [int]$SoundRows = 0,                           # MODKIT_SOUND_ROWS: rows of the mod's sounds.txt (0 = no new sounds)
    [string]$Build = "",                           # build directory (default <Source>\build)
    [string]$Sdk = ""                              # PluginSDK directory (default <Source>\..\third_party\PluginSDK)
)
$ErrorActionPreference = 'Stop'
$Source = (Resolve-Path $Source).Path
if (-not $Build) { $Build = Join-Path $Source "build" }
$configure = @("-S", $Source, "-B", $Build, "-A", "x64", "-DMODKIT_PLUGIN_NAME=$Name", "-DMODKIT_MOD_NAME=$Mod", "-DMODKIT_SOUND_ROWS=$SoundRows")
if ($Sdk) { $configure += "-DMODKIT_SDK_DIR=$((Resolve-Path $Sdk).Path)" }
cmake @configure
if ($LASTEXITCODE -ne 0) { throw "cmake configure failed ($LASTEXITCODE)" }
cmake --build $Build --config Release
if ($LASTEXITCODE -ne 0) { throw "cmake build failed ($LASTEXITCODE)" }
$dll = Join-Path $Build "Release\$Name.dll"
if (-not (Test-Path $dll)) { throw "build finished but $dll is missing" }
Write-Output "built: $dll"
