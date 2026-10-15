# Linux desktop ships an AppImage and a .deb, and checks for its own updates

Status: accepted. Scope: desktop host on Linux x86_64; macOS behaviour is unchanged.

**Formats.** The primary download is an AppImage (one file, no root, runs on any glibc
distribution); the `.deb` serves Debian/Ubuntu users who prefer a system package. Forge has no
maintained AppImage maker, so `desktop/resources/maker-appimage.cjs` builds it from the packaged
app with `appimagetool` and the type2 runtime, both pinned by URL **and SHA-256** (a changed
download fails the build). The `.deb` uses `@electron-forge/maker-deb`. Published names are stable
(`Open-Net-Worth-linux-x86_64.AppImage`, `Open-Net-Worth-linux-amd64.deb`) so landing-page links
survive releases. arm64 Linux is not built: the bundled CPython runtime and CI are x64-only.

**Sandbox.** Chromium's sandbox needs unprivileged user namespaces, which a read-only `nosuid`
AppImage mount cannot replace with the setuid helper. Ubuntu 24.04 forbids them to unconfined
programs by default, so a stock Electron AppImage would not start there. The AppImage's `AppRun`
therefore adds `--no-sandbox` only when the kernel reports user namespaces blocked, and says so on
stderr. The `.deb` installs `chrome-sandbox` setuid root, so its sandbox is always on; CI asserts
mode 4755 and runs the packaged smoke test against the installed binary. The renderer loads only
the app's own `127.0.0.1` pages under context isolation, so the residual risk of the AppImage
fallback is accepted.

**Updates.** Electron's `autoUpdater` does not exist on Linux and there is no update server for
it, so `linux-updates.ts` reads the public releases repo's latest release from the GitHub API
(https only, no token, redirects restricted to GitHub hosts, assets restricted to the repo's own
download path) and compares plain `x.y.z` tags. It plugs into the existing `UpdateController` as an
`Updater`, so the notification, menu and tray behaviour matches macOS. Two outcomes:

- Running from a writable AppImage: the new AppImage is downloaded beside the old one, its SHA-256
  is checked against the release's `SHA256SUMS`, it must start with the ELF magic, and only then
  is it renamed over the old file. A mismatch deletes the download and changes nothing. The
  running process keeps its old inode; "Restart to Update…" relaunches the new file.
- Anything else (the `.deb`, a read-only folder): the app only notifies and opens the download
  page (`update-manual`, "Download Update…"). It never installs system packages itself.

`SHA256SUMS` is published beside the assets by the same GitHub release, so it protects against
corruption and a mismatched asset, not against a compromised releases repo or token. Detached
signatures (minisign/GPG) would close that and are a follow-up.

**Amendment 2026-10-04.** Done for the in-app updater: the publisher signs `SHA256SUMS` with
minisign (`SHA256SUMS.minisig`) from the `ONW_MINISIGN_SECRET_KEY` secret and the app verifies it
in-process against a public key compiled in (`desktop/src/main/update-key.ts`) before reading any
hash. No key, no signature asset or an unwritable location means the manual download page; an
invalid signature is an error. The release workflow fails loudly without the secret or key. The
terminal installer (`install.sh`) still checks only `SHA256SUMS` over https.

**Start at login.** `setLoginItemSettings` does nothing on Linux, so the host writes and removes
`~/.config/autostart/open-net-worth.desktop` (XDG, honouring `XDG_CONFIG_HOME`) launching with the
existing `--onw-login` flag.

**Window close.** Linux keeps the non-macOS rule: closing the window quits unless background mode
is on. Where the desktop environment has no system tray (stock GNOME), a hidden background app can
be brought back by launching it again from the applications menu (single-instance activation).

**Installer.** `scripts/install-desktop.sh`, published as the release asset `install.sh`
(`curl -fsSL https://opennetworth.ai/install.sh | sh`), downloads over https, verifies the asset's
SHA-256 against `SHA256SUMS` before installing or executing anything, fails closed when the release
has no checksums, and is idempotent (re-run = upgrade). It is a separate file from
`scripts/install.sh`, the uv-based CLI installer documented in the README. The release publisher
generates one `SHA256SUMS` over every desktop asset of every platform job.
