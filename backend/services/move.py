"""Move protocol — a resumable, on-disk transfer of a whole book (contract C8).

A book moves between two Open Net Worth instances (a computer and a remote server, either
way) as ONE exported bundle. The bundle can be gigabytes, so it is never held in memory
by the protocol: the sender uploads fixed-size parts, each checked against its own sha256,
into a temp file under ``CACHE_DIR/moves/<move_id>/``; ``commit`` verifies the whole file's
sha256 and imports it. A dropped connection resumes from ``status().received``.

State lives on disk in ``meta.json`` (atomically rewritten), so a restart of the server
loses nothing: an upload resumes, and an import that was running when the process died is
reported ``failed`` rather than ``importing`` forever.

Rules this module enforces:
  * one move at a time per instance (a second sender gets 409; the same owner restarting
    replaces its own unfinished upload);
  * a move belongs to the credential that began it — any other caller gets 404;
  * ``max_size`` is what the disk can hold (the bundle plus its extracted copy, with a
    margin), stated before a byte is sent;
  * every temp file is removed when the move finishes, is aborted, fails to commit past its
    time limit, or expires (24 h).

The import itself is injected (``runner``): the API layer wraps ``portability.apply_import``
in a pre-import checkpoint, which this layer may not import. Secrets travel separately
(``services.move_secrets``) and are never written to the bundle or logged.
"""

from __future__ import annotations

import hashlib
import json
import logging
import math
import re
import secrets
import shutil
import threading
import time
import uuid
from pathlib import Path
from typing import AsyncIterator, Callable, Optional

from backend.core import ratelimit
from backend.core.errors import ConflictError, NotFoundError, OpenNetWorthError, ValidationError
from backend.core.io import atomic_write_text
from backend.core.paths import CACHE_DIR

logger = logging.getLogger(__name__)

PART_SIZE = 8 * 1024 * 1024
DISK_MARGIN = 512 * 1024 * 1024
MOVE_TTL_SECONDS = 24 * 60 * 60
_BEGINS_PER_HOUR = 10
_MOVE_ID = re.compile(r"^mv_[0-9a-f]{32}$")
_HEX64 = re.compile(r"^[0-9a-f]{64}$")

UPLOADING, IMPORTING, DONE, FAILED, FINISHED = (
    "uploading",
    "importing",
    "done",
    "failed",
    "finished",
)
_LIVE = {UPLOADING, IMPORTING}

_EXPORT_NONCE_SECONDS = 300
_MAX_NONCES = 8
_nonces: dict[str, tuple[str, float]] = {}
_lock = threading.RLock()
_running: set[str] = set()
_begin_limiter = ratelimit.SlidingWindowLimiter(_BEGINS_PER_HOUR, 3600)


class MoveTooLarge(ValidationError):
    code = "move_too_large"

    def __init__(self, max_size: int) -> None:
        super().__init__(
            "This book is larger than the free space on the destination.", max_size=max_size
        )
        self.max_size = max_size


class MoveNotEmpty(ConflictError):
    code = "move_not_empty"


def _root() -> Path:
    return CACHE_DIR / "moves"


def _dir(move_id: str) -> Path:
    if not _MOVE_ID.match(move_id):
        raise NotFoundError("unknown move")
    return _root() / move_id


def _bundle(move_id: str) -> Path:
    return _dir(move_id) / "bundle.zip"


