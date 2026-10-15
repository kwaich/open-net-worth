"""What a blind model actually RECEIVES for an attached image, and what the user is told.

ADR 2026-07-26. Companion to test_vision_capability.py (which pins the ladder itself):
these pin the two user-visible outcomes — transcription when a reader is configured, an
explicit "you have NOT seen this" when none is — plus the statement importer's up-front
refusal for a scanned PDF.

Cross-channel-attachments RFC Phase 2 split ``build_user_content`` into two steps:
construction time (durable ``attachment_ref`` blocks only — see
``tests/test_attachments_persistence.py``) and DISPATCH time
(``resolve_attachment_refs_for_dispatch``, the current-turn resolution these tests pin).
``_dispatch_content`` below chains the two exactly as ``ai/assistant.py::_run_turn`` does.
"""

from __future__ import annotations

import pytest

from backend.ai import image_payloads
from backend.ai.attachments import build_user_content, resolve_attachment_refs_for_dispatch
from backend.services import attachments as attachments_svc
from backend.services.image_extract import ExtractOutcome

_IMG = "ZmFrZS1qcGVn"  # base64, contents irrelevant — nothing decodes it in these tests


def _image_ref(name: str = "receipt.jpg") -> str:
    return attachments_svc.create_attachment(
        {
            "kind": "image",
            "media_type": "image/jpeg",
            "data": _IMG,
            "summary": f"Image ({name}, 1KB)",
        },
        filename=name,
    )


def _scan_ref(pages: int, name: str = "statement.pdf") -> str:
    return attachments_svc.create_attachment(
        {
            "kind": "pdf_pages",
            "images": [{"media_type": "image/png", "data": _IMG} for _ in range(pages)],
            "summary": f"PDF '{name}' ({pages} pages, scanned, 400KB)",
        },
        filename=name,
    )


async def _dispatch_content(text: str, refs, *, model: str):
    """The two-step flow a real turn runs: durable refs, then dispatch resolution."""
    durable = await build_user_content(text, refs)
    return await resolve_attachment_refs_for_dispatch(durable, model=model)


def _texts(content) -> "list[str]":
    assert isinstance(content, list), content
    return [p["text"] for p in content if p.get("type") == "text"]


def _blocks(content, btype: str) -> list:
    return [p for p in content if p.get("type") == btype]


@pytest.fixture()
def blind(monkeypatch):
    """The active model has no vision, and NO reader is configured."""
    monkeypatch.setattr("backend.services.vision_capability.supports_vision", lambda *a, **k: False)
    monkeypatch.setattr(
        "backend.services.vision_capability.resolve_extractor_ref", lambda *a, **k: ""
    )


@pytest.fixture()
def blind_with_reader(monkeypatch):
    """The active model has no vision, and a reader IS configured."""
    monkeypatch.setattr("backend.services.vision_capability.supports_vision", lambda *a, **k: False)
    monkeypatch.setattr(
        "backend.services.vision_capability.resolve_extractor_ref",
        lambda *a, **k: "anthropic:claude-haiku-4-5",
    )


class TestSightedModelIsUnaffected:
    async def test_an_image_still_rides_as_a_real_image_block(self, monkeypatch):
        monkeypatch.setattr(
            "backend.services.vision_capability.supports_vision", lambda *a, **k: True
        )
        content = await _dispatch_content("what is this", [_image_ref()], model="openai:gpt-4o")
        assert len(_blocks(content, "image")) == 1

    async def test_an_UNKNOWN_model_is_let_through_with_the_image_intact(self, monkeypatch):
        """The fail-open rule. A model nobody has classified must still get the image —
        the provider either accepts it or refuses and teaches us."""
        monkeypatch.setattr(
            "backend.services.vision_capability.supports_vision", lambda *a, **k: None
        )
        content = await _dispatch_content("what is this", [_image_ref()], model="custom:mystery")
        assert len(_blocks(content, "image")) == 1


class TestBlindModelWithNoReader:
    async def test_no_image_block_is_sent(self, blind):
        content = await _dispatch_content("what is this", [_image_ref()], model="glm:glm-4.6")
        assert _blocks(content, "image") == []

    async def test_the_model_is_told_it_has_NOT_seen_the_image_and_how_to_fix_it(self, blind):
        content = await _dispatch_content("what is this", [_image_ref()], model="glm:glm-4.6")
        joined = "\n".join(_texts(content))
        assert "NOT seen" in joined
        assert "do not guess" in joined
        # Names the model that failed and the exact remedy — a bare "unsupported" is useless.
        assert "glm:glm-4.6" in joined
        assert "chat_vision" in joined

    async def test_the_users_own_text_still_leads_the_turn(self, blind):
        """Same rule the mention/screen blocks follow: the user's words must not end up buried
        under machine prose."""
        content = await _dispatch_content("what is this", [_image_ref()], model="glm:glm-4.6")
        assert _texts(content)[-1] == "what is this"


