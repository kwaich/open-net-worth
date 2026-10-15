// Sets the icon of a Windows executable without running it (the job rcedit does, minus wine).
// Usage: node set-exe-icon.cjs <file.exe> <icon.ico>
const { readFileSync, writeFileSync } = require('node:fs');
const ResEdit = require('resedit');

const [target, iconPath] = process.argv.slice(2);
if (!target || !iconPath) throw new Error('Usage: node set-exe-icon.cjs <file.exe> <icon.ico>');
const exe = ResEdit.NtExecutable.from(readFileSync(target));
const resources = ResEdit.NtExecutableResource.from(exe);
const icons = ResEdit.Data.IconFile.from(readFileSync(iconPath)).icons.map(item => item.data);
const existing = ResEdit.Resource.IconGroupEntry.fromEntries(resources.entries)[0];
ResEdit.Resource.IconGroupEntry.replaceIconsForResource(resources.entries, existing ? existing.id : 1, existing ? existing.lang : 1033, icons);
resources.outputResource(exe);
writeFileSync(target, Buffer.from(exe.generate()));
