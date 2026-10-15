"""REST API for the community-extensions platform (P4-EXT phase-1).

Thin routes over ``services.extensions.*`` — no parsing/validation logic here.

    GET    /api/extensions              list installed + shipped packs
    POST   /api/extensions/inspect      dry-run: validate + return permission review (no write)
    POST   /api/extensions/install      validate + install a zip bundle (after operator confirms)
    DELETE /api/extensions/{type}/{id}  uninstall a user-installed pack (whole id; all versions)
    DELETE /api/extensions/{type}/{id}/{version}  uninstall one specific version

All endpoints require auth (app-wide ``require_auth`` dependency).

INSTALL PROTOCOL (mirrors statement-import confirm flow):
  1. Operator uploads a ``.zip`` bundle or provides a ``source_url``.
  2. Router calls ``inspect_extension`` → returns ``{ok, permission_review, errors}``.
  3. Operator reviews the permission payload and confirms.
  4. Operator POST ``/install`` with the SAME source → ``install_extension`` writes to disk.

For the one-shot ``confirm=true`` form the router fetches + installs in a single call.
This is an intentional shortcut only for scripted/API usage; the UI MUST use the
two-step inspect-then-install flow.

SECURITY-CRITICAL — /security-review required before merge.
"""

from __future__ import annotations

import logging
from typing import Optional
from urllib.parse import urljoin

import httpx
from fastapi import APIRouter, File, Form, HTTPException, UploadFile
from pydantic import BaseModel

from backend.core.net_guard import UnsafeUrlError, assert_public_url
from backend.services.extensions.install import (
    inspect_extension,
    install_extension,
    uninstall_extension,
)
from backend.services.extensions.registry import (
    is_pack_enabled,
    list_installed,
    set_pack_enabled,
)

# 20 MB cap — reasonable for a data-pack zip (the installer enforces its own
# 100 MB total-uncompressed cap after extraction).
_FETCH_MAX_BYTES = 20 * 1024 * 1024
_FETCH_TIMEOUT = 30.0
# Cap on the redirect chain (each hop is SSRF-re-validated before following).
_MAX_FETCH_REDIRECTS = 5

logger = logging.getLogger(__name__)

router = APIRouter()

# ── List ──────────────────────────────────────────────────────────────────────


@router.get("/api/extensions")
def list_extensions(type: Optional[str] = None):
    """List all installed (user + shipped) extension packs.

    Returns a list of ``{id, name, version, type, engine, source, verified,
    domain, author, description}``.
    ``source`` is ``"user"`` or ``"shipped"``.
    ``pack_dir`` and ``manifest`` are internal fields and are NOT exposed.
    """
    packs = list_installed(type_filter=type)
    result = []
    for p in packs:
        m = p.get("manifest") or {}
        result.append(
            {
                "id": p.get("id"),
                "name": p.get("name"),
                "version": p.get("version"),
                "type": p.get("type"),
                "engine": p.get("engine"),
                "source": p.get("source"),
                "verified": p.get("verified"),
                "domain": m.get("domain") if isinstance(m, dict) else getattr(m, "domain", None),
                "author": m.get("author") if isinstance(m, dict) else getattr(m, "author", None),
                "description": m.get("description")
                if isinstance(m, dict)
                else getattr(m, "description", None),
                "enabled": is_pack_enabled(str(p.get("type") or ""), str(p.get("id") or "")),
            }
        )
    return result


class ExtensionEnabledBody(BaseModel):
    enabled: bool


@router.patch("/api/extensions/{type}/{id}")
def set_extension_enabled(type: str, id: str, body: ExtensionEnabledBody):
    """Switch one pack on or off without uninstalling it.

    Disabling is not cosmetic: ``registry.get_data_pack`` refuses to resolve a disabled
    pack, so the extension stops being consulted on the very next lookup.
    """
    known = {(p.get("type"), p.get("id")) for p in list_installed()}
    if (type, id) not in known:
        raise HTTPException(status_code=404, detail="unknown extension")
    set_pack_enabled(type, id, body.enabled)
    return {"ok": True, "id": id, "type": type, "enabled": body.enabled}


# ── Inspect (dry-run) ─────────────────────────────────────────────────────────


@router.post("/api/extensions/inspect")
async def inspect_ext(
    source_url: Optional[str] = Form(default=None),
    file: Optional[UploadFile] = File(default=None),
):
    """Dry-run validate a zip bundle and return the permission review payload.

    Does NOT write anything to disk. Returns:
    ``{ok, manifest_summary, permission_review, errors, warnings}``
    """
    zip_bytes = await _resolve_bytes(source_url, file, endpoint="inspect")
    result = inspect_extension(zip_bytes)
    # Remove internal staging dir from result (never in response).
    result.pop("_staging", None)
    return result


# ── Install ───────────────────────────────────────────────────────────────────


@router.post("/api/extensions/install")
async def install_ext(
    source_url: Optional[str] = Form(default=None),
    file: Optional[UploadFile] = File(default=None),
):
    """Validate + install a zip bundle.

    Returns ``{ok, installed, id, name, version, type, permission_review, errors, warnings}``.

    The caller MUST have already shown ``permission_review`` to the operator and
    received explicit confirmation before calling this endpoint. The UI must use
    the two-step inspect→install flow. For API/scripted use the inspect call is
    skipped at the caller's risk.

    On success ``installed=true`` and the pack is live immediately (the registry
    picks it up on the next ``get_data_pack`` / ``list_installed`` call).
    """
    zip_bytes = await _resolve_bytes(source_url, file, endpoint="install")
    result = install_extension(zip_bytes)
    result.pop("_staging", None)
    if not result["ok"]:
        raise HTTPException(status_code=422, detail=result)
    return result


