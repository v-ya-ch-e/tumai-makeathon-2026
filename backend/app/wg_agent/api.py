"""FastAPI router: JSON + SSE endpoints for the WG hunter agent."""

from __future__ import annotations

import asyncio
import json
import logging

from fastapi import APIRouter, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from .models import ContactInfo, Hunt, HuntStatus, SearchProfile, WGCredentials
from .orchestrator import HuntOrchestrator


class HuntRequest(BaseModel):
    """Top-level POST body that kicks off a hunt run."""

    requirements: SearchProfile
    credentials: WGCredentials
    profile: ContactInfo
    dry_run: bool = Field(
        default=True,
        description=(
            "If True, the agent searches + scores + drafts messages but never actually "
            "sends anything on wg-gesucht. Great for demos and safe by default."
        ),
    )
    headless: bool = Field(
        default=False,
        description="If False, Playwright browser is visible — best for demos.",
    )

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/wg", tags=["wg-gesucht-agent"])

# In-memory registry of running hunts. For a real deployment this would be a
# DB / Redis; for a hackathon demo a process-local dict is fine.
RUNS: dict[str, Hunt] = {}
ORCHESTRATORS: dict[str, HuntOrchestrator] = {}
TASKS: dict[str, asyncio.Task] = {}


@router.post("/hunt", response_model=Hunt)
async def start_hunt(payload: HuntRequest) -> Hunt:
    run = Hunt(
        requirements=payload.requirements,
        dry_run=payload.dry_run,
        status=HuntStatus.pending,
    )
    orchestrator = HuntOrchestrator(payload, run)
    RUNS[run.id] = run
    ORCHESTRATORS[run.id] = orchestrator
    TASKS[run.id] = asyncio.create_task(orchestrator.run_hunt())
    return run


@router.get("/hunt/{run_id}", response_model=Hunt)
async def get_hunt(run_id: str) -> Hunt:
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
