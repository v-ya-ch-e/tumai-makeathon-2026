import logging
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Optional

from alembic import command
from alembic.config import Config
from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from .wg_agent.api import router as api_router

logger = logging.getLogger(__name__)

# frontend/dist/ is built by `npm run build` in the frontend/ directory.
# Path resolution: backend/app/main.py -> repo-root/frontend/dist
REPO_ROOT = Path(__file__).resolve().parents[2]
FRONTEND_DIST = REPO_ROOT / "frontend" / "dist"
BACKEND_DIR = Path(__file__).resolve().parents[1]


@asynccontextmanager
async def lifespan(app: FastAPI):
    from .wg_agent import db as wg_db

    wg_db.init_db()
    logger.info("WG database URL: %s", wg_db.DATABASE_URL)
    alembic_ini = BACKEND_DIR / "alembic.ini"
    cfg = Config(str(alembic_ini))
    cfg.set_main_option("script_location", str(BACKEND_DIR / "alembic"))
    command.upgrade(cfg, "head")
    # TODO(periodic_hunter): re-spawn running hunts
    yield


app = FastAPI(title="TUM.ai Campus Co-Pilot · WG Hunter", lifespan=lifespan)
app.include_router(api_router)


class Item(BaseModel):
    name: str
    price: float
    is_offer: Optional[bool] = None


@app.get("/health")
def healthz() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/api/health")
def api_healthz() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/items/{item_id}")
def read_item(item_id: int, q: Optional[str] = None):
    return {"item_id": item_id, "q": q}


@app.put("/items/{item_id}")
def update_item(item_id: int, item: Item):
    return {"item_name": item.name, "item_id": item_id}


# --- SPA serving -------------------------------------------------------------
# Vite's `npm run build` emits `frontend/dist/{index.html, assets/…}`.
# /assets/ is served verbatim; every non-/api/* path falls back to
# index.html so client-side React Router can handle it.

if (FRONTEND_DIST / "assets").is_dir():
    app.mount(
        "/assets",
        StaticFiles(directory=str(FRONTEND_DIST / "assets")),
        name="frontend-assets",
    )


@app.get("/{full_path:path}", include_in_schema=False)
def spa_fallback(full_path: str) -> FileResponse:
    if full_path.startswith(("api/", "assets/")):
        raise HTTPException(status_code=404, detail="Not Found")
    index_file = FRONTEND_DIST / "index.html"
    if not index_file.is_file():
        raise HTTPException(
            status_code=503,
            detail="frontend/dist/index.html not found — run `npm run build` in frontend/",
        )
    return FileResponse(str(index_file))
