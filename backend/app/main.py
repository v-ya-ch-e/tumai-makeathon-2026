from fastapi import FastAPI
from fastapi.responses import RedirectResponse
from pydantic import BaseModel
from typing import Optional

from .wg_agent.api import mount_static, router as wg_router

app = FastAPI(title="TUM.ai Campus Co-Pilot · WG Hunter")
app.include_router(wg_router)
mount_static(app)


class Item(BaseModel):
    name: str
    price: float
    is_offer: Optional[bool] = None


@app.get("/")
def read_root():
    return RedirectResponse(url="/wg/")


@app.get("/health")
def healthz():
    return {"status": "ok"}


@app.get("/api/health")
def api_healthz():
    return {"status": "ok"}


@app.get("/items/{item_id}")
def read_item(item_id: int, q: Optional[str] = None):
    return {"item_id": item_id, "q": q}


@app.put("/items/{item_id}")
def update_item(item_id: int, item: Item):
    return {"item_name": item.name, "item_id": item_id}