def free_space_limit(path: Optional[Path] = None) -> int:
    """The largest bundle the disk can take: it is stored, then extracted, so half the free space."""
    target = path or _root()
    target.mkdir(parents=True, exist_ok=True)
    return max(0, (shutil.disk_usage(target).free - DISK_MARGIN) // 2)


def _read(move_id: str) -> dict:
    with _lock:
        try:
            meta = json.loads((_dir(move_id) / "meta.json").read_text("utf-8"))
        except (FileNotFoundError, ValueError):
            raise NotFoundError("unknown move") from None
        if meta["status"] == IMPORTING and move_id not in _running:
            meta.update(status=FAILED, error="The import was interrupted. Start the move again.")
            _write(meta)
        return meta


def _write(meta: dict) -> None:
    meta["updated_at"] = time.time()
    atomic_write_text(_dir(meta["move_id"]) / "meta.json", json.dumps(meta))


def _discard(move_id: str) -> None:
    shutil.rmtree(_dir(move_id), ignore_errors=True)


def _owned(move_id: str, owner: str) -> dict:
    meta = _read(move_id)
    if meta["owner"] != owner:
        raise NotFoundError("unknown move")
    return meta


def sweep_expired() -> None:
    """Remove moves nobody has touched for a day, whatever state they stopped in."""
    root = _root()
    if not root.is_dir():
        return
    for entry in root.iterdir():
        try:
            meta = json.loads((entry / "meta.json").read_text("utf-8"))
            stale = (
                time.time() - meta["updated_at"] > MOVE_TTL_SECONDS and entry.name not in _running
            )
        except (OSError, ValueError, KeyError):
            stale = True
        if stale:
            shutil.rmtree(entry, ignore_errors=True)


def issue_export_nonce(owner: str) -> str:
    """A one-time, five-minute token that must accompany a secrets export from this caller, so the
    export is a deliberate second step and not a single replayable request."""
    nonce = secrets.token_urlsafe(32)
    now = time.time()
    with _lock:
        for key in [k for k, (_, expires) in _nonces.items() if expires <= now]:
            del _nonces[key]
        while len(_nonces) >= _MAX_NONCES:
            del _nonces[next(iter(_nonces))]
        _nonces[hashlib.sha256(nonce.encode()).hexdigest()] = (owner, now + _EXPORT_NONCE_SECONDS)
    return nonce


def consume_export_nonce(owner: str, nonce: str) -> bool:
    key = hashlib.sha256(nonce.encode()).hexdigest()
    with _lock:
        entry = _nonces.pop(key, None)
    return entry is not None and entry[0] == owner and entry[1] > time.time()


def begin(owner: str, size: object, sha256: object) -> dict:
    if not isinstance(size, int) or isinstance(size, bool) or size < 1:
        raise ValidationError("size must be a positive number of bytes")
    if not isinstance(sha256, str) or not _HEX64.match(sha256.lower()):
        raise ValidationError("sha256 must be a 64-character hex digest")
    allowed, retry = _begin_limiter.check(owner)
    if not allowed:
        raise ConflictError(f"too many moves started; try again in {retry} seconds")
    with _lock:
        sweep_expired()
        for entry in _root().iterdir() if _root().is_dir() else []:
            try:
                live = _read(entry.name)
            except NotFoundError:
                continue
            if live["status"] not in _LIVE:
                continue
            if live["owner"] == owner and live["status"] == UPLOADING:
                _discard(live["move_id"])
            else:
                raise ConflictError("another move is already in progress on this server")
        max_size = free_space_limit()
        if size > max_size:
            raise MoveTooLarge(max_size)
        _begin_limiter.record(owner)
        move_id = f"mv_{uuid.uuid4().hex}"
        _dir(move_id).mkdir(parents=True, mode=0o700)
        _bundle(move_id).touch(mode=0o600)
        _write(
            {
                "move_id": move_id,
                "owner": owner,
                "size": size,
                "sha256": sha256.lower(),
                "part_size": PART_SIZE,
                "parts": {},
                "status": UPLOADING,
                "progress": 0,
                "error": None,
                "replace": False,
                "created_at": time.time(),
                "paused_automations": [],
                "deferred_connectors": [],
                "skipped": [],
                "secrets_applied": False,
                "finish_result": None,
            }
        )
    return {"move_id": move_id, "part_size": PART_SIZE, "max_size": max_size}


def part_count(meta: dict) -> int:
    return math.ceil(meta["size"] / meta["part_size"])


def status(move_id: str, owner: str) -> dict:
    meta = _owned(move_id, owner)
    return {
        "move_id": move_id,
        "status": meta["status"],
        "received": sorted(int(n) for n in meta["parts"]),
        "progress": meta["progress"],
        "error": meta["error"],
        "size": meta["size"],
        "part_size": meta["part_size"],
        "parts": part_count(meta),
    }


async def put_part(
    move_id: str, owner: str, n: int, part_sha256: str, body: AsyncIterator[bytes]
) -> None:
    """Store part ``n`` (0-based). Idempotent: resending a part replaces it with identical bytes."""
    meta = _owned(move_id, owner)
    if meta["status"] != UPLOADING:
        raise ConflictError("this move is no longer accepting parts")
    if not 0 <= n < part_count(meta):
        raise ValidationError("part number out of range")
    if not _HEX64.match(part_sha256.lower()):
        raise ValidationError("X-Part-Sha256 must be a 64-character hex digest")
    expected = min(meta["part_size"], meta["size"] - n * meta["part_size"])
    scratch = _dir(move_id) / f"part-{n}.tmp"
    digest, length = hashlib.sha256(), 0
    try:
        with open(scratch, "wb") as out:
            async for chunk in body:
                length += len(chunk)
                if length > expected:
                    raise ValidationError("part is larger than expected")
                digest.update(chunk)
                out.write(chunk)
        if length != expected:
            raise ValidationError("part is shorter than expected")
        if digest.hexdigest() != part_sha256.lower():
            raise ValidationError("part checksum does not match")
        with _lock:
            meta = _owned(move_id, owner)
            if meta["status"] != UPLOADING:
                raise ConflictError("this move is no longer accepting parts")
            with open(scratch, "rb") as src, open(_bundle(move_id), "r+b") as dest:
                dest.seek(n * meta["part_size"])
                shutil.copyfileobj(src, dest, 1024 * 1024)
            meta["parts"][str(n)] = digest.hexdigest()
            _write(meta)
    finally:
        scratch.unlink(missing_ok=True)


def _whole_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as source:
        for block in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _book_is_empty() -> bool:
    from backend.db.onboarding_state_store import OnboardingStateStore
    from backend.services.setup import is_fresh_setup

    return is_fresh_setup() and not OnboardingStateStore().restore_pending()


def commit(move_id: str, owner: str, replace: bool, runner: Callable[[bytes], dict]) -> dict:
    """Start the import in the background; poll ``status`` for progress."""
    with _lock:
        meta = _owned(move_id, owner)
        if meta["status"] != UPLOADING:
            raise ConflictError("this move was already committed")
        missing = part_count(meta) - len(meta["parts"])
        if missing:
            raise ConflictError(f"{missing} parts have not arrived yet")
        if not replace and not _book_is_empty():
            raise MoveNotEmpty(
                "This server already has a book. Moving replaces it; confirm to continue."
            )
        _running.add(move_id)
        meta.update(status=IMPORTING, progress=1, replace=replace, error=None)
        _write(meta)
    threading.Thread(
        target=_import, args=(move_id, runner), name=f"move-{move_id[:11]}", daemon=True
    ).start()
    return status(move_id, owner)


def _progress(move_id: str, value: int) -> None:
    with _lock:
        meta = _read(move_id)
        meta["progress"] = value
        _write(meta)


def _import(move_id: str, runner: Callable[[bytes], dict]) -> None:
    from backend.services import move_secrets, portability

    failure: Optional[str] = None
    try:
        meta = _read(move_id)
        _progress(move_id, 5)
        if _whole_sha256(_bundle(move_id)) != meta["sha256"]:
            raise ValidationError(
                "The uploaded book did not match its checksum. Start the move again."
            )
        _progress(move_id, 20)
        data = read_bundle(_bundle(move_id))
        report = portability.inspect_bundle(data)
        if not report["ok"]:
            raise ValidationError(
                "The book could not be read: " + "; ".join(str(e) for e in report["errors"][:3])
            )
        _progress(move_id, 35)
        runner(data)
        del data
        _progress(move_id, 90)
        paused = move_secrets.pause_automations()
        with _lock:
            meta = _read(move_id)
            meta["paused_automations"] = paused
            _write(meta)
    except OpenNetWorthError as exc:
        failure = exc.message or str(exc)
    except Exception:
        logger.exception("move import failed: %s", move_id)
        failure = "The import did not finish. Check the server and try again."
    with _lock:
        try:
            meta = _read(move_id)
            if failure is None:
                meta.update(status=DONE, progress=100)
                _bundle(move_id).unlink(missing_ok=True)
            else:
                meta.update(status=FAILED, error=failure)
            _write(meta)
        except NotFoundError:
            pass
        finally:
            _running.discard(move_id)


def read_bundle(path: Path) -> Path:
    """The uploaded bundle as the importer wants it: the file itself. ``services.portability``
    reads straight from disk (contract C9), so a multi-GB book never sits in memory."""
    return path


def apply_secrets(move_id: str, owner: str, payload: dict) -> dict:
    from backend.services import move_secrets

    meta = _owned(move_id, owner)
    if meta["status"] != DONE:
        raise ConflictError("the book must finish importing before secrets can be applied")
    result = move_secrets.apply(payload)
    with _lock:
        meta = _read(move_id)
        meta["secrets_applied"] = True
        # The book arrived with a key for its model: the first-run "pick a model" step that a
        # restored backup needs (it carries no keys) has nothing left to ask.
        meta["model_ready"] = any(item.get("kind") == "ai_provider" for item in result["applied"])
        meta["deferred_connectors"] = result.pop("deferred")
        meta["skipped"] = result["skipped"]
        _write(meta)
    return result


def finish(move_id: str, owner: str) -> dict:
    """Switch the moved book on (bots, connectors, automations) and report what could not move."""
    from backend.db.onboarding_state_store import OnboardingStateStore
    from backend.services import move_secrets

    meta = _owned(move_id, owner)
    if meta["finish_result"] is not None:
        return meta["finish_result"]
    if meta["status"] != DONE:
        raise ConflictError("the book must finish importing before the move can finish")
    enabled, failed = move_secrets.enable_connectors(meta["deferred_connectors"])
    resumed = move_secrets.resume_automations(meta["paused_automations"])
    if meta.get("model_ready"):
        OnboardingStateStore().mark_complete()
    result = {
        "enabled": enabled,
        "could_not_enable": failed,
        "automations_resumed": resumed,
        "checklist": move_secrets.checklist(meta["skipped"], meta["secrets_applied"]),
    }
    with _lock:
        meta = _read(move_id)
        meta.update(status=FINISHED, finish_result=result)
        _write(meta)
    _bundle(move_id).unlink(missing_ok=True)
    return result


def abort(move_id: str, owner: str) -> None:
    meta = _owned(move_id, owner)
    if meta["status"] == IMPORTING:
        raise ConflictError("the import is running and cannot be cancelled")
    _discard(move_id)
