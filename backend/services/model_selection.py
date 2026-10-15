"""Canonical model references shared by every conversation surface.

Human-facing pickers may render a friendly model id, but persisted selections always
carry the provider key (``"<provider_key>:<model_id>"``). A bare legacy/user value is
resolved against the instance default provider once, at the selection boundary, so a
later provider change cannot silently re-aim the conversation.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable

from backend.core.errors import ConfigurationError
from backend.db.model_config_store import ModelConfigStore, provider_entry


@dataclass(frozen=True)
class ModelChoice:
    """One provider-pinned model choice and its presentation metadata."""

    ref: str
    model_id: str
    provider_key: str
    provider_label: str

    @property
    def display(self) -> str:
        return f"{self.provider_label} • {self.model_id}"


def canonical_model_ref(
    ref: str,
    *,
    store: ModelConfigStore | None = None,
    legacy_bare: bool = False,
) -> str:
    """Resolve ``ref`` and return its provider-pinned canonical form."""

    clean = (ref or "").strip()
    if not clean:
        return ""
    if ":" in clean:
        provider_key, _, model_id = clean.partition(":")
        if provider_entry(provider_key) is None and not legacy_bare:
            raise ConfigurationError(
                f"Unknown AI provider {provider_key!r}. Choose a valid provider in "
                "Settings → AI Models."
            )
        if provider_entry(provider_key) is not None:
            if not model_id and provider_key not in ("codex_cli", "antigravity_cli"):
                raise ConfigurationError(
                    "AI model is not configured. Choose one in Settings → AI Models."
                )
            # Canonical identity does not require live credentials or an enabled provider.
            # Availability is validated when an explicit preference is saved, and again
            # inside provider resolution for already-stored selections.
            return clean
        # Old session metadata stored only the model id. Local model ids commonly contain
        # a tag separator (for example ``llama3.1:8b``), so a read-time migration must not
        # reinterpret an unknown prefix as a provider. New selections remain strict above.
    store = store or ModelConfigStore()
    default_ref = (store.public().get("default_model") or "").strip()
    provider_key, separator, _ = default_ref.partition(":")
    if not separator or not provider_key:
        raise ConfigurationError(
            "AI provider is not configured. Choose one in Settings → AI Models."
        )
    if provider_entry(provider_key) is None:
        raise ConfigurationError(
            f"Unknown AI provider {provider_key!r}. Choose a valid provider in "
            "Settings → AI Models."
        )
    # A legacy bare value has already lost its provider identity. Pin it to the
    # current default provider without resolving credentials or availability; those
    # belong inside the supervised turn just like an already-canonical selection.
    return f"{provider_key}:{clean}"


def describe_model_ref(ref: str, *, store: ModelConfigStore | None = None) -> ModelChoice:
    """Resolve ``ref`` into its canonical value and friendly provider/model label."""

    store = store or ModelConfigStore()
    canonical = canonical_model_ref(ref, store=store)
    provider_key, _, model_id = canonical.partition(":")
    entry = provider_entry(provider_key) or {}
    provider_label = (entry.get("label") or provider_key).strip()
    return ModelChoice(
        ref=canonical,
        model_id=model_id,
        provider_key=provider_key,
        provider_label=provider_label,
    )


def instance_default_model_ref(*, store: ModelConfigStore | None = None) -> str:
    """Return the instance default in the same canonical form used by a conversation."""

    store = store or ModelConfigStore()
    default_ref = (store.public().get("default_model") or "").strip()
    return canonical_model_ref(default_ref, store=store)


def configured_model_choices(
    *,
    store: ModelConfigStore | None = None,
    preferred_refs: Iterable[str] | None = None,
    limit: int = 10,
) -> list[ModelChoice]:
    """Compact default + task + preferred list for non-searchable channel UIs.

    Web can search the complete live provider catalog. A Telegram inline keyboard
    cannot sensibly render hundreds of models, so it presents the same stored default,
    task pins, and user-managed Preferred shortlist. The selection identity and provider
    metadata remain the same; only the adapter's compact presentation differs.
    """

    store = store or ModelConfigStore()
    if preferred_refs is None:
        try:
            from backend.db.preferred_models_store import PreferredModelsStore

            preferred_refs = PreferredModelsStore().resolved().get("models") or []
        except Exception:  # noqa: BLE001 — a corrupt shortlist must not break /model
            preferred_refs = []
    public = store.public()
    refs = [
        public.get("default_model"),
        *(public.get("task_models") or {}).values(),
        *preferred_refs,
    ]
    choices: list[ModelChoice] = []
    seen: set[str] = set()
    provider_enabled = getattr(store, "provider_enabled", lambda _key: True)
    for ref in refs:
        if not ref:
            continue
        try:
            choice = describe_model_ref(str(ref), store=store)
        except ConfigurationError:
            # A removed provider can survive in a legacy task/preferred row. One stale
            # optional choice must not take down the whole channel picker.
            continue
        if not provider_enabled(choice.provider_key):
            continue
        if choice.ref in seen:
            continue
        seen.add(choice.ref)
        choices.append(choice)
        if len(choices) >= limit:
            break
    return choices
