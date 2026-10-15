import { readFileSync as __readFileSync } from "node:fs";
const APP_VERSION = JSON.parse(__readFileSync(new URL("../package.json", import.meta.url), "utf8")).version; // the fixture app must report the real version: the backend refuses a mismatched one
import assert from "node:assert/strict";
import { spawn, spawnSync } from "node:child_process";
import {
  mkdir,
  mkdtemp,
  readFile,
  realpath,
  symlink,
  writeFile,
} from "node:fs/promises";
import { tmpdir } from "node:os";
import path from "node:path";

export async function fixture() {
  const repo = path.resolve("..");
  const root = await realpath(
    await mkdtemp(path.join(tmpdir(), "onw-server-client-")),
  );
  const home = path.join(root, "Home");
  const profile = path.join(root, "Profile");
  const local = path.join(root, "Local Book");
  const alternate = path.join(root, "Alternate Local Book");
  const python = path.resolve(
    "runtime",
    JSON.parse(await readFile("runtime/manifest.json", "utf8")).python,
  );
  for (const folder of [home, profile, local, "qa/artifacts"])
    await mkdir(folder, { recursive: true });
  const allowed = [
    "USER",
    "PATH",
    "TMPDIR",
    "LANG",
    "LC_ALL",
    "TZ",
    "SystemRoot",
    "DISPLAY",
    "XAUTHORITY",
  ];
  const env = {
    ...Object.fromEntries(
      allowed.flatMap((key) =>
        process.env[key] ? [[key, process.env[key]]] : [],
      ),
    ),
    HOME: home,
    ONW_QA_MODE: "1",
    ONW_DESKTOP_REPO: repo,
    ONW_DESKTOP_PYTHON: python,
  };
  const seed = (book, label, password = false) => {
    const result = spawnSync(
      python,
      [
        "-c",
        `
from backend.db.migrate import ensure_migrated
ensure_migrated()
from backend.services.profile import read_profile, write_profile
write_profile({**read_profile(), 'base_currency':'USD'})
from backend.db.model_config_store import ModelConfigStore
ModelConfigStore().update({'provider':'openai_compatible','model':'client-qa','base_url':'http://127.0.0.1:9/v1'})
from backend.db.onboarding_state_store import OnboardingStateStore
OnboardingStateStore().mark_complete()
from backend.services.accounts import create_account
create_account({'name':${JSON.stringify(label)},'type':'cash','currency':'USD','initial_balance':1234,'initial_balance_date':'2026-10-02'})
from backend.db.auth_store import AuthStore
AuthStore().get()
`,
      ],
      {
        cwd: repo,
        env: {
          ...env,
          ONW_ROOT: book,
          AUTH_PASSWORD: password ? "synthetic-client-password" : "",
          AUTH_TRUST_LOOPBACK: password ? "false" : "true",
        },
        encoding: "utf8",
        timeout: 45000,
      },
    );
    assert.equal(result.status, 0, result.stderr);
  };
  seed(local, "Client QA Local");
  seed(alternate, "Client QA Alternate Local");
  const servers = [8098, 8099].map((port, index) => {
    const book = path.join(root, `Server ${index + 1} Book`);
    seed(book, `Client QA Server ${index + 1}`, true);
    return {
      port,
      book,
      origin: `http://127.0.0.1:${port}`,
      child: null,
      requests: path.join(root, `server-${port}-requests.jsonl`),
    };
  });
  await writeFile(
    path.join(root, "server_entry.py"),
    `
import json, os
from backend.main import app
@app.middleware('http')
async def client_qa_headers(request, call_next):
    with open(os.environ['CLIENT_QA_REQUEST_LOG'], 'a') as output:
        output.write(json.dumps({'path':request.url.path,'desktop_header':bool(request.headers.get('x-onw-desktop-session'))})+'\\n')
    return await call_next(request)
`,
  );
  await writeFile(
    path.join(root, "package.json"),
    JSON.stringify({
      name: "onw-server-client-qa",
      version: APP_VERSION,
      main: "entry.cjs",
    }),
  );
  await symlink(path.resolve("resources"), path.join(root, "resources"));
  await writeFile(
    path.join(root, "entry.cjs"),
    `const {app}=require('electron'); const fs=require('node:fs'); const write=process.stderr.write.bind(process.stderr); process.stderr.write=(data,...args)=>{fs.appendFileSync(${JSON.stringify(path.join(root, "native-stderr.log"))},data); return write(data,...args);}; process.on('uncaughtException',error=>{process.stderr.write(JSON.stringify({event:'desktop.qa_fatal',message:error.message,stack:error.stack})+'\\n');app.exit(1)}); app.setPath('home',${JSON.stringify(home)}); app.setPath('userData',${JSON.stringify(profile)}); require(${JSON.stringify(path.resolve("dist/main/index.js"))});`,
  );
  async function start(server) {
    await assert.rejects(
      fetch(`${server.origin}/api/health`, {
        signal: AbortSignal.timeout(1000),
      }),
      "QA port is already in use",
    );
    server.child = spawn(
      python,
      [
        "-m",
        "uvicorn",
        "server_entry:app",
        "--app-dir",
        root,
        "--host",
        "127.0.0.1",
        "--port",
        String(server.port),
      ],
      {
        cwd: repo,
        env: {
          ...env,
          ONW_ROOT: server.book,
          PORT: String(server.port),
          CLIENT_QA_REQUEST_LOG: server.requests,
        },
        stdio: ["ignore", "ignore", "pipe"],
      },
    );
    server.child.stderr.on("data", () => {});
    await poll(async () => {
      try {
        return (await fetch(`${server.origin}/api/health`)).ok;
      } catch {
        return false;
      }
    }, 45000);
  }
  async function stop(server) {
    if (!server.child || server.child.exitCode !== null) return;
    const child = server.child;
    child.kill("SIGTERM");
    await new Promise((resolve, reject) => {
      const timer = setTimeout(
        () => reject(new Error("Synthetic server did not stop")),
        15000,
      );
      child.once("exit", () => {
        clearTimeout(timer);
        resolve();
      });
    });
    server.child = null;
  }
  return {
    repo,
    root,
    home,
    profile,
    local,
    alternate,
    env,
    servers,
    start,
    stop,
  };
}

export async function poll(fn, timeout = 20000) {
  const until = Date.now() + timeout;
  while (Date.now() < until) {
    try {
      if (await fn()) return;
    } catch {}
    await new Promise((resolve) => setTimeout(resolve, 100));
  }
  throw new Error("Condition did not become true");
}

export async function api(
  page,
  url,
  body,
  method = body === undefined ? "GET" : "POST",
) {
  return page.evaluate(
    async ({ url, body, method }) => {
      const response = await fetch(url, {
        method,
        headers: {
          "X-Requested-With": "XMLHttpRequest",
          ...(body === undefined ? {} : { "Content-Type": "application/json" }),
        },
        ...(body === undefined ? {} : { body: JSON.stringify(body) }),
      });
      if (!response.ok) throw new Error(`${url}: ${response.status}`);
      return response.json();
    },
    { url, body, method },
  );
}
