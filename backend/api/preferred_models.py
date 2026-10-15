"""REST surface for the owner's preferred-model shortlist (Settings → AI Models).

GET returns the list; PUT replaces it wholesale (it is a selection, not a patchable
object). Thin router per the layer rule — validation + one store call + response shape.
"""

from __future__ import annotations

from fastapi import APIRouter
from pydantic import BaseModel

from backend.db.preferred_models_store import PreferredModelsStore

router = APIRouter()


class PreferredModelsUpdate(BaseModel):
    models: list[str]


@router.get("/api/preferred-models")
def get_preferred_models():
    return PreferredModelsStore().resolved()


@router.put("/api/preferred-models")
def put_preferred_models(body: PreferredModelsUpdate):
    return PreferredModelsStore().replace(body.models)
