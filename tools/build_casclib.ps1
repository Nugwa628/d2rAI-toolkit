# Builds CascLib (third_party/CascLib submodule) into build\CascLib\Release\CascLib.dll, used by Python tools via
# ctypes to read D2R's CASC storage.   powershell -ExecutionPolicy Bypass -File tools/build_casclib.ps1
# Needs CMake and Visual Studio (C++ workload). Run from anywhere; paths are relative to the repo root.
$ErrorActionPreference = 'Stop'
$root = Split-Path $PSScriptRoot -Parent
$src = Join-Path $root "third_party\CascLib"
$out = Join-Path $root "build\CascLib"
if (-not (Test-Path (Join-Path $src "CMakeLists.txt"))) {
    git -C $root submodule update --init third_party/CascLib
    if ($LASTEXITCODE -ne 0) { throw "third_party/CascLib is missing and the submodule update failed" }
}
# CascLib still declares an old cmake_minimum_required.
cmake -S $src -B $out -A x64 "-DCMAKE_POLICY_VERSION_MINIMUM=3.5" -DCASC_BUILD_SHARED_LIB=ON
if ($LASTEXITCODE -ne 0) { throw "cmake configure failed ($LASTEXITCODE)" }
cmake --build $out --config Release
if ($LASTEXITCODE -ne 0) { throw "cmake build failed ($LASTEXITCODE)" }
$dll = Join-Path $out "Release\CascLib.dll"
if (-not (Test-Path $dll)) { throw "build finished but $dll is missing" }
Write-Output "built: $dll"
