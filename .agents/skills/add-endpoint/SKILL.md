---
name: add-endpoint
description: How to add a FastAPI route under backend/api/ that respects the dependency rule. Use when adding or changing a REST endpoint, router, or request/response model.
---

# Add a FastAPI endpoint

API routers are the **thin top layer**: validate input → call **one service** → shape the response. No business logic and no financial math (net worth, P&L, FX, balances) in a route — those live in `backend/services/*`.

## Steps

1. **Put it in (or create) a module** under `backend/api/`, one `APIRouter()` per file. Match an existing domain file (`ledger.py`, `commitments_api.py`, `portfolio.py`, …) when one fits.
2. **Define a router + pydantic bodies:**

   ```python
   from fastapi import APIRouter, HTTPException
   from pydantic import BaseModel

   from backend.services.widgets import compute_widget   # call a service
   from backend.db.widget_store import WidgetStore        # or a store for a plain read

   router = APIRouter()

   class WidgetCreate(BaseModel):
       name: str
       kind: str = "default"

   @router.get("/api/widgets")
   def list_widgets(kind: str | None = None):
       return WidgetStore().query_widgets(kind=kind)

   @router.post("/api/widgets")
   def create_widget(body: WidgetCreate):
       w = WidgetStore().create(body.model_dump())
       if w is None:
           raise HTTPException(status_code=404, detail="...")
       return w
   ```

   Stores are stateless — construct one per request (`WidgetStore()`); it reads disk each call, costs nothing, and keeps the path test-monkeypatchable.
3. **Mount the router** in `backend/main.py`: add `from backend.api.widgets import router as widgets_router` and `app.include_router(widgets_router)` alongside the others.
4. **Dependency rule (import-linter enforces this — `make imports`):**
   - `api` may import `ai`, `services`, `db`, `core`. It may **not** be imported by them.
   - **Routes call exactly one service** and don't recompute money math. Don't reach past `services` into raw stores for anything that has domain rules; a plain list/read of a store is fine.
   - Never import another layer's `_private` symbols.
5. **Validation & errors.** Use pydantic for bodies/queries; raise `HTTPException` with a clear status for not-found / bad input. Prefer typed exceptions + structured `logging`; never `print` in library code.

## Tests (required)

Every endpoint gets a `TestClient` smoke test (status + response shape), isolated to `tmp_path` by monkeypatching the relevant store path constant. Pattern: `tests/test_commitments_api.py`. See the `write-test` skill.

## Docs

Add the route to the relevant `api/*` line in the `AGENTS.md` project-structure map and to `FEATURES.md` if user-facing.

## Verify

`make lint && make imports && make test` + the backend import smoke (see `run-app` skill). NEVER boot a uvicorn server to test a route — use `TestClient`.
