# Open Net Worth

**An open-source agent harness for personal finance.** A real multi-currency ledger, budgets, and
investments — plus an AI that can actually read and change them, not just talk about them.
Self-hosted, on a machine you control.

Most personal-finance apps that added AI let it *describe* your data. Open Net Worth is built the
other way round: the ledger, the budget, and the investment book are the substrate, and the
assistant (Lucius) is an agent operating them through a registry of real tools — the same guarded
write paths the UI uses.

|  | Chatbot bolted onto an app | An agent harness |
|---|---|---|
| What the AI touches | a summary of your data | the ledger itself, through real write tools |
| When it's wrong | you find out later | a confirm card shows the change before it lands |
| What it can do | answer | act, on a schedule, unattended if you allow it |
| Extending it | wait for the vendor | connect an MCP server and it gains tools |

Every tool carries a permission level — `always_allow`, `needs_approval` (the default), or
`blocked` — enforced at dispatch for every tool in the app.

Nothing leaves your machine except the calls you explicitly configure (an AI provider, a price
feed, an optional connector). There is no Open Net Worth cloud service, no account, no telemetry.
The one exception is a problem report you choose to send: you read it first, and it goes only when
you press Send.

## Is this for you?

**Yes, if** you want to self-host your own finances, you're comfortable running a small app on
your own machine or a server you control, and you'd rather have an AI assistant *do* bookkeeping
for you (categorize a transaction, reconcile an account, explain a swing in net worth) than just
chat about screenshots of it.

**Not yet, if** you need a polished, install-and-forget consumer product, multi-user or family
accounts, or mobile apps.

## Requirements

- **The desktop app:** macOS on Apple Silicon, Windows 10/11 (x64), or Linux (x86_64, AppImage
  or `.deb`). It bundles its own Python, so nothing else needs installing. Intel Macs and arm64
  Linux have no desktop build yet; what differs on Windows is listed in
  [`docs/WINDOWS.md`](docs/WINDOWS.md).
- **Running a server from source:** Python 3.12+ and Node.js 22+ (macOS, Linux or Windows).
- **An AI model** to use Lucius: an API key (Anthropic, OpenAI, or any OpenAI-compatible
  endpoint, including a local one), or an existing Claude Code, Codex or Antigravity CLI sign-in
  on the same machine, which first run detects for you. The rest of the app — ledger, budget,
  investments — works without one.

## Getting started

### Option A — the desktop app (recommended)