class TestBlindModelWithAReader:
    async def test_the_extracted_text_rides_the_turn_instead_of_the_image(
        self, blind_with_reader, monkeypatch
    ):
        async def fake_extract(images, *, model):
            assert model == "anthropic:claude-haiku-4-5"
            return _ok("«image-extract:n1»COFFEE BEANS 12.40«/image-extract:n1»", len(images))

        monkeypatch.setattr("backend.services.image_extract.extract_image_text", fake_extract)
        content = await _dispatch_content("book this", [_image_ref()], model="glm:glm-4.6")
        joined = "\n".join(_texts(content))
        assert _blocks(content, "image") == []
        assert "COFFEE BEANS 12.40" in joined
        # The transcription is untrusted content and must be labelled as such.
        assert "untrusted" in joined

    async def test_a_failed_extraction_degrades_to_the_honest_message(
        self, blind_with_reader, monkeypatch
    ):
        async def fake_extract(images, *, model):
            return ExtractOutcome(pages_total=len(images))

        monkeypatch.setattr("backend.services.image_extract.extract_image_text", fake_extract)
        content = await _dispatch_content("book this", [_image_ref()], model="glm:glm-4.6")
        joined = "\n".join(_texts(content))
        assert "NOT seen" in joined
        assert "failed" in joined

    async def test_aggregate_image_overflow_is_rejected_before_any_reader_call(
        self, blind_with_reader, monkeypatch
    ):
        from backend.core.errors import ValidationError

        monkeypatch.setattr(image_payloads, "MAX_TOTAL_IMAGE_BYTES", 5)

        async def must_not_extract(*_args, **_kwargs):
            raise AssertionError("aggregate validation must run before paid extraction")

        monkeypatch.setattr("backend.services.image_extract.extract_image_text", must_not_extract)
        refs = [_image_ref("a.png"), _image_ref("b.png")]

        with pytest.raises(ValidationError, match="combined image payload"):
            await _dispatch_content("read these", refs, model="glm:glm-4.6")

    async def test_aggregate_count_overflow_refuses_before_any_reader_call(
        self, blind_with_reader, monkeypatch
    ):
        from backend.services.image_extract import MAX_IMAGES

        async def must_not_extract(*_args, **_kwargs):
            raise AssertionError("aggregate count refusal must precede paid extraction")

        monkeypatch.setattr("backend.services.image_extract.extract_image_text", must_not_extract)
        refs = [_image_ref(f"receipt-{index}.jpg") for index in range(MAX_IMAGES + 1)]

        content = await _dispatch_content("read these", refs, model="glm:glm-4.6")

        joined = "\n".join(_texts(content))
        assert "NOT read" in joined
        assert f"{MAX_IMAGES}-page limit" in joined


def _ok(text: str, pages: int, gaps: tuple = ()) -> ExtractOutcome:
    """A complete read of ``pages`` pages."""
    return ExtractOutcome(text=text, pages_total=pages, pages_read=pages - len(gaps), gaps=gaps)


