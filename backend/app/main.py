import logging
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from .wg_agent.api import router as api_router

logger = logging.getLogger(__name__)

# frontend/dist/ is built by `npm run build` in the frontend/ directory.
# Path resolution: backend/app/main.py -> repo-root/frontend/dist
REPO_ROOT = Path(__file__).resolve().parents[2]
FRONTEND_DIST = REPO_ROOT / "frontend" / "dist"


@asynccontextmanager
async def lifespan(app: FastAPI):
    from .wg_agent import db as wg_db

    wg_db.init_db()
    logger.info("WG database: %s", wg_db.describe_database())
    from .wg_agent import periodic as wg_periodic

    await wg_periodic.resume_running_hunts()
    yield


app = FastAPI(title="TUM.ai Campus Co-Pilot · WG Hunter", lifespan=lifespan)
app.include_router(api_router)


@app.get("/health")
def healthz() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/api/health")
def api_healthz() -> dict[str, str]:
    return {"status": "ok"}


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
