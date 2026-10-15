# Acceptance — attaching an image when the model can't see it

ADR: `docs/adr/2026-07-26-vision-capability-and-image-fallback.md`

Covers the chat composer notice, what the model receives, scanned PDFs, and the statement
importer's refusal. "The model answered as if it saw the image" is a FAIL in every case below.

## Setup

- A GLM (or any text-only) provider configured and selected for the conversation.
- For the "reader configured" cases: Settings → AI Model → Task models → **Image reading**
  set to a vision-capable model.

---

## A. Composer notice (before sending)

**A1 — no notice until an image is attached**
GIVEN the conversation model is `glm-4.6`
WHEN I open the composer and type text with no attachment
THEN no image-capability notice is shown.

**A2 — notice names the reader when one is configured**
GIVEN `glm-4.6` is selected AND Image reading is set to `anthropic:claude-haiku-4-5`
WHEN I attach `receipt.jpg`
THEN a notice appears naming both models and saying the image will be read and passed along.

**A3 — notice is explicit when NO reader is configured**
GIVEN `glm-4.6` is selected AND Image reading is unset AND the instance default is also blind
WHEN I attach `receipt.jpg`
THEN the notice says Lucius won't see the attachment
AND it names where to fix it (Settings → AI Model → Task models → Image reading).

**A4 — no notice for a non-image attachment**
WHEN I attach `statement.csv` on a blind model
THEN no notice appears (a CSV needs no vision).

**A5 — no notice for a vision-capable model**
GIVEN the conversation model is `gpt-4o` or a Claude model
WHEN I attach `receipt.jpg`
THEN no notice appears AND the image is sent as an image.

**A6 — no notice for an UNKNOWN model**
GIVEN the model is a custom/self-hosted id no heuristic recognises
WHEN I attach an image
THEN no notice appears (an unknown capability must not be reported as missing).

---

## B. What the model receives

**B1 — transcription rides the turn**
GIVEN a blind model AND a configured reader
WHEN I attach a receipt photo and ask "what's the total?"
THEN the reply reports the real total from the photo
AND the turn contained no image block
AND the transcription was labelled untrusted content.

**B2 — honest refusal with no reader**
GIVEN a blind model AND no reader
WHEN I attach a receipt and ask "what's the total?"
THEN Lucius says it has not seen the image and does NOT invent a total
AND it tells me how to enable reading.

**B3 — a failed extraction degrades, never fabricates**
GIVEN a blind model AND a reader whose provider is unreachable
WHEN I attach an image
THEN Lucius says the read failed and that it has not seen the image.

**B4 — my own words still lead the turn**
WHEN I attach an image with a caption on a blind model
THEN my typed text is the LAST text part of the turn (not buried under machine prose).

---

## C. Scanned PDFs

**C1 — pages are read, not dropped**
GIVEN a blind model AND a configured reader
WHEN I attach a 2-page scanned PDF
THEN its rows are transcribed and usable in the reply.

**C2 — a 30-page scan is read IN FULL**
WHEN I attach a 30-page scanned PDF on a blind model with a reader
THEN every page is transcribed (30 vision calls, concurrency-bounded)
AND no incompleteness disclaimer appears, because nothing was dropped.

**C2b — past the 40-page ceiling it is REFUSED, never partially read**
WHEN I attach a 41-page scanned PDF on a blind model with a reader
THEN no provider call is made
AND Lucius says it was not read, names the 40-page limit, and points me to
    Transactions → Import.

**C2c — a missing page makes the totals explicitly unusable**
GIVEN one page of a 10-page scan fails to transcribe
WHEN I ask for the statement total
THEN Lucius names which page is missing and refuses to present a total as the document's total.

**C3 — page count is named when nothing can read it**
WHEN I attach a 3-page scan on a blind model with no reader
THEN the message names it as a 3-page scanned PDF and says it was not seen.

---

## D. Statement importer

**D1 — a scan is refused up front, with the fix**
GIVEN the `statement_vision` task resolves to a text-only model
WHEN I import a scanned PDF statement
THEN it fails immediately (no provider call is made)
AND the error names that model, names the `statement_vision` setting, and says
    "Nothing was imported"
AND no transactions were created.

**D2 — an unknown model is still attempted**
GIVEN `statement_vision` resolves to an unrecognised custom model
WHEN I import a scanned PDF
THEN the import is attempted (it is not refused on a guess).

**D3 — a text PDF is unaffected**
WHEN I import a text-layer PDF on a text-only model
THEN it parses normally (the vision tier is never reached).

---

## E. Learning and correction

**E1 — a refusal is learned once**
GIVEN a model whose capability is unknown and which actually rejects images
WHEN I attach an image (first time)
THEN the turn still completes (images stripped after one retry)
AND on the NEXT attachment the composer already shows the notice from A2/A3.

**E3 — the Image reading picker never offers a model that can't read images**
GIVEN the app default is `glm-5.2` (no vision)
WHEN I open Settings → AI Model → Task models → Image reading
THEN the default option reads "None — app default (…) can't read images", NOT "App default (…)"
AND no known text-only model (glm-4.6, glm-5.2) appears in the list
AND a model of unknown capability is still listed.

**E2 — the owner can correct a wrong verdict**
GIVEN a model wrongly classified as blind
WHEN a `user`-sourced override marks it vision-capable
THEN images are sent to it again
AND neither a later model-list fetch (`declared`) nor a stale `learned` row overrides that.
