"""Phone shortcuts: the signed iOS Shortcuts and the setup that configures them.

Routes, two trust levels:

* ``GET  /api/shortcuts``             — what is on offer (authenticated).
* ``GET  /api/shortcuts/setup-code``  — this instance's setup code (authenticated).
* ``POST /api/shortcuts/prepare``     — mint a short-lived phone-setup ticket + QR
  (authenticated; this is the call that decides which address the phone gets).
* ``GET  /api/shortcuts/setup``       — what the phone setup page (``/phone-setup``) shows.
* ``GET  /api/shortcuts/file/<slug>.shortcut`` — a signed ``.shortcut`` for a ticket
  (``/api/shortcuts/file?slug=`` is the older form, still served).

The last two are **exempt from ``require_auth``** because the device fetching them is a
phone with no session; the ticket is its credential. See ``services.shortcuts.tickets``.
"""

import logging
from typing import Literal, Optional

from fastapi import APIRouter, Cookie, HTTPException, Request, Response
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from backend.db.auth_store import AuthStore
from backend.services.shortcuts import phone_address, qr, setup_code, signed, tickets
from backend.services.shortcuts.catalog import BY_SLUG, SPECS

_log = logging.getLogger(__name__)

router = APIRouter()

_PAGE_PATH = "/phone-setup"  # the app page the QR opens (frontend PhoneSetupPage)
_SETUP_PATH = "/api/shortcuts/setup"
_FILE_PATH = "/api/shortcuts/file"
_NO_STORE = {"Cache-Control": "no-store, private"}
_DEVICE_COOKIE = "onw_shortcut_setup"


def detect_base_url(request: Request) -> str:
    """The address the CLIENT used to reach us — not what the server thinks it is.

    A self-hosted instance is routinely reached through a tunnel (Tailscale,
    cloudflared, ngrok), so ``localhost:8080`` is almost never the right answer to
    hand a phone. The browser loading Settings reached us on some URL; that same URL
    is the one the phone should use.

    ``X-Forwarded-Proto``/``X-Forwarded-Host`` are read because tunnels terminate
    TLS and set them. They are caller-controllable, which is fine here and only
    here: the value is echoed back to the signed-in operator, shown to them in the
    UI, and editable before anything is generated. It is never trusted for an
    authorization decision.
    """

    def _first(header: str) -> str:
        return (request.headers.get(header) or "").split(",")[0].strip()

    scheme = _first("x-forwarded-proto") or request.url.scheme
    host = _first("x-forwarded-host") or _first("host") or request.url.netloc
    return f"{scheme}://{host}".rstrip("/")


def _resolve_base_url(supplied: Optional[str], request: Request) -> str:
    base_url = (supplied or "").strip().rstrip("/") or detect_base_url(request)
    if not base_url.startswith(("http://", "https://")):
        raise HTTPException(400, "base_url must start with http:// or https://")
    if setup_code.SEPARATOR in base_url:
        raise HTTPException(400, f"base_url must not contain {setup_code.SEPARATOR!r}")
    return base_url


@router.get("/api/shortcuts")
def list_shortcuts(request: Request):
    """The catalog, plus the address to hand a phone: the one this request came in on, or —
    when that is this computer's own (``localhost``) — the phone address remembered earlier."""
    return {
        "base_url": phone_address.suggest(detect_base_url(request)),
        "capture_path": "/api/capture",
        "shortcuts": [
            {
                "slug": spec.slug,
                "name": spec.name,
                "summary": spec.summary,
                "asks": spec.asks,
                "ios": spec.ios,
                # False until `make ios-shortcuts-sign` has signed THIS build — iOS would
                # refuse the file, so the UI says so instead of offering a dead install.
                "signed": signed.is_current(spec),
            }
            for spec in SPECS
        ],
    }


@router.get("/api/shortcuts/setup-code")
def get_setup_code(request: Request, base_url: Optional[str] = None):
    """Address + API token as the one string the shortcut's import question asks for."""
    code = setup_code.make(_resolve_base_url(base_url, request), AuthStore().api_token())
    return JSONResponse({"setup_code": code}, headers=_NO_STORE)


class PrepareRequest(BaseModel):
    # Overrides the detected address. The UI shows the detection and lets the
    # operator correct it — behind a reverse proxy that rewrites headers, or when
    # the phone should use a different route than the browser did.
    base_url: Optional[str] = None
    # Which iPhone the page opens for: "27" (triggers inside the shortcut) or "26" (a Wallet
    # automation runs it). Only steers what the page shows first — the ticket is the same.
    ios: Literal["27", "26"] = "27"


