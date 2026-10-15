// Node and Python must agree on a file's identity: the watched-folder grant is recorded from
// Node's lstat and verified by the Python helper's fstat. Windows reports 64-bit file indexes
// through a double in Node, so this prints both sides and fails when they diverge.
// Usage: ONW_DESKTOP_PYTHON=<python> node qa/identity-check.mjs
import assert from "node:assert/strict";
import { execFileSync } from "node:child_process";
import { lstat, mkdtemp, mkdir, writeFile } from "node:fs/promises";
import { tmpdir } from "node:os";
import path from "node:path";

const python = process.env.ONW_DESKTOP_PYTHON ?? "python3";
const root = await mkdtemp(path.join(tmpdir(), "onw-identity-"));
const folder = path.join(root, "Statements");
await mkdir(folder);
const file = path.join(folder, "statement.csv");
await writeFile(file, "date,amount\n2026-01-01,1\n");
const probe = (target) =>
  JSON.parse(
    execFileSync(
      python,
      ["-c", "import json,os,sys; s=os.stat(sys.argv[1]); print(json.dumps({'dev': str(s.st_dev), 'ino': str(s.st_ino)}))", target],
      { encoding: "utf8" },
    ),
  );
for (const target of [folder, file]) {
  const node = await lstat(target);
  const py = probe(target);
  console.log(target, "node", { dev: node.dev, ino: node.ino }, "python", py);
  // Python reads a Windows volume serial as 64 bits; Node reports the low 32.
  const device = process.platform === "win32" ? BigInt(py.dev) & 0xffffffffn : BigInt(py.dev);
  assert.equal(Number(device), node.dev, `device differs for ${target}`);
  assert.equal(Number(BigInt(py.ino)), node.ino, `inode differs for ${target}`);
}
console.log("IDENTITY_OK");
