"""Outbound notification-channel config — pydantic patch schema (webhook).

``models/`` is a leaf beside ``core/`` (import-linter Contract 4): it imports only
stdlib + pydantic + ``backend.models.base``. This describes the **PUT payload** the
``/api/channels/webhook`` route accepts; the *stored* shape (config + the connector
secrets) lives in ``db/channel_config_store.py``. Email's settings are the Email
connector's (``api/email_ingest_api.py``).

The webhook's ``url`` and ``secret`` are both WRITE-ONLY: a blank value on update means
"leave the saved one unchanged", and ``ChannelConfigStore.public()`` exposes only ``has_*``
flags (and the URL's host) — neither is ever returned to a client. Every field is optional
(``None`` = leave unchanged), so a partial save never clobbers other fields.
"""

from __future__ import annotations

from typing import Optional

from pydantic import field_validator

from backend.models.base import ONBaseModel, _blank_to_none


class WebhookChannelPatch(ONBaseModel):
    """Patch for the ``webhook`` (HTTP POST) channel. Every field optional."""

    enabled: Optional[bool] = None
    url: Optional[str] = None  # WRITE-ONLY — blank leaves the saved one unchanged
    secret_header_name: Optional[str] = None
    secret: Optional[str] = None  # WRITE-ONLY — blank leaves the saved one unchanged

    _blank_strs = field_validator("url", "secret_header_name", mode="before")(_blank_to_none)
