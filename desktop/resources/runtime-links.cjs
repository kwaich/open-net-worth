const { lstat, readdir, readlink, realpath, symlink, unlink } = require('node:fs/promises');
const path = require('node:path');

function contained(root, target) {
  const relative = path.relative(root, target);
  return !path.isAbsolute(relative) && relative !== '..' && !relative.startsWith(`..${path.sep}`);
}

async function runtimeLinks(directory) {
  const links = [];
  for (const entry of await readdir(directory, { withFileTypes: true })) {
    const file = path.join(directory, entry.name);
    if (entry.isSymbolicLink()) links.push(file);
    else if (entry.isDirectory()) links.push(...await runtimeLinks(file));
  }
  return links;
}

async function validateRuntimeLinks(directory, relativeOnly = false) {
  const root = await realpath(directory);
  const links = await runtimeLinks(root);
  for (const link of links) {
    const target = await realpath(link);
    if (!contained(root, target) || relativeOnly && path.isAbsolute(await readlink(link))) throw new Error('The Python runtime contains a nonportable symlink.');
  }
  return { root, links };
}

async function normalizeCopiedRuntime(source, destination) {
  const { root: sourceRoot, links } = await validateRuntimeLinks(source);
  const destinationRoot = await realpath(destination);
  for (const link of links) {
    const target = await realpath(link);
    const copiedLink = path.join(destinationRoot, path.relative(sourceRoot, link));
    if (!(await lstat(copiedLink)).isSymbolicLink()) throw new Error('The packaged runtime does not match its source.');
    const copiedTarget = path.join(destinationRoot, path.relative(sourceRoot, target));
    const relativeTarget = path.relative(path.dirname(copiedLink), copiedTarget);
    await unlink(copiedLink);
    await symlink(relativeTarget, copiedLink, (await lstat(target)).isDirectory() ? 'dir' : 'file');
  }
  await validateRuntimeLinks(destinationRoot, true);
}

function runtimeResourceHooks(source, appName) {
  return {
    beforeCopyExtraResources: [async () => validateRuntimeLinks(source)],
    afterCopyExtraResources: [async ({ buildPath, platform }) => {
      const resources = platform === 'darwin' || platform === 'mas' ? path.join(buildPath, `${appName}.app`, 'Contents', 'Resources') : path.join(buildPath, 'resources');
      await normalizeCopiedRuntime(source, path.join(resources, 'runtime'));
    }],
  };
}

module.exports = { validateRuntimeLinks, normalizeCopiedRuntime, runtimeResourceHooks };
