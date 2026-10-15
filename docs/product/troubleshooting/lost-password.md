---
title: I lost my password
description: There's no email reset. Set a new password from the desktop app's Settings, or run onw reset-password on the machine that runs the server.
category: troubleshooting
order: 60
audience: [user, operator]
status: stable
related: [getting-started/installation, operations/desktop, operations/security-and-privacy, reference/environment-variables]
keywords: [password, login, locked out, forgot password, reset password, onw reset-password, incorrect password, too many attempts]
---

# I lost my password

There's no "forgot password" email — your book isn't tied to an email address or an online
account. Instead there are exactly two ways to set a new password without knowing the old one,
and both work only from the machine that holds your book:

| You use… | Do this |
|---|---|
| The **desktop app**, with your book on this computer | [Reset it in Settings](#in-the-desktop-app) |
| A **server** (`onw start`, `make start`, Docker, a home server) | [Run `onw reset-password`](#on-a-server-onw-reset-password) on that machine |

Both sign every other browser and device out. Your data is untouched.

## In the desktop app

1. Open **Settings → Security**.
2. Under **Password**, type a new password in **New password** and again in **Confirm
   password** (at least 8 characters). The desktop app doesn't ask for the current one.
3. Click **Reset password**.

This works only for a book stored on this computer. If the desktop app is connected to a
server instead, use `onw reset-password` on that server.

## On a server: `onw reset-password`

1. Open a terminal **on the machine that runs Open Net Worth**, signed in as the user that runs
   the app (the one whose home folder holds `.open-net-worth`).
2. Run the command for how you installed it:

   | Install | Command |
   |---|---|
   | `uv tool install` | `onw reset-password` |
   | From a source checkout | `.venv/bin/onw reset-password` (from the repository folder) |
   | Docker | `docker compose exec app python3 -m backend.cli reset-password` (from the folder with `docker-compose.yml`) |

3. Type the new password at `New password:`, then again at `Confirm new password:`. Nothing
   appears as you type.
4. You should see `Password updated for the book in …. Every signed-in session was signed out.`
   The next line reminds you that your API token was not changed — rotate it from
   **Settings → Security** if you think it was exposed.

The server doesn't need restarting — sign in with the new password straight away. It doesn't
matter whether the server is running while you do this.

**Notes**

- If you keep your book somewhere other than the default, set the same `ONW_ROOT` you start the
  server with: `ONW_ROOT=/path/to/folder onw reset-password`.
- For a script, `--password-stdin` reads the password from one line of input instead of
  prompting.
- `AUTH_PASSWORD` is **not** a reset. It only sets a password when none is stored yet, and is
  ignored after that.

### If the command fails

| Message | Fix |
|---|---|
| `No Open Net Worth book found in …. Start the app once to create it, or point this at the right folder with ONW_ROOT.` | You're pointing at the wrong folder. Run it as the right user, or set `ONW_ROOT` to the folder the server uses. |
| `The two passwords do not match.` | Run it again and type the same password twice. |
| `The password must be at least 8 characters.` | Choose a longer password. |
| `No terminal to prompt on. Run this in a terminal, or pass --password-stdin.` | Run it in an interactive terminal, or pipe the password in with `--password-stdin`. |

## Why opening the app on the server isn't enough

A browser on the server machine skips the login screen by default, but changing the password
there still asks for the current one. That's deliberate: otherwise any program on the machine
could take over your password. Use `onw reset-password` instead. Turning the login off with
`AUTH_DISABLED=true` doesn't help either, for the same reason.

There is no remote way back in. If you can't get a terminal on the machine, you can't reset the
password — that's what makes it mean something.

## Other sign-in messages

**"Incorrect password"** — check Caps Lock and your keyboard layout. Spaces count: the password
is exactly what you typed when you set it.

**"Too many attempts. Try again in N minutes."** — after 5 wrong passwords within 15 minutes
from the same place, sign-in is paused for the rest of that window, even for the right password.
Wait it out, then try again.

## Don't want a password at all?

In **Settings → Security**, **Remove password** (it asks for the current one, except in the
desktop app on its own book) lets this computer open the book without one, and signs out every
other device. Outside the desktop app, turn on **Don't ask for the password on this computer** first.
See [Security and privacy](../operations/security-and-privacy.md).

## Related

- [Security and privacy](../operations/security-and-privacy.md)
- [Desktop app](../operations/desktop.md)
- [Environment variables](../reference/environment-variables.md#auth-instance-password)
