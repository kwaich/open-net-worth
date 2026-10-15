import assert from "node:assert/strict";
import { spawn, execFileSync } from "node:child_process";
import { randomBytes } from "node:crypto";
import { chmod, copyFile, mkdir, mkdtemp, readdir, readFile, realpath, stat, writeFile } from "node:fs/promises";
import { tmpdir } from "node:os";
import path from "node:path";

// Black-box check of a packaged Linux build: an .AppImage (run from a path with spaces, as a user
// would keep it) or an installed executable such as the .deb's /usr/lib/open-net-worth/open-net-worth.
const source = process.argv[2];
assert.ok(source, "Usage: node qa/packaged-smoke-linux.mjs /absolute/Open-Net-Worth.AppImage|/absolute/open-net-worth");
const appImage = source.endsWith(".AppImage");
const profile = await mkdtemp(path.join(tmpdir(), "onw-packaged-profile-"));
await mkdir("qa/artifacts", { recursive: true });
await mkdir(path.join(profile, "book"));
let executable = path.resolve(source);
if (appImage) {
  const relocated = path.join(profile, "App With Spaces", path.basename(source));
  await mkdir(path.dirname(relocated));
  await copyFile(source, relocated);
  await chmod(relocated, 0o755);
  executable = relocated;
  // The desktop entry and icon a launcher integrates must be inside the image.
  const extracted = path.join(profile, "extracted");
  await mkdir(extracted);
  execFileSync(executable, ["--appimage-extract", "open-net-worth.desktop"], { cwd: extracted, env: { ...process.env, APPIMAGE_EXTRACT_AND_RUN: "1" }, stdio: "ignore" });
  const entry = await readFile(path.join(extracted, "squashfs-root", "open-net-worth.desktop"), "utf8");
  assert.match(entry, /^Exec=open-net-worth$/m);
  assert.match(entry, /^Categories=Office;Finance;$/m);
}
await writeFile(
  path.join(profile, "desktop-settings.json"),
  JSON.stringify({ background: false, launchAtLogin: false, notifications: true, selectedBookFolder: await realpath(path.join(profile, "book")), books: {} }),
);
const forbidden = path.join(profile, "ambient-book-must-not-exist");
const env = {
  ...process.env,
  PATH: "/usr/bin:/bin:/usr/sbin:/sbin",
  ONW_ROOT: forbidden,
  ONW_DESKTOP_ROOT: forbidden,
  ONW_DESKTOP_REPO: "/missing-checkout",
  ONW_DESKTOP_PYTHON: "/missing-python",
  ONW_QA_MODE: "1",
  ONW_DESKTOP_UPDATES: "0",
  PYTHONPATH: "/missing-python-path",
  ...(appImage ? { APPIMAGE_EXTRACT_AND_RUN: "1" } : {}),
};
const child = spawn(executable, [`--user-data-dir=${profile}`], { env, stdio: ["ignore", "pipe", "pipe"] });
let diagnostic = "";
let spawnError;
child.on("error", (error) => { spawnError = error; });
child.stdout.on("data", () => {});
child.stderr.on("data", (bytes) => { if (diagnostic.length < 20000) diagnostic += bytes.toString(); });
const tail = (text, limit = 6000) => (text.length > limit ? `...${text.slice(-limit)}` : text);