# ── Uninstall ─────────────────────────────────────────────────────────────────


@router.delete("/api/extensions/{ext_type}/{ext_id}")
def uninstall_ext(ext_type: str, ext_id: str):
    """Uninstall a user-installed extension (all versions).

    Shipped packs cannot be uninstalled via this endpoint (they are in-repo).
    Returns ``{ok, removed_path, errors}``.
    """
    result = uninstall_extension(ext_type, ext_id)
    if not result["ok"]:
        raise HTTPException(status_code=404, detail=result["errors"])
    return result


@router.delete("/api/extensions/{ext_type}/{ext_id}/{version}")
def uninstall_ext_version(ext_type: str, ext_id: str, version: str):
    """Uninstall one specific version of a user-installed extension.

    Returns ``{ok, removed_path, errors}``.
    """
    result = uninstall_extension(ext_type, ext_id, version=version)
    if not result["ok"]:
        raise HTTPException(status_code=404, detail=result["errors"])
    return result


# ── Helpers ───────────────────────────────────────────────────────────────────


async def _resolve_bytes(
    source_url: Optional[str],
    file: Optional[UploadFile],
    *,
    endpoint: str,
) -> bytes:
    """Return the zip bytes from either an uploaded file or a URL.

    Raises 422 when neither source is provided.

    FIX 2 — SSRF per-hop redirect re-validation: redirects are followed
    MANUALLY (``follow_redirects=False``).  EVERY ``Location`` header is
    passed through ``assert_public_url`` BEFORE the next request is issued.
    A 302 → 169.254.169.254 / localhost / private-LAN is therefore refused on
    the redirect hop, not only on the initial URL.  The redirect chain is
    capped at ``_MAX_FETCH_REDIRECTS``.

    FIX 2 (streaming body cap): the response body is consumed via
    ``httpx.stream()`` and aborted once ``_FETCH_MAX_BYTES`` is exceeded —
    the server never buffers more than the cap regardless of what the
    ``Content-Length`` header claims.
    """
    if file is not None:
        return await file.read()
    if source_url:
        try:
            safe_url = assert_public_url(source_url)
        except UnsafeUrlError as exc:
            raise HTTPException(status_code=422, detail=f"Unsafe URL: {exc}") from exc
        try:
            content = _fetch_url_bytes_ssrf_safe(safe_url)
        except UnsafeUrlError as exc:
            raise HTTPException(status_code=422, detail=f"Unsafe redirect: {exc}") from exc
        except httpx.HTTPStatusError as exc:
            raise HTTPException(
                status_code=422,
                detail=f"Fetch failed: HTTP {exc.response.status_code}",
            ) from exc
        except httpx.HTTPError as exc:
            raise HTTPException(status_code=422, detail=f"Fetch error: {exc}") from exc
        except ValueError as exc:
            raise HTTPException(status_code=413, detail=str(exc)) from exc
        return content

    raise HTTPException(
        status_code=422,
        detail=f"/extensions/{endpoint} requires either a file upload or source_url",
    )


def _fetch_url_bytes_ssrf_safe(url: str) -> bytes:
    """Fetch ``url`` synchronously with per-hop SSRF re-validation and a streaming body cap.

    Each redirect ``Location`` is validated by ``assert_public_url`` before the
    next request is issued (mirrors ``plain_get_fetch`` in ``ai/search/base.py``).
    The response body is consumed in chunks and aborted once ``_FETCH_MAX_BYTES``
    is exceeded.

    Raises:
        UnsafeUrlError — any hop resolves to a private / internal address.
        httpx.HTTPStatusError — non-2xx final response.
        ValueError — response body exceeds ``_FETCH_MAX_BYTES``.
    """
    headers = {"User-Agent": "OpenNetWorth-extensions/1.0"}
    safe_url = url
    with httpx.Client(timeout=_FETCH_TIMEOUT, follow_redirects=False) as client:
        for _ in range(_MAX_FETCH_REDIRECTS + 1):
            with client.stream("GET", safe_url, headers=headers) as resp:
                if resp.is_redirect:
                    location = resp.headers.get("location")
                    if not location:
                        # 3xx with no Location — treat as final response.
                        resp.read()
                        resp.raise_for_status()
                        return resp.content
                    next_url = urljoin(safe_url, location)
                    # Re-validate EVERY redirect hop before following it.
                    safe_url = assert_public_url(next_url)
                    continue
                resp.raise_for_status()
                # Stream-read with hard byte cap.
                chunks: list[bytes] = []
                total = 0
                for chunk in resp.iter_bytes():
                    total += len(chunk)
                    if total > _FETCH_MAX_BYTES:
                        raise ValueError(
                            f"Extension zip from URL is too large (>{_FETCH_MAX_BYTES} bytes)"
                        )
                    chunks.append(chunk)
                return b"".join(chunks)
        raise httpx.HTTPError("Too many redirects")
