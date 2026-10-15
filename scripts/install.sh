#!/bin/sh
# Open Net Worth — install (or update) on macOS / Linux with nothing preinstalled.
#
#   curl -fsSL https://github.com/open-net-worth-ai/open-net-worth-releases/releases/latest/download/install-server.sh | sh
#   curl -fsSL ...install-server.sh | sh -s -- --autostart   # also start at login/boot, restart on crash
#
# This is the SERVER installer (a release asset named install-server.sh). The desktop app has its own,
# https://opennetworth.ai/install.sh.
#
# What it does, and nothing else:
#   1. installs uv (https://docs.astral.sh/uv/) into ~/.local/bin if it is missing
#   2. `uv tool install open-net-worth` on uv's OWN Python 3.12 (--managed-python), never a
#      system/Homebrew one — a `brew upgrade` deletes the old interpreter and would leave the
#      install dangling. No system Python or Node.js is needed; everything lands in uv's tool dir
#   3. with --autostart: `onw autostart install`
# Re-running it upgrades to the newest release. Your data lives in ~/.open-net-worth/, outside
# the install, so upgrades and reinstalls never touch it. Windows: use Docker (docs, Installation).
set -eu

say() { printf '==> %s\n' "$1"; }
die() { printf '\nERROR: %s\n' "$1" >&2; exit 1; }

INSTALL_DOCS="https://docs.opennetworth.ai/getting-started/installation/"
DOCKER_HINT="use Docker instead: $INSTALL_DOCS"
case "$(uname -s 2>/dev/null || echo unknown)" in
  Darwin|Linux) ;;
  MINGW*|MSYS*|CYGWIN*) die "native Windows is not supported (the backend needs POSIX file locking) — $DOCKER_HINT" ;;
  *) die "unsupported platform '$(uname -s)' — $DOCKER_HINT" ;;
esac

AUTOSTART=0
for arg in "$@"; do
  case "$arg" in
    --autostart) AUTOSTART=1 ;;
    *) die "unknown option: $arg (the only option is --autostart)" ;;
  esac
done

command -v curl >/dev/null 2>&1 || die "curl is required to download uv"

export PATH="$HOME/.local/bin:$PATH"
if ! command -v uv >/dev/null 2>&1; then
  say "Installing uv (the Python app installer) into ~/.local/bin ..."
  curl -LsSf https://astral.sh/uv/install.sh | sh -s -- --quiet
  command -v uv >/dev/null 2>&1 || die "uv did not end up on PATH — install it by hand (https://docs.astral.sh/uv/) and re-run"
fi
say "uv $(uv --version 2>/dev/null | awk '{print $2}')"

# The package may not be on PyPI yet (publishing is switched on release by release): say so and
# point at what works today instead of letting uv print a bare "not found in the package registry".
if [ "$(curl -s -o /dev/null -w '%{http_code}' https://pypi.org/pypi/open-net-worth/json)" = 404 ]; then
  die "open-net-worth is not published on PyPI yet. The desktop app (https://opennetworth.ai) and the Docker image work today: $INSTALL_DOCS"
fi

say "Installing open-net-worth (the first run downloads a few hundred MB of dependencies) ..."
uv tool install --managed-python --python 3.12 --upgrade open-net-worth

ONW="$(command -v onw 2>/dev/null || true)"
[ -n "$ONW" ] || ONW="$HOME/.local/bin/onw"
[ -x "$ONW" ] || die "installed, but 'onw' was not found — run 'uv tool update-shell', open a new terminal and re-run"
say "Installed $("$ONW" version)"

if [ "$AUTOSTART" = 1 ]; then
  "$ONW" autostart install
fi

echo ""
echo "==> Done."
echo "    Run it:           onw start --open"
if [ "$AUTOSTART" = 1 ]; then
  echo "    It is running now and will start at login: open http://127.0.0.1:8080"
else
  echo "    Start at login:   onw autostart install"
fi
echo "    Update later:     onw upgrade"
echo "    Your data:        ~/.open-net-worth/  (outside the install; upgrades never touch it)"
if ! command -v onw >/dev/null 2>&1; then
  echo ""
  echo "NOTE: ~/.local/bin is not on your PATH yet — open a new terminal, or run: uv tool update-shell"
fi
