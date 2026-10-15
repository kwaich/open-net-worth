"""In-app Help REST surface — thin over ``services.product_docs``.

  GET /api/help/docs                — every doc's metadata + the category rollup
  GET /api/help/docs/{doc_id:path}  — one doc's full markdown (404 for an unknown id)

The app-wide auth gate (``backend.main``'s ``dependencies=[Depends(require_auth)]``)
already covers both routes. ``doc_id`` is never used to build a filesystem path here or
in the service it calls — see ``services/product_docs.py``'s SECURITY note; an unknown id
resolves to ``NotFoundError`` -> 404, never a filesystem error.
"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException

from backend.core.errors import NotFoundError
from backend.services import product_docs

router = APIRouter()


def _doc_meta_dict(meta: product_docs.DocMeta) -> dict:
    return {
        "doc_id": meta.doc_id,
        "title": meta.title,
        "description": meta.description,
        "category": meta.category,
        "order": meta.order,
        "audience": list(meta.audience),
        "status": meta.status,
        "related": list(meta.related),
        "keywords": list(meta.keywords),
        "sections": [{"anchor": s.anchor, "heading": s.heading} for s in meta.sections],
    }


@router.get("/api/help/docs")
def list_help_docs() -> dict:
    docs = [_doc_meta_dict(m) for m in product_docs.list_docs()]
    categories = [
        {"id": c.id, "title": c.title, "order": c.order, "description": c.description}
        for c in product_docs.list_categories()
    ]
    return {"docs": docs, "categories": categories}


@router.get("/api/help/docs/{doc_id:path}")
def get_help_doc(doc_id: str) -> dict:
    # Deliberately a narrower shape than the list route's DocMeta dict — no `order`/
    # `keywords` (sidebar-ordering/search-only fields with no reader use once a specific
    # page is open) — per the frozen frontend contract.
    try:
        doc = product_docs.get_doc(doc_id)
    except NotFoundError as exc:
        raise HTTPException(404, exc.message) from exc
    return {
        "doc_id": doc.doc_id,
        "title": doc.title,
        "description": doc.description,
        "category": doc.category,
        "status": doc.status,
        "audience": list(doc.audience),
        "related": list(doc.related),
        "markdown": doc.markdown,
        "sections": [{"anchor": s.anchor, "heading": s.heading} for s in doc.sections],
    }
