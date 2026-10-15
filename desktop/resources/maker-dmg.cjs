const { MakerBase } = require('@electron-forge/maker-base');
const { mkdir, mkdtemp, cp, symlink, rm } = require('node:fs/promises');
const { join } = require('node:path');
const { tmpdir } = require('node:os');
const { promisify } = require('node:util');
const execFile = promisify(require('node:child_process').execFile);

module.exports = class MakerDMG extends MakerBase {
  name = 'onw-dmg';
  defaultPlatforms = ['darwin'];

  isSupportedOnCurrentPlatform() { return process.platform === 'darwin'; }

  async make({ dir, makeDir, appName, targetArch }) {
    await mkdir(makeDir, { recursive: true });
    const staging = await mkdtemp(join(tmpdir(), 'onw-dmg-'));
    const artifact = join(makeDir, `${appName}-${this.config.version}-${targetArch}.dmg`);
    try {
      await cp(join(dir, `${appName}.app`), join(staging, `${appName}.app`), { recursive: true, verbatimSymlinks: true });
      await symlink('/Applications', join(staging, 'Applications'));
      await execFile('/usr/bin/hdiutil', ['create', '-volname', appName, '-srcfolder', staging, '-ov', '-format', 'ULFO', artifact], { timeout: 300_000 });
      return [artifact];
    } finally { await rm(staging, { recursive: true, force: true }); }
  }
};
