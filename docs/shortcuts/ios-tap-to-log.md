# iOS Shortcuts — maintainer notes

**Using them?** Read [Log a spend from your phone](../product/features/phone-shortcuts.md) (in the
app at `/help/features/phone-shortcuts`). It covers iPhone, Android, Telegram, the
`POST /api/capture` reference and troubleshooting. This page is only about how the files are
built and shipped.

## Why every shortcut is one signed, generic file

Since iOS 15 the Shortcuts app refuses to import an **unsigned** `.shortcut` file ("Importing
unsigned shortcut files is not supported"), and the old "Allow Untrusted Shortcuts" switch no
longer exists. Signing needs Apple's `shortcuts sign` on a Mac signed in to iCloud. A signed file is
sealed, so the server can't insert an instance's address and token at download time.

So:

- Each shortcut is built **once**, with placeholders, by `backend/services/shortcuts/catalog.py`:
  **Log Apple Pay** (Wallet tap, run by an automation) and **Log Finance Notifications** (iOS 27: its
  `WFNotificationTrigger` is stored in the file's `WFWorkflowTriggers`, apps left for the owner to
  pick, so there are no input types). Each trigger is its own `source` — `ios-wallet` and
  `ios-notification` — so the match engine compares a tap with the bank's notification of the
  same purchase instead of vetoing two refs under one namespace (live 2026-10-15). `ref` is
  `wallet-tap:<ISO>` or `notif:<the notification's own ISO time>@<app>`; the app is the
  emitter (`core.txn_sources.ref_emitter`). Older shortcuts still send `ios-shortcut`.
- The instance-specific values travel as a **setup code** (`<address>|<API token>`,
  `services/shortcuts/setup_code.py`). The capture shortcut's single import question asks for it
  and a Split Text action separates the two.
- Per-download signing was rejected: it only works on macOS hosts, and `--mode anyone` sends the
  file to Apple. A file with the token baked in would hand the token to Apple.

## Files

| Path | What |
|---|---|
| `docs/shortcuts/dist/*.shortcut` | Unsigned placeholder builds: readable and diffable, **not installable**. `make ios-shortcuts`. |
| `backend/services/shortcuts/signed/*.shortcut` | The signed copies the app serves, plus `manifest.json`, which pins each one's source and signed bytes. `make ios-shortcuts-sign`. |

`manifest.json` pins two hashes per shortcut: the unsigned source it was signed from, and the
signed bytes. `services/shortcuts/signed.py` serves a file only while both match. A stale
template or a swapped file is treated as missing and never served. This matters because every
user pastes their API token into this shortcut, and `--mode anyone` lets any Apple ID sign a
look-alike. **Review every change under `signed/` as a security change.** A diff that moves both
a signed file and its manifest hash should come only from a maintainer's own
`make ios-shortcuts-sign` run. When you test-install, check that iOS shows the expected
signer's name.

`tests/test_shortcuts_signed.py` fails CI until the signed files are current.

## Releasing a template change

1. Change `catalog.py`. `tests/test_ios_shortcuts.py` pins the structure and the
   `CaptureRequest` payload.
2. On a Mac signed in to iCloud: `make ios-shortcuts-sign`. Installers see the signer's Apple ID
   name.
3. Commit `docs/shortcuts/dist/` and `backend/services/shortcuts/signed/` together.
4. Test on a real iPhone: install from the setup page, paste the setup code, compare the actions
   with the template, and make one real Apple Pay tap. Running it by hand sends an empty sentence,
   because there's no transaction to read. CI cannot import a shortcut.

## How phone setup reaches the phone

`POST /api/shortcuts/prepare` mints a 10-minute in-memory ticket (`services/shortcuts/tickets.py`)
and returns a QR for `/phone-setup?t=…`, an app page that `main.tsx` renders before the sign-in
gate (`components/phoneSetup/`), with the app's theme and UI kit. It reads
`GET /api/shortcuts/setup?t=` (auth-exempt; the ticket is the credential) for the setup code and the
install links.
`GET /api/shortcuts/file?t=&slug=` serves the signed file. A ticket is bound to the API token it
was minted under (any rotation kills it) and, on first view, to that device by an HttpOnly cookie.
A forwarded link or a photographed QR stops showing the code after first use. Changing or removing
the password revokes every open ticket too.

Inside the app, the setup code and token are never rendered as text, because the Lucius screen
snapshot reads visible text. They only go to the clipboard.

Design rationale for the capture endpoint itself:
[`docs/prd/tap-to-log-transactions.md`](../prd/tap-to-log-transactions.md) ·
[`docs/adr/2026-07-28-capture-endpoint.md`](../adr/2026-07-28-capture-endpoint.md)
