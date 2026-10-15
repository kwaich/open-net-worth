#!/bin/sh
# Open Net Worth desktop app: install, upgrade or uninstall from a terminal.
#
#   curl -fsSL https://opennetworth.ai/install.sh | sh
#   curl -fsSL https://opennetworth.ai/install.sh | sh -s -- --uninstall
#
# Published as `install.sh` next to the installers on the public releases page. It downloads the
# app for this computer plus the release's SHA256SUMS over https, verifies the checksum BEFORE
# anything is installed or executed, then:
#   Linux x86_64   installs the AppImage to ~/.local/bin/open-net-worth with a launcher entry and
#                  icon under ~/.local/share (no sudo)
#   macOS arm64    copies Open Net Worth.app to /Applications (or ~/Applications when that is not
#                  writable) and checks it with Gatekeeper (spctl)
# Re-running upgrades in place. --uninstall removes the app and its launcher entries; your book
# (~/.open-net-worth or the folder you chose) is never touched.
#
# Test seams (not for normal use): ONW_RELEASE_BASE_URL, ONW_INSTALL_OS, ONW_INSTALL_ARCH.
set -eu

REPO_URL="https://github.com/open-net-worth-ai/open-net-worth-releases"
BASE_URL="${ONW_RELEASE_BASE_URL:-$REPO_URL/releases/latest/download}"
OS="${ONW_INSTALL_OS:-$(uname -s 2>/dev/null || echo unknown)}"
ARCH="${ONW_INSTALL_ARCH:-$(uname -m 2>/dev/null || echo unknown)}"
LINUX_ASSET="Open-Net-Worth-linux-x86_64.AppImage"
MAC_ASSET="Open-Net-Worth-mac-arm64.dmg"
APP_NAME="Open Net Worth"

say() { printf '==> %s\n' "$1"; }
warn() { printf 'warning: %s\n' "$1" >&2; }
die() { printf '\nERROR: %s\n' "$1" >&2; exit 1; }

usage() {
  cat <<'EOF'
Usage: install.sh [--uninstall]

  (no option)   install Open Net Worth, or upgrade it to the latest release
  --uninstall   remove the app and its launcher entries (your book is kept)
EOF
}

ACTION=install
for arg in "$@"; do
  case "$arg" in
    --uninstall) ACTION=uninstall ;;
    -h|--help) usage; exit 0 ;;
    *) usage >&2; die "unknown option: $arg" ;;
  esac
done

TMP=""
MOUNT=""
cleanup() {
  if [ -n "$MOUNT" ]; then hdiutil detach "$MOUNT" -quiet >/dev/null 2>&1 || hdiutil detach "$MOUNT" -force -quiet >/dev/null 2>&1 || true; fi
  if [ -n "$TMP" ]; then rm -rf "$TMP"; fi
}
trap cleanup EXIT
trap 'exit 1' INT TERM HUP

fetch() {
  if [ -n "${ONW_RELEASE_BASE_URL:-}" ]; then proto='=https,http'; else proto='=https'; fi
  curl --proto "$proto" --tlsv1.2 -fsSL --retry 2 --connect-timeout 20 -o "$2" "$1"
}

sha256_of() {
  if command -v sha256sum >/dev/null 2>&1; then sha256sum "$1" | awk '{print $1}'
  elif command -v shasum >/dev/null 2>&1; then shasum -a 256 "$1" | awk '{print $1}'
  else die "sha256sum or shasum is required to verify the download"; fi
}

# The published checksum for one asset, or nothing. Accepts text ("  name") and binary (" *name") lines.
expected_sum() {
  awk -v n="$2" '$2 == n || $2 == "*" n { print $1; exit }' "$1"
}

need_tools() {
  command -v curl >/dev/null 2>&1 || die "curl is required"
  if [ -z "${HOME:-}" ] || [ ! -d "$HOME" ]; then die "HOME is not set to a directory"; fi
  TMP="$(mktemp -d "${TMPDIR:-/tmp}/onw-install.XXXXXX")" || die "could not create a temporary directory"
}

