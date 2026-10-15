#!/usr/bin/env bash
# Build and verify the Linux x86_64 desktop app locally, with Docker, from the repo root:
#
#   scripts/build_desktop_linux.sh            # build runtime + AppImage + .deb, then verify them
#   scripts/build_desktop_linux.sh build      # build only
#   scripts/build_desktop_linux.sh verify     # verify the existing outputs only
#
# Everything runs in throwaway ubuntu:22.04 (glibc 2.35, so the result runs on 22.04 and newer)
# linux/amd64 containers from a clean `git archive HEAD` of the checkout; no Linux files are ever
# written into it. Outputs land in $ONW_LINUX_OUT (default: <repo>/desktop/out/linux-release):
#   Open-Net-Worth-linux-x86_64.AppImage   Open-Net-Worth-linux-amd64.deb   SHA256SUMS
# On an Apple Silicon host the amd64 containers are emulated, so expect a long first build.
set -euo pipefail

IMAGE="ubuntu:22.04"
NODE_VERSION="22.23.3"
NODE_SHA256="df450af89261115ef9f9e3830c3eeb2cc9213b63c720b1af623cb5dcbe2e02de"
UV_VERSION="0.12.22"
UV_SHA256="b9980552309f09c15172b8be828555e375097f16deb459795ce7bfd200380f0b"
APPIMAGE="Open-Net-Worth-linux-x86_64.AppImage"
DEB="Open-Net-Worth-linux-amd64.deb"
RUNTIME_PACKAGES="ca-certificates curl xz-utils python3 fakeroot dpkg xvfb libgtk-3-0 libnss3 libgbm1 libasound2 libxss1 libxtst6 libatspi2.0-0 libsecret-1-0 libnotify4 procps"

toolchain() {
  apt-get update -qq
  DEBIAN_FRONTEND=noninteractive apt-get install -y -qq --no-install-recommends $RUNTIME_PACKAGES >/dev/null
  curl -fsSL "https://nodejs.org/dist/v$NODE_VERSION/node-v$NODE_VERSION-linux-x64.tar.xz" -o /tmp/node.tar.xz
  echo "$NODE_SHA256  /tmp/node.tar.xz" | sha256sum -c -
  tar -xJf /tmp/node.tar.xz -C /usr/local --strip-components=1
  curl -fsSL "https://github.com/astral-sh/uv/releases/download/$UV_VERSION/uv-x86_64-unknown-linux-gnu.tar.gz" -o /tmp/uv.tar.gz
  echo "$UV_SHA256  /tmp/uv.tar.gz" | sha256sum -c -
  tar -xzf /tmp/uv.tar.gz -C /usr/local/bin --strip-components=1
  rm -f /tmp/node.tar.xz /tmp/uv.tar.gz
}

