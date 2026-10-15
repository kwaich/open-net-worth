const { MakerBase } = require('@electron-forge/maker-base');
const { chmod, copyFile, cp, mkdir, mkdtemp, readFile, rename, rm, symlink, writeFile } = require('node:fs/promises');
const { createHash } = require('node:crypto');
const { join } = require('node:path');
const { tmpdir } = require('node:os');
const { promisify } = require('node:util');
const execFile = promisify(require('node:child_process').execFile);

// Pinned by content: a changed or tampered download never reaches a release build.
const TOOLS = {
  appimagetool: {
    url: 'https://github.com/AppImage/appimagetool/releases/download/1.9.1/appimagetool-x86_64.AppImage',
    sha256: 'ed4ce84f0d9caff66f50bcca6ff6f35aae54ce8135408b3fa33abfc3cb384eb0',
  },
  runtime: {
    url: 'https://github.com/AppImage/type2-runtime/releases/download/20251108/runtime-x86_64',
    sha256: '2fca8b443c92510f1483a883f60061ad09b46b978b2631c807cd873a47ec260d',
  },
};
const CACHE = join(__dirname, '..', '.cache', 'appimage');
const EXECUTABLE = 'open-net-worth';

// Chromium's sandbox needs unprivileged user namespaces (the setuid helper cannot work inside a
// read-only, nosuid AppImage mount). Where the OS forbids them — Ubuntu 24.04's AppArmor default
// — the app would not start at all, so the sandbox is dropped there and nowhere else.
const APP_RUN = `#!/bin/sh
here="$(dirname "$(readlink -f "$0")")"
userns_blocked() {
  [ "$(cat /proc/sys/kernel/apparmor_restrict_unprivileged_userns 2>/dev/null)" = 1 ] ||
  [ "$(cat /proc/sys/kernel/unprivileged_userns_clone 2>/dev/null)" = 0 ] ||
  [ "$(cat /proc/sys/user/max_user_namespaces 2>/dev/null)" = 0 ]
}
if userns_blocked; then
  echo "open-net-worth: this system blocks unprivileged user namespaces, starting without the Chromium sandbox" >&2
  set -- --no-sandbox "$@"
fi
exec "$here/usr/lib/${EXECUTABLE}/${EXECUTABLE}" "$@"
`;

const desktopEntry = (appName, version) => `[Desktop Entry]
Type=Application
Name=${appName}
GenericName=Wealth manager
Comment=Your wealth, on your computer
Exec=${EXECUTABLE}
Icon=${EXECUTABLE}
Terminal=false
StartupNotify=true
StartupWMClass=${EXECUTABLE}
Categories=Office;Finance;
X-AppImage-Version=${version}
`;

async function fetchTool(name) {
  const { url, sha256 } = TOOLS[name];
  const file = join(CACHE, name);
  try {
    if (createHash('sha256').update(await readFile(file)).digest('hex') === sha256) return file;
  } catch { /* not cached yet */ }
  const response = await fetch(url, { signal: AbortSignal.timeout(300_000) });
  if (!response.ok) throw new Error(`Could not download ${name}: HTTP ${response.status}`);
  const bytes = Buffer.from(await response.arrayBuffer());
  if (createHash('sha256').update(bytes).digest('hex') !== sha256) throw new Error(`${name} does not match its pinned SHA-256.`);
  await mkdir(CACHE, { recursive: true });
  const staged = `${file}.${process.pid}`;
  await writeFile(staged, bytes, { mode: 0o755 });
  await rename(staged, file);
  return file;
}

module.exports = class MakerAppImage extends MakerBase {
  name = 'onw-appimage';
  defaultPlatforms = ['linux'];

  isSupportedOnCurrentPlatform() { return process.platform === 'linux'; }

  async make({ dir, makeDir, appName, targetArch }) {
    if (targetArch !== 'x64') throw new Error(`AppImage packaging supports x64 only, not ${targetArch}.`);
    const [appimagetool, runtime] = [await fetchTool('appimagetool'), await fetchTool('runtime')];
    await mkdir(makeDir, { recursive: true });
    const staging = await mkdtemp(join(tmpdir(), 'onw-appimage-'));
    const artifact = join(makeDir, `${appName}-${this.config.version}-x86_64.AppImage`);
    try {
      const appDir = join(staging, 'AppDir');
      await cp(dir, join(appDir, 'usr', 'lib', EXECUTABLE), { recursive: true, verbatimSymlinks: true });
      await writeFile(join(appDir, 'AppRun'), APP_RUN, { mode: 0o755 });
      await chmod(join(appDir, 'AppRun'), 0o755);
      await writeFile(join(appDir, `${EXECUTABLE}.desktop`), desktopEntry(appName, this.config.version));
      const icons = join(appDir, 'usr', 'share', 'icons', 'hicolor', '512x512', 'apps');
      await mkdir(icons, { recursive: true });
      await copyFile(join(__dirname, 'icon.png'), join(icons, `${EXECUTABLE}.png`));
      await copyFile(join(__dirname, 'icon.png'), join(appDir, `${EXECUTABLE}.png`));
      await symlink(`${EXECUTABLE}.png`, join(appDir, '.DirIcon'));
      await rm(artifact, { force: true });
      await execFile(appimagetool, ['--appimage-extract-and-run', '--no-appstream', '--runtime-file', runtime, appDir, artifact], { env: { ...process.env, ARCH: 'x86_64' }, timeout: 600_000 });
      return [artifact];
    } finally { await rm(staging, { recursive: true, force: true }); }
  }
};