# Downloads SHA256SUMS; the asset is fetched and verified by the caller. Fails closed when the
# release publishes no checksums or none for this asset.
fetch_sums() {
  fetch "$BASE_URL/SHA256SUMS" "$TMP/SHA256SUMS" || die "this release has no SHA256SUMS, so the download cannot be verified; nothing was installed ($REPO_URL/releases/latest)"
  WANT="$(expected_sum "$TMP/SHA256SUMS" "$1")"
  [ -n "$WANT" ] || die "SHA256SUMS has no entry for $1; nothing was installed"
}

verify() {
  GOT="$(sha256_of "$1")"
  [ "$GOT" = "$WANT" ] || die "checksum mismatch for $2 (expected $WANT, got $GOT); the download was discarded and nothing was installed"
}

safe_path() {
  case "$1" in
    *[\"\`\$\\%]*|*'
'*) die "refusing to install into a path with special characters: $1" ;;
  esac
}

# ---------------------------------------------------------------- Linux

linux_paths() {
  BIN_DIR="${ONW_BIN_DIR:-$HOME/.local/bin}"
  DATA_DIR="${XDG_DATA_HOME:-$HOME/.local/share}"
  CONFIG_DIR="${XDG_CONFIG_HOME:-$HOME/.config}"
  TARGET="$BIN_DIR/open-net-worth"
  ENTRY="$DATA_DIR/applications/open-net-worth.desktop"
  ICON="$DATA_DIR/icons/hicolor/512x512/apps/open-net-worth.png"
  AUTOSTART="$CONFIG_DIR/autostart/open-net-worth.desktop"
  safe_path "$TARGET"
}

linux_install() {
  linux_paths
  need_tools
  fetch_sums "$LINUX_ASSET"
  mkdir -p "$BIN_DIR"
  if [ -f "$TARGET" ] && [ "$(sha256_of "$TARGET")" = "$WANT" ]; then
    say "Open Net Worth is already up to date"
  else
    say "Downloading $LINUX_ASSET ..."
    fetch "$BASE_URL/$LINUX_ASSET" "$TMP/$LINUX_ASSET" || die "could not download $LINUX_ASSET"
    verify "$TMP/$LINUX_ASSET" "$LINUX_ASSET"
    say "Checksum verified"
    chmod 755 "$TMP/$LINUX_ASSET"
    # Written beside the target and renamed over it, so a running copy and an interrupted run are both safe.
    if ! { cp "$TMP/$LINUX_ASSET" "$TARGET.new.$$" && mv -f "$TARGET.new.$$" "$TARGET"; }; then
      rm -f "$TARGET.new.$$"
      die "could not write $TARGET"
    fi
    linux_icon
  fi
  linux_entry
  say "Installed to $TARGET"
  case ":$PATH:" in *":$BIN_DIR:"*) ;; *) say "Add $BIN_DIR to your PATH to run \`open-net-worth\` from a terminal" ;; esac
  say "Start it from your applications menu (Open Net Worth) or run: open-net-worth"
  say "It updates itself in place when a new version is released. Re-run this installer any time to upgrade."
}

# The verified image carries its own icon; extracting it never starts the app and needs no FUSE.
linux_icon() {
  mkdir -p "$(dirname "$ICON")"
  if (cd "$TMP" && APPIMAGE_EXTRACT_AND_RUN=1 "./$LINUX_ASSET" --appimage-extract open-net-worth.png >/dev/null 2>&1) && [ -f "$TMP/squashfs-root/open-net-worth.png" ]; then
    cp "$TMP/squashfs-root/open-net-worth.png" "$ICON"
  else
    warn "could not extract the app icon; the launcher entry will use a generic icon"
  fi
}

linux_entry() {
  mkdir -p "$(dirname "$ENTRY")"
  cat > "$ENTRY.new.$$" <<EOF
[Desktop Entry]
Type=Application
Name=$APP_NAME
GenericName=Wealth manager
Comment=Your wealth, on your computer
Exec="$TARGET" %U
Icon=open-net-worth
Terminal=false
StartupNotify=true
StartupWMClass=open-net-worth
Categories=Office;Finance;
MimeType=x-scheme-handler/onw;
EOF
  mv -f "$ENTRY.new.$$" "$ENTRY"
  if command -v update-desktop-database >/dev/null 2>&1; then update-desktop-database "$(dirname "$ENTRY")" >/dev/null 2>&1 || true; fi
}

