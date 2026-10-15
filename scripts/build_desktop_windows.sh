#!/usr/bin/env bash
# Cross-builds the Windows x64 desktop installer on a macOS/Linux host. No Windows machine and no
# GitHub Actions: the Windows runtime is assembled from wheels (nothing is executed), and Electron
# Forge packages and builds the Squirrel installer inside an amd64 Linux container that has wine
# and mono (the Squirrel tooling is a Windows program).
#
#   scripts/build_desktop_windows.sh [OUT_DIR]      # default: desktop/out-windows
#
# Produces, unsigned:  Open-Net-Worth-windows-x64-setup.exe  RELEASES  <name>-<version>-full.nupkg
#                      SHA256SUMS
# Signing: set ONW_DESKTOP_WINDOWS_SIGN=1 plus the credentials @electron/windows-sign reads
# (WINDOWS_CERTIFICATE_FILE + WINDOWS_CERTIFICATE_PASSWORD); a .pfx must be inside the mounted
# tree, so signing is normally left to a Windows build host. Without it the build stays unsigned.
#
# Needs: docker (amd64 emulation is fine), uv, node 22+, python3. Docker image
# electronuserland/builder:wine-mono (~1.6 GB compressed) is pulled once; it is not removed.
set -euo pipefail

repo="$(cd "$(dirname "$0")/.." && pwd)"
out="${1:-$repo/desktop/out-windows}"
image="${ONW_WINDOWS_BUILDER_IMAGE:-electronuserland/builder:wine-mono}"
runtime="$repo/desktop/runtime-windows-x64"

cd "$repo"
if [ ! -f "$runtime/manifest.json" ]; then
  python3 scripts/build_desktop_runtime.py --target windows-x64 --output "$runtime"
fi

# The TypeScript and onboarding assets are platform-neutral: build them with the host's node.
(cd desktop && npm ci --no-audit --no-fund && npm run build)

mkdir -p "$out"
docker run --rm --platform linux/amd64 \
  -e ELECTRON_SKIP_BINARY_DOWNLOAD=1 \
  -e WINEDEBUG=-all \
  -e ONW_DESKTOP_WINDOWS_SIGN="${ONW_DESKTOP_WINDOWS_SIGN:-0}" \
  -v "$repo/desktop:/src:ro" \
  -v "$runtime:/runtime:ro" \
  -v "$out:/out" \
  "$image" bash -euxc '
    mkdir /work
    cd /src
    tar --exclude=./node_modules --exclude=./out --exclude="./out-*" --exclude="./runtime*" -cf - . | tar -xf - -C /work
    cd /work
    cp -a /runtime ./runtime
    npm ci --no-audit --no-fund
    # wine64 cannot start the vendored 32-bit rcedit.exe: give Update.exe its icon directly.
    cp -a node_modules/electron-winstaller/vendor /work/squirrel-vendor
    node resources/set-exe-icon.cjs /work/squirrel-vendor/Squirrel.exe resources/icon.ico
    ONW_SQUIRREL_VENDOR_DIR=/work/squirrel-vendor npx electron-forge make --platform=win32 --arch=x64
    rm -rf /out/*
    cp out/make/squirrel.windows/x64/* /out/
  '

cd "$out"
ls -la
rm -f SHA256SUMS
sha256sum -- * 2>/dev/null > SHA256SUMS || shasum -a 256 -- * > SHA256SUMS
echo "Windows installer files in $out"