inside_build() {
  toolchain
  mkdir /work && tar -xf /src.tar -C /work && cd /work
  uv run --no-project --python 3.12 python scripts/build_desktop_runtime.py
  cd desktop
  npm ci --no-audit --no-fund
  npm run make
  rm -rf /out/*
  cp "$(find out/make -name '*.AppImage' | head -1)" "/out/$APPIMAGE"
  cp "$(find out/make -name '*.deb' | head -1)" "/out/$DEB"
  (cd /out && sha256sum -- "$APPIMAGE" "$DEB" > SHA256SUMS)
  chmod -R a+rX /out
}

# A fresh container, as an ordinary user: the AppImage smoke, the .deb install + launch (setuid
# sandbox), and the terminal installer against a local file server, including a tampered download.
inside_verify() {
  toolchain
  mkdir /work && tar -xf /src.tar -C /work
  cd /work/desktop && npm ci --no-audit --no-fund --ignore-scripts >/dev/null
  useradd -m tester
  cp -r /out /home/tester/release && chown -R tester /home/tester /work
  su tester -c "cd /work/desktop && timeout 300 xvfb-run -a node qa/packaged-smoke-linux.mjs /home/tester/release/$APPIMAGE"
  apt-get install -y -qq "/home/tester/release/$DEB" >/dev/null
  test "$(stat -c %a /usr/lib/open-net-worth/chrome-sandbox)" = 4755
  test -f /usr/share/applications/open-net-worth.desktop
  su tester -c "cd /work/desktop && timeout 300 xvfb-run -a node qa/packaged-smoke-linux.mjs /usr/lib/open-net-worth/open-net-worth"
  su tester -c "bash /inner.sh installer_check"
  echo "VERIFY_OK"
}

installer_check() {
  local release=/home/tester/release served=/tmp/served tampered=/tmp/tampered
  mkdir -p "$served" "$tampered" && cp "$release"/* "$served"/ && cp "$release"/* "$tampered"/
  printf 'x' >> "$tampered/$APPIMAGE"
  (cd "$served" && timeout 300 python3 -m http.server 8765 --bind 127.0.0.1 >/dev/null 2>&1 &)
  (cd "$tampered" && timeout 300 python3 -m http.server 8766 --bind 127.0.0.1 >/dev/null 2>&1 &)
  sleep 2
  export HOME=/tmp/installer-home && mkdir -p "$HOME"
  install=/work/scripts/install-desktop.sh
  if ONW_RELEASE_BASE_URL=http://127.0.0.1:8766 sh "$install"; then echo "tampered download was installed" >&2; return 1; fi
  test ! -e "$HOME/.local/bin/open-net-worth"
  ONW_RELEASE_BASE_URL=http://127.0.0.1:8765 sh "$install"
  test "$(sha256sum "$HOME/.local/bin/open-net-worth" | cut -d' ' -f1)" = "$(sha256sum "$release/$APPIMAGE" | cut -d' ' -f1)"
  grep -q '^Exec=' "$HOME/.local/share/applications/open-net-worth.desktop"
  test -s "$HOME/.local/share/icons/hicolor/512x512/apps/open-net-worth.png"
  ONW_RELEASE_BASE_URL=http://127.0.0.1:8765 sh "$install" | grep -q "already up to date"
  ONW_RELEASE_BASE_URL=http://127.0.0.1:8765 sh "$install" --uninstall
  test ! -e "$HOME/.local/bin/open-net-worth"
  echo "INSTALLER_OK"
}

host() {
  command -v docker >/dev/null || { echo "docker is required" >&2; exit 1; }
  cd "$(git rev-parse --show-toplevel)"
  [ -z "$(git status --porcelain --untracked-files=no)" ] || echo "warning: uncommitted changes are NOT included; the build uses HEAD" >&2
  out="${ONW_LINUX_OUT:-$PWD/desktop/out/linux-release}"
  mkdir -p "$out"
  tmp="$(mktemp -d)"
  trap 'rm -rf "$tmp"' EXIT
  git archive --format=tar -o "$tmp/src.tar" HEAD
  step="$1"
  run() {
    # `timeout` is inside the container, so a hung step cannot outlive it; --init reaps children.
    docker run --rm --init --platform linux/amd64 -e "ONW_STEP=$1" \
      -v "$tmp/src.tar:/src.tar:ro" -v "$out:/out$2" -v "$PWD/scripts/build_desktop_linux.sh:/inner.sh:ro" \
      "$IMAGE" bash /inner.sh "inside_$1"
  }
  [ "$step" = verify ] || run build ""
  [ "$step" = build ] || run verify ":ro"
  echo "Outputs in $out"
  ls -l "$out"
  cat "$out/SHA256SUMS"
}

case "${1:-all}" in
  inside_build) inside_build ;;
  inside_verify) inside_verify ;;
  installer_check) installer_check ;;
  all|build|verify) host "${1:-all}" ;;
  *) echo "usage: $0 [build|verify]" >&2; exit 2 ;;
esac