Download it from [opennetworth.ai](https://opennetworth.ai), or from the public
[releases page](https://github.com/open-net-worth-ai/open-net-worth-releases/releases/latest):

| Platform | Download | Terminal install |
|---|---|---|
| macOS (Apple Silicon; signed and notarized DMG) | [opennetworth.ai/download/mac](https://opennetworth.ai/download/mac) | `curl -fsSL https://opennetworth.ai/install.sh \| sh` |
| Windows 10/11 x64 (installer) | [opennetworth.ai/download/windows](https://opennetworth.ai/download/windows) | `irm https://opennetworth.ai/install.ps1 \| iex` (PowerShell) |
| Linux x86_64 (AppImage, or `.deb` from the releases page) | [opennetworth.ai/download/linux](https://opennetworth.ai/download/linux) | `curl -fsSL https://opennetworth.ai/install.sh \| sh` |

The Windows installer is **not code-signed yet**, so SmartScreen may show a warning: choose
**More info → Run anyway**. `install.ps1` verifies the download against the release's
`SHA256SUMS`; add `-RequireSigned` to refuse an unsigned installer. `install.sh` verifies the same
checksum before it installs anything. The macOS and Windows apps update themselves in place;
a Linux AppImage replaces itself after checking its SHA-256, and a `.deb` points you to the
releases page.

Then follow the first-run screen: it asks where your book lives (a local folder, or an existing
server), creates your login, picks a base currency, and hands off to Lucius to add your first
account. Your data defaults to `~/.open-net-worth/`, outside the app, so updating or reinstalling
never touches it. See the
[desktop guide](docs/product/operations/desktop.md) for background mode, start at login, watched
folders and keyboard shortcuts.

### Option B — a server

```bash
curl -fsSL https://github.com/open-net-worth-ai/open-net-worth-releases/releases/latest/download/install-server.sh | sh
onw start --open
```

Installs [uv](https://docs.astral.sh/uv/) if needed, then `open-net-worth` on its own Python 3.12
(no system Python or Node.js). It provides the `onw` command (`onw start --open`,
`onw autostart install`, `onw upgrade`, `onw reset-password`, `onw mcp`, `onw version`). The desktop app can connect to a server
like this instead of using a local book. Working on the code instead? `make setup start` from a
checkout (Python 3.12+ and Node.js 22+).

No `.env` file is needed. Everything lives in one working folder, `~/.open-net-worth/`, holding
`data/`, `cache/`, `logs/` and `backups/`. First run lets you put it elsewhere and remembers the
choice.

### Option C — Docker

```bash
curl -fsSLO https://github.com/open-net-worth-ai/open-net-worth-releases/releases/latest/download/docker-compose.yml
AUTH_PASSWORD='pick a password' docker compose up -d
```

Pulls the public image `ghcr.io/open-net-worth-ai/open-net-worth`, pinned by that compose file to
the release it came with (`ONW_VERSION=x.y.z` runs another). From a checkout, add
`-f compose.build.yml --build` to build it locally instead. Then open http://localhost:8080 and log in with
that password. The password is not optional under Docker: your browser reaches the container from
Docker's bridge, not from localhost, so the "same machine, no login" shortcut never applies, and
without a password nothing can operate the instance — details in the
[first-run guide](docs/product/getting-started/first-run.md#docker). Compose refuses to start
without it. Migrations run at boot; data lives in a named volume that survives
`docker compose down`.

### Try it with demo data first

```bash
make seed
```

Generates a fabricated book — two and a half years of accounts, transactions, holdings and a
budget. It refuses to run against a book that already has data, so it cannot overwrite anything.

## Keeping it running

The desktop app does this itself: **Settings → Desktop** has *Keep running in the background* and
*Start at login*. For a server, Open Net Worth is one Python process (`onw start`, which runs uvicorn and serves both the API and
the built frontend on the same port) — there is no separate web server (no Apache, no nginx) in
front of it, and none is required.

- **Docker** already restarts itself: `docker-compose.yml`
  sets `restart: unless-stopped`, which covers a crash and a host reboot, as long as the Docker
  daemon itself starts on boot (the default on Docker Desktop and most Linux installs).
- **Not using Docker?** One command installs it as a boot service, so it starts at login/boot and
  restarts on crash, with no terminal window to keep open:

  ```bash
  onw autostart install
  ```

  (From a source checkout, `make autostart` runs setup first, then the same thing.) This
  installs a per-user **launchd** LaunchAgent on macOS, or a per-user **systemd** unit (plus
  `loginctl enable-linger`, so it starts even before you log in) on Linux — no sudo needed on
  either. `onw autostart status` shows whether it's running; `onw autostart stop` stops it and
  removes the service. Logs land in `~/.open-net-worth/logs/autostart.{out,err}.log`.

  <details>
  <summary>What that installs, if you'd rather see it or do it by hand</summary>

  The service runs the same interpreter `onw` runs on, so it is identical for a uv install and a
  checkout's `.venv`; it also carries your shell's `PATH` (launchd and systemd give a fresh
  process a minimal one) and any of `HOST`, `PORT`, `ONW_ROOT`, `DATA_DIR`, `CACHE_DIR`,
  `LOGS_DIR` that were set when you installed it.

  macOS (`~/Library/LaunchAgents/ai.opennetworth.app.plist`):

  ```xml
  <?xml version="1.0" encoding="UTF-8"?>
  <!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
  <plist version="1.0"><dict>
    <key>Label</key><string>ai.opennetworth.app</string>
    <key>ProgramArguments</key>
    <array>
      <string>/path/to/python3</string>
      <string>-m</string><string>backend.cli</string><string>start</string>
    </array>
    <key>WorkingDirectory</key><string>/Users/you/.open-net-worth</string>
    <key>RunAtLoad</key><true/>
    <key>KeepAlive</key><true/>
  </dict></plist>
  ```

  Linux (`~/.config/systemd/user/open-net-worth.service`):

  ```ini
  [Unit]
  Description=Open Net Worth
  After=network.target

  [Service]
  WorkingDirectory=/home/you/.open-net-worth
  ExecStart=/path/to/python3 -m backend.cli start
  Restart=on-failure

  [Install]
  WantedBy=default.target
  ```

  See `backend/cli/autostart.py` for the exact commands (`launchctl bootstrap` /
  `systemctl --user enable --now`).

  </details>

- **Updating.** The desktop app updates itself (see the
  [desktop guide](docs/product/operations/desktop.md#updates)); re-running its installer also
  works. A server: `onw upgrade` for a uv install; for Docker, download the new release's
  `docker-compose.yml` (it pins the image version) or set `ONW_VERSION=x.y.z`, then
  `docker compose up -d` (a checkout: `git pull && make setup`). Your data is outside all of them, and migrations run at the next start.

## Configuration

Everything needed to *run* the app is an optional environment variable — see `.env.example` for
the full list (working folder, host/port, CORS).

Everything needed to make it *useful* is configured **inside the running app** and stored
there: the AI provider and model under **Settings**, and banks, brokers, email, Telegram, web
search and price sources under **Connectors**. You never hand-edit a config file for those.

Set `ONW_ROOT` only to pin the working folder from outside the app: a container, or a second
instance on the same machine (which also needs its own `PORT`).

## Your data

One working folder (default `~/.open-net-worth/`): a single SQLite database plus a small set of
file-backed stores, alongside `cache/`, `logs/` and `backups/`. It never leaves your machine
except for calls you configured yourself.

Settings → Data gives you a one-click full backup `.zip`, automatic daily backups, and
restore-from-backup — a checkpoint is saved first, so a restore is itself undoable.

## Running it beyond localhost

Open Net Worth is built for **one operator on a machine you control**. Treat it like a
self-hosted password manager, not a public web app.

- **The local machine is trusted by default.** With `AUTH_TRUST_LOOPBACK=true` (the default), any
  process on the same machine can call the API without a password — so the person sitting at the
  machine never has to log in. Set it to `false` on a shared machine.
- **A password only gates non-localhost requests.** It is not, by itself, what stands between
  your data and the network.
- **Behind a reverse proxy, loopback trust stops working as intended.** A same-host proxy
  (nginx, Caddy, a tunnel) makes every remote request arrive from `127.0.0.1`, so it is trusted.
  Set `AUTH_TRUST_LOOPBACK=false` before putting this behind one.
- **Before exposing it anywhere** — a LAN, a tunnel, a public domain — set a strong password
  (Settings → Security), turn off loopback trust, and put it behind HTTPS. The app does not
  terminate TLS itself.

Found a vulnerability? See [`SECURITY.md`](SECURITY.md) — please do not open a public issue.

## Documentation

- **In-app help** — once running, the `/help` tab has the full docs, searchable, without leaving
  the app.
- [`docs/DATA_MODEL.md`](docs/DATA_MODEL.md) — stores, fields, and how the money math works.
- [`CLAUDE.md`](CLAUDE.md) — the codebase map, if you are reading code rather than using the app.
- [`CONTRIBUTING.md`](CONTRIBUTING.md) — dev setup, the dependency rule, test policy.

## Stack

- **Backend** — Python 3.12, FastAPI, SQLAlchemy + Alembic (SQLite by default, Postgres supported)
- **Frontend** — React 19, TypeScript, Vite
- **AI** — Anthropic Claude by default, or any OpenAI-compatible endpoint; your key
- **Prices** — Yahoo Finance, CoinGecko

## License

AGPL-3.0 — see [`LICENSE`](LICENSE). Third-party dependency licenses are in [`NOTICE`](NOTICE).