@router.post("/api/shortcuts/prepare")
def prepare(body: PrepareRequest, request: Request):
    """Mint a phone-setup ticket and render the QR the phone will scan."""
    base_url = _resolve_base_url(body.base_url, request)
    phone_address.remember(base_url)
    token = tickets.mint(base_url, AuthStore().api_token())
    setup_url = f"{base_url}{_PAGE_PATH}?t={token}&ios={body.ios}"
    return {
        "setup_url": setup_url,
        "qr_data_uri": qr.data_uri(setup_url),
        "expires_in": tickets.TTL_SECONDS,
        "base_url": base_url,
    }


def _redeem(t: str) -> tickets.Ticket:
    """Unauthenticated routes: an absent, expired or rotated-away ticket is a flat 404 —
    nothing here distinguishes "wrong ticket" from "no such thing"."""
    ticket = tickets.redeem(t, AuthStore().api_token())
    if ticket is None:
        raise HTTPException(404, _GONE)
    return ticket


_GONE = (
    "This setup link expired, or was already opened on another device. Make a new one in Settings."
)


@router.get(_SETUP_PATH)
def setup_data(
    request: Request, t: str = "", device: Optional[str] = Cookie(None, alias=_DEVICE_COOKIE)
):
    """What the phone setup page (``/phone-setup?t=``, an app page) shows: the setup code and
    the install links. The page is public; this is what the ticket unlocks."""
    ticket = tickets.redeem(t, AuthStore().api_token())
    bound = tickets.claim(t, device) if ticket is not None else None
    if ticket is None or bound is None:
        raise HTTPException(404, _GONE)
    _log.info("shortcut setup opened: base_url=%s", ticket.base_url)
    response = JSONResponse(
        {
            "setup_code": setup_code.make(ticket.base_url, AuthStore().api_token()),
            "expires_in": tickets.TTL_SECONDS,
            "shortcuts": [
                {
                    "slug": spec.slug,
                    "name": spec.name,
                    "summary": spec.summary,
                    "asks": spec.asks,
                    "ios": spec.ios,
                    "install_url": (
                        f"{file_path(spec.slug)}?t={t}" if signed.is_current(spec) else None
                    ),
                }
                for spec in SPECS
            ],
        },
        headers=_NO_STORE,
    )
    # The first device to open the page owns it: a reload there still works, a photographed
    # QR or a forwarded link opened anywhere else does not see the token.
    response.set_cookie(
        _DEVICE_COOKIE,
        bound,
        max_age=tickets.TTL_SECONDS,
        path=_SETUP_PATH,
        httponly=True,
        samesite="lax",
        secure=request.url.scheme == "https",
    )
    return response


def file_path(slug: str) -> str:
    """Where a shortcut downloads from. The name is in the PATH, the ticket the only query
    parameter: handing a link from the home-screen app to Safari kept the first parameter and
    lost ``&slug=`` (seen on device, 2026-10-13 — the download worked, the page said "no signed
    shortcut for that name")."""
    return f"{_FILE_PATH}/{slug}.shortcut"


@router.get(_FILE_PATH + "/{filename}")
def download_named_file(filename: str, t: str = ""):
    return _signed_file(t, filename.removesuffix(".shortcut"))


@router.get(_FILE_PATH)
def download_file(t: str = "", slug: str = ""):
    return _signed_file(t, slug)


def _signed_file(t: str, slug: str) -> Response:
    """A signed shortcut. It carries no secret — the ticket gates it only so that every
    phone-facing route answers to the same credential."""
    _redeem(t)
    spec = BY_SLUG.get(slug)
    payload = signed.signed_bytes(spec) if spec is not None else None
    if spec is None or payload is None:
        _log.warning("shortcut file refused: slug=%r signed=%s", slug[:64], payload is not None)
        raise HTTPException(404, "no signed shortcut for that name")
    return Response(
        content=payload,
        media_type="application/octet-stream",
        headers={
            # RFC 5987 filename* so the space-bearing name survives; the plain
            # filename is the fallback for anything that ignores it.
            "Content-Disposition": (
                f'attachment; filename="{spec.slug}.shortcut"; '
                f"filename*=UTF-8''{spec.name.replace(' ', '%20')}.shortcut"
            ),
            **_NO_STORE,
        },
    )
