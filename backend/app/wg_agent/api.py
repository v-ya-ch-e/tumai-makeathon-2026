"""FastAPI router: endpoints + minimal HTML dashboard for the WG hunter agent."""

from __future__ import annotations

import asyncio
import json
import logging
from pathlib import Path

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import HTMLResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from .models import HuntRequest, HuntRun, HuntStatus
from .orchestrator import HuntOrchestrator

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/wg", tags=["wg-gesucht-agent"])

HERE = Path(__file__).resolve().parent
templates = Jinja2Templates(directory=str(HERE / "templates"))

# In-memory registry of running hunts. For a real deployment this would be a
# DB / Redis; for a hackathon demo a process-local dict is fine.
RUNS: dict[str, HuntRun] = {}
ORCHESTRATORS: dict[str, HuntOrchestrator] = {}
TASKS: dict[str, asyncio.Task] = {}


# --- UI -----------------------------------------------------------------------

@router.get("/", response_class=HTMLResponse)
async def home(request: Request) -> HTMLResponse:
    return templates.TemplateResponse(
        request, "home.html", {"runs": list(RUNS.values())[-10:]}
    )


@router.get("/runs/{run_id}", response_class=HTMLResponse)
async def run_page(request: Request, run_id: str) -> HTMLResponse:
    run = RUNS.get(run_id)
    if run is None:
        raise HTTPException(status_code=404, detail="Run not found")
    return templates.TemplateResponse(request, "run.html", {"run": run})


# --- REST ---------------------------------------------------------------------

@router.post("/hunt", response_model=HuntRun)
async def start_hunt(payload: HuntRequest) -> HuntRun:
    run = HuntRun(
        requirements=payload.requirements,
        dry_run=payload.dry_run,
        status=HuntStatus.pending,
    )
    orchestrator = HuntOrchestrator(payload, run)
    RUNS[run.id] = run
    ORCHESTRATORS[run.id] = orchestrator
    TASKS[run.id] = asyncio.create_task(orchestrator.run_hunt())
    return run


@router.get("/hunt/{run_id}", response_model=HuntRun)
async def get_hunt(run_id: str) -> HuntRun:
    run = RUNS.get(run_id)
    if run is None:
        raise HTTPException(status_code=404, detail="Run not found")
    return run


@router.get("/hunt/{run_id}/stream")
async def stream_hunt(run_id: str) -> StreamingResponse:
    run = RUNS.get(run_id)
    orch = ORCHESTRATORS.get(run_id)
    if run is None or orch is None:
        raise HTTPException(status_code=404, detail="Run not found")

    async def event_source():
        # Replay everything already logged so late-joiners see the full history.
        for action in list(run.actions):
            yield _sse(action.model_dump(mode="json"))
        while True:
            try:
                action = await asyncio.wait_for(orch.event_queue.get(), timeout=1.0)
            except asyncio.TimeoutError:
                if run.status in (HuntStatus.done, HuntStatus.failed):
                    yield _sse(
                        {
                            "kind": "stream-end",
                            "status": run.status.value,
                            "summary": "done",
                        }
                    )
                    return
                yield ": keep-alive\n\n"
                continue
            payload = action.model_dump(mode="json")
            yield _sse(payload)
            if action.summary == "stream-end":
                return

    return StreamingResponse(event_source(), media_type="text/event-stream")


def _sse(data: dict) -> str:
    return f"data: {json.dumps(data)}\n\n"


def mount_static(app) -> None:
    """Mount /wg/static to serve the (tiny) CSS/JS bundle."""
    static_dir = HERE / "static"
    static_dir.mkdir(exist_ok=True)
    app.mount("/wg/static", StaticFiles(directory=str(static_dir)), name="wg-static")