// On failure the host's own stderr says little, so look at what the bundled runtime does by itself: its
// linked libraries, and the backend started directly with the packaged interpreter and the same stripped
// environment the desktop gives it.
async function diagnose() {
  const report = {};
  const attempt = async (name, fn) => { try { report[name] = await fn(); } catch (error) { report[name] = `unavailable: ${error}`; } };
  await attempt("bookFiles", async () => (await readdir(path.join(profile, "book"), { recursive: true })).slice(0, 80));
  await attempt("bookLogs", async () => {
    const logs = {};
    for (const entry of await readdir(path.join(profile, "book"), { recursive: true })) {
      if (entry.endsWith(".log")) logs[entry] = tail(await readFile(path.join(profile, "book", entry), "utf8"));
    }
    return logs;
  });
  let root;
  await attempt("runtimeRoot", async () => {
    if (appImage) {
      const full = path.join(profile, "full");
      await mkdir(full);
      execFileSync(executable, ["--appimage-extract"], { cwd: full, env: { ...process.env, APPIMAGE_EXTRACT_AND_RUN: "1" }, stdio: "ignore", timeout: 180000 });
      root = path.join(full, "squashfs-root", "usr/lib/open-net-worth/resources/runtime");
    } else root = path.join(path.dirname(await realpath(executable)), "resources/runtime");
    return root;
  });
  if (!root) return report;
  await attempt("manifest", async () => JSON.parse(await readFile(path.join(root, "manifest.json"), "utf8")));
  const python = await realpath(path.join(root, "python/bin/python3")).catch(() => null);
  report.python = python;
  if (!python) return report;
  await attempt("pythonMode", async () => (await stat(python)).mode.toString(8));
  await attempt("ldd", () => execFileSync("ldd", [python], { encoding: "utf8", timeout: 30000 }));
  await attempt("backend", () => new Promise((resolve) => {
    const book = path.join(profile, "diagnostic-book");
    const bootstrap = { token: randomBytes(24).toString("hex"), instance_id: randomBytes(12).toString("hex"), root: book, qa_mode: true };
    const backend = spawn(python, ["-m", "backend.cli.desktop"], {
      cwd: root,
      env: { HOME: process.env.HOME ?? "", PATH: env.PATH, TMPDIR: process.env.TMPDIR ?? "/tmp", PYTHONNOUSERSITE: "1", PYTHONUNBUFFERED: "1", PYTHONUTF8: "1" },
      stdio: ["pipe", "pipe", "pipe"],
    });
    let out = "";
    let err = "";
    const finish = (outcome) => { clearTimeout(timer); backend.kill("SIGKILL"); resolve({ outcome, exitCode: backend.exitCode, signal: backend.signalCode, stdout: tail(out), stderr: tail(err) }); };
    const timer = setTimeout(() => finish("no ready line within 60s"), 60000);
    backend.on("error", (error) => { err += String(error); finish("spawn failed"); });
    backend.on("exit", () => finish("exited"));
    backend.stdout.on("data", (bytes) => { out += bytes; if (out.includes('"type": "ready"')) finish("ready"); });
    backend.stderr.on("data", (bytes) => { err += bytes; });
    backend.stdin.on("error", () => {});
    mkdir(book).then(() => backend.stdin.write(`${JSON.stringify(bootstrap)}\n`));
  }));
  return report;
}
async function poll(fn, timeout = 90000, what = "Packaged application did not become ready") {
  const until = Date.now() + timeout;
  while (Date.now() < until) {
    if (await fn()) return;
    await new Promise((resolve) => setTimeout(resolve, 250));
  }
  throw new Error(what);
}
const alive = (pid) => { try { process.kill(pid, 0); return true; } catch { return false; } };
let owner;
let origin;
const exited = () => child.exitCode !== null || child.signalCode !== null;
try {
  await poll(async () => {
    if (spawnError) throw spawnError;
    if (exited()) throw new Error(`Packaged host exited (${child.exitCode}): ${diagnostic}`);
    try {
      owner = JSON.parse(await readFile(path.join(profile, "book/.instance.lock"), "utf8"));
      origin = `http://127.0.0.1:${owner.port}`;
      return (await fetch(`${origin}/api/health`)).ok;
    } catch {
      return false;
    }
  });
  const command = execFileSync("ps", ["-p", String(owner.pid), "-o", "command="], { encoding: "utf8" });
  assert.ok(command.includes("/runtime/python/"), command);
  assert.ok(command.includes("backend.cli.desktop"), command);
  assert.equal((await fetch(`${origin}/api/accounts`)).status, 200);
  assert.equal((await fetch(`${origin}/`)).status, 200);
  assert.equal((await fetch(`${origin}/api/desktop/status`)).status, 403);
  assert.equal((await fetch(`${origin}/api/accounts`, { headers: { Origin: "https://evil.test" } })).status, 403);
  assert.equal((await fetch(`${origin}/api/accounts`, { headers: { "X-ONW-Desktop-Session": "wrong" } })).status, 401);
  assert.equal(await stat(forbidden).then(() => true, () => false), false);
  const canonical = await realpath(profile);
  assert.ok((await stat(path.join(profile, "book/data/onw.db"))).size > 0);
  // An AppImage's launcher process is not the Electron host, so quit the host (the backend's parent) itself.
  const host = Number(execFileSync("ps", ["-p", String(owner.pid), "-o", "ppid="], { encoding: "utf8" }));
  assert.ok(host > 1, `backend parent ${host}`);
  process.kill(host, "SIGTERM");
  await poll(() => !alive(host), 30000, `The desktop host (${host}) did not quit after SIGTERM`);
  await poll(exited, 30000, "The launched process did not exit after the host quit");
  await poll(() => !alive(owner.pid), 15000, "The backend outlived the desktop host");
  await writeFile("qa/artifacts/packaged-linux-report.json", JSON.stringify({ executable, appImage, profile: canonical, sandboxNote: diagnostic.includes("without the Chromium sandbox") ? "launcher dropped the sandbox (user namespaces blocked)" : "sandbox active", exitCode: child.exitCode }, null, 2));
  console.log("PACKAGED_SMOKE_OK", canonical);
} catch (error) {
  const findings = await diagnose().catch((failure) => ({ diagnosticsFailed: String(failure) }));
  await writeFile("qa/artifacts/packaged-linux-failure.json", JSON.stringify({ executable, profile, error: String(error), diagnostic, findings }, null, 2));
  console.error(JSON.stringify(findings, null, 2));
  throw new Error(`Packaged smoke failed (${profile}): ${error}\n${diagnostic}`, { cause: error });
} finally {
  if (!exited()) {
    child.kill("SIGTERM");
    try { await poll(exited, 5000); } catch { child.kill("SIGKILL"); await poll(exited, 5000); }
  }
  if (owner) { try { process.kill(owner.pid, "SIGTERM"); } catch {} }
}