class TestScannedPdfInChat:
    async def test_page_images_are_read_not_dropped(self, blind_with_reader, monkeypatch):
        seen: dict = {}

        async def fake_extract(images, *, model):
            seen["count"] = len(images)
            return _ok("«image-extract:n1»ROW 1 | 12.40«/image-extract:n1»", len(images))

        monkeypatch.setattr("backend.services.image_extract.extract_image_text", fake_extract)
        content = await _dispatch_content("read this", [_scan_ref(2)], model="glm:glm-4.6")
        assert _blocks(content, "image") == []
        assert seen["count"] == 2
        assert "ROW 1 | 12.40" in "\n".join(_texts(content))

    async def test_a_30_page_scan_is_read_IN_FULL_not_truncated(
        self, blind_with_reader, monkeypatch
    ):
        """The cap that used to sit here (4 pages, truncate + note) was wrong: page 4 of a
        30-page statement is a wrong answer with a confident tone. 30 is under MAX_IMAGES, so
        every page is read — matching statements/parser, which has always done this."""
        seen: dict = {}

        async def fake_extract(images, *, model):
            seen["count"] = len(images)
            return _ok("«image-extract:n1»ROW«/image-extract:n1»", len(images))

        monkeypatch.setattr("backend.services.image_extract.extract_image_text", fake_extract)
        content = await _dispatch_content("read this", [_scan_ref(30)], model="glm:glm-4.6")
        joined = "\n".join(_texts(content))
        assert seen["count"] == 30
        assert "INCOMPLETE" not in joined  # nothing was dropped, so nothing is disclaimed

    async def test_a_scan_past_the_page_ceiling_is_REFUSED_never_partially_read(
        self, blind_with_reader, monkeypatch
    ):
        """Same contract as statements/parser: past the ceiling, refuse loudly. A truncated
        transcription that reads like a whole document is the outcome being prevented."""
        from backend.services.image_extract import MAX_IMAGES

        # Exercise the REAL extract_image_text (the refusal is its decision), and fail loudly
        # if it reaches the provider at all — "refuses without making a single call" is the
        # actual contract, not just "returns a refusal string".
        async def no_calls(*a, **k):
            raise AssertionError("no page should be sent to the provider past the ceiling")

        monkeypatch.setattr("backend.services.image_extract.complete", no_calls)
        content = await _dispatch_content(
            "read this", [_scan_ref(MAX_IMAGES + 1)], model="glm:glm-4.6"
        )
        joined = "\n".join(_texts(content))
        assert "NOT read" in joined
        assert f"{MAX_IMAGES}-page limit" in joined
        assert "Transactions → Import" in joined

    async def test_a_gap_is_named_and_the_total_is_explicitly_untrustworthy(
        self, blind_with_reader, monkeypatch
    ):
        """One unreadable page must not discard the other 9 — but it must also make the
        remaining totals explicitly unusable, per the repo's own partial-page rule."""

        async def fake_extract(images, *, model):
            return ExtractOutcome(
                text="«image-extract:n1»ROWS«/image-extract:n1»",
                pages_total=10,
                pages_read=9,
                gaps=("page 7 could not be read",),
            )

        monkeypatch.setattr("backend.services.image_extract.extract_image_text", fake_extract)
        content = await _dispatch_content("read this", [_scan_ref(10)], model="glm:glm-4.6")
        joined = "\n".join(_texts(content))
        assert "INCOMPLETE" in joined
        assert "9 of 10 pages" in joined
        assert "page 7 could not be read" in joined
        assert "Do not treat any total here as the document" in joined

    async def test_the_page_count_is_named_when_nothing_can_read_it(self, blind):
        content = await _dispatch_content("read this", [_scan_ref(3)], model="glm:glm-4.6")
        joined = "\n".join(_texts(content))
        assert "3-page scanned PDF" in joined
        assert "NOT seen" in joined


class TestStatementImportRefusesUpFront:
    async def test_a_scan_is_refused_with_a_named_model_and_a_fix(self, monkeypatch):
        """Before this check the parser burned TWO provider calls to produce "unavailable OR
        doesn't support images" — an ambiguity with no model named and no remedy."""
        from backend.services.statements import tiers
        from backend.services.statements.tiers import StatementParseError

        # Blind everywhere: the pin can't see AND the instance-default fallback can't either,
        # so the resolver returns "" and the refusal must still NAME something the owner can
        # act on — it falls back to naming the rejected pin.
        monkeypatch.setattr(tiers, "_task_model", lambda task: "glm:glm-4.6")
        monkeypatch.setattr(
            "backend.services.vision_capability.supports_vision", lambda *a, **k: False
        )

        async def boom(*a, **k):  # must never be reached
            raise AssertionError("the provider was called for a model known to have no vision")

        monkeypatch.setattr(tiers, "_row_complete", boom)
        with pytest.raises(StatementParseError) as exc:
            await tiers.tier3_vision([{"media_type": "image/png", "data": _IMG}])
        msg = str(exc.value)
        assert "glm:glm-4.6" in msg
        # Points at the ONE import knob that now exists, not the retired per-stage task name.
        assert "Statement import" in msg
        assert "statement_vision" not in msg
        assert "Nothing was imported" in msg

    def test_an_unknown_model_is_still_attempted(self, monkeypatch):
        """Fail-open here too: refusing an import on a guess would block a working setup."""
        from backend.services.statements import tiers

        monkeypatch.setattr(tiers, "_task_model", lambda task: "custom:mystery")
        monkeypatch.setattr(
            "backend.services.vision_capability.supports_vision", lambda *a, **k: None
        )
        tiers._assert_vision_model_available()  # does not raise