linux_uninstall() {
  linux_paths
  rm -f "$TARGET" "$ENTRY" "$ICON" "$AUTOSTART"
  say "Removed Open Net Worth and its launcher entries"
  say "Your book was not touched (by default ~/.open-net-worth); delete that folder yourself if you want it gone"
}

# ---------------------------------------------------------------- macOS

mac_dest() {
  if [ -w /Applications ]; then DEST_DIR="/Applications"; else DEST_DIR="$HOME/Applications"; fi
}

mac_running() { pgrep -f "$APP_NAME.app/Contents/MacOS/" >/dev/null 2>&1; }

mac_install() {
  [ "$ARCH" = "arm64" ] || die "Open Net Worth for macOS is Apple Silicon only for now (this Mac reports $ARCH)"
  need_tools
  mac_dest
  mac_running && die "$APP_NAME is running; quit it and run the installer again"
  fetch_sums "$MAC_ASSET"
  say "Downloading $MAC_ASSET ..."
  fetch "$BASE_URL/$MAC_ASSET" "$TMP/$MAC_ASSET" || die "could not download $MAC_ASSET"
  verify "$TMP/$MAC_ASSET" "$MAC_ASSET"
  say "Checksum verified"
  mkdir -p "$TMP/mnt"
  hdiutil attach -nobrowse -readonly -noautoopen -mountpoint "$TMP/mnt" "$TMP/$MAC_ASSET" >/dev/null || die "could not open the disk image"
  MOUNT="$TMP/mnt"
  [ -d "$MOUNT/$APP_NAME.app" ] || die "the disk image does not contain $APP_NAME.app"
  mkdir -p "$DEST_DIR"
  STAGED="$DEST_DIR/.$APP_NAME.app.new.$$"
  rm -rf "$STAGED"
  ditto "$MOUNT/$APP_NAME.app" "$STAGED" || { rm -rf "$STAGED"; die "could not copy the app into $DEST_DIR"; }
  if ! spctl --assess --type execute "$STAGED" >/dev/null 2>&1; then
    rm -rf "$STAGED"
    die "Gatekeeper does not accept this build (spctl --assess failed); nothing was installed"
  fi
  rm -rf "$DEST_DIR/$APP_NAME.app"
  mv "$STAGED" "$DEST_DIR/$APP_NAME.app"
  say "Installed $DEST_DIR/$APP_NAME.app (accepted by Gatekeeper)"
  say "Open it from Launchpad or Spotlight. It updates itself; re-run this installer any time to upgrade."
}

mac_uninstall() {
  mac_running && die "$APP_NAME is running; quit it and run the uninstaller again"
  removed=0
  for dir in /Applications "$HOME/Applications"; do
    if [ -d "$dir/$APP_NAME.app" ]; then
      rm -rf "$dir/$APP_NAME.app" 2>/dev/null || die "could not remove $dir/$APP_NAME.app; drag it to the Trash instead"
      removed=1
    fi
  done
  if [ "$removed" = 1 ]; then say "Removed $APP_NAME.app"; else say "$APP_NAME.app was not installed"; fi
  say "Your book was not touched (by default ~/.open-net-worth); delete that folder yourself if you want it gone"
}

# ---------------------------------------------------------------- dispatch

case "$OS" in
  Linux)
    [ "$ARCH" = "x86_64" ] || [ "$ARCH" = "amd64" ] || die "Open Net Worth for Linux is x86_64 only for now (this computer reports $ARCH)"
    "linux_$ACTION" ;;
  Darwin) "mac_$ACTION" ;;
  MINGW*|MSYS*|CYGWIN*) die "this installer is for macOS and Linux; on Windows download the installer from https://opennetworth.ai" ;;
  *) die "unsupported operating system '$OS'; downloads are listed at $REPO_URL/releases/latest" ;;
esac
