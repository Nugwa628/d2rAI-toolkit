#!/bin/sh
# Builds testing/wctl/wctl.exe (x64 Windows console program) on Linux without Visual Studio or Wine:
# clang targets x86_64-pc-windows-msvc, lld-link links, the headers and import libraries come from an MSVC + Windows SDK
# tree in the msvc-wine layout (VC/Tools/MSVC/<ver>, "Windows Kits/10"). Static CRT, so the exe needs only
# user32/kernel32 from the Wine prefix. /Brepro: the same sources give the same bytes.
# Usage: testing/wctl/build.sh [OUT_DIR]       (default OUT_DIR: testing/wctl; *.exe is git-ignored)
# Env:   MSVC_ROOT (required: your msvc-wine tree), CLANG (default clang), LLD_LINK (default lld-link)
set -eu
HERE=$(cd "$(dirname "$0")" && pwd)
REPO=$(cd "$HERE/../.." && pwd)
OUT=${1:-$HERE}
M=${MSVC_ROOT:?set MSVC_ROOT to an msvc-wine tree (see docs/linux.md)}
CLANG=${CLANG:-clang}
LLD=${LLD_LINK:-lld-link}
VC=$(ls -d "$M"/VC/Tools/MSVC/*/ | sort | tail -n 1)
KITS="$M/Windows Kits/10"
SDKV=$(ls "$KITS/Include" | sort | tail -n 1)
for d in "$VC/include" "$KITS/Include/$SDKV/um" "$VC/lib/x64" "$KITS/Lib/$SDKV/um/x64"; do
  [ -d "$d" ] || { echo "missing $d (set MSVC_ROOT to an msvc-wine tree)" >&2; exit 1; }
done
mkdir -p "$OUT"
OBJ="$OUT/wctl.obj"
"$CLANG" --target=x86_64-pc-windows-msvc -O2 -Wall -Wno-unused-parameter -D_CRT_SECURE_NO_WARNINGS -DUNICODE -D_UNICODE \
  -fms-compatibility -fno-stack-protector -ffile-prefix-map="$REPO"=. \
  -nostdinc -isystem "$VC/include" -isystem "$KITS/Include/$SDKV/ucrt" -isystem "$KITS/Include/$SDKV/um" \
  -isystem "$KITS/Include/$SDKV/shared" -c "$HERE/wctl.c" -o "$OBJ"
"$LLD" /nologo /subsystem:console /entry:wmainCRTStartup /out:"$OUT/wctl.exe" /Brepro \
  /libpath:"$VC/lib/x64" /libpath:"$KITS/Lib/$SDKV/um/x64" /libpath:"$KITS/Lib/$SDKV/ucrt/x64" \
  "$OBJ" libcmt.lib libvcruntime.lib libucrt.lib kernel32.lib user32.lib
rm -f "$OBJ"
echo "$OUT/wctl.exe"
