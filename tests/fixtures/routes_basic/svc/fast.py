"""Decorator routes, fastapi style."""
from fastapi import APIRouter, FastAPI

app = FastAPI()
router = APIRouter()
READ_WRITE = ["get", "post"]


@app.get("/api/v1/items/{item_id}")
async def read_item(item_id: int) -> dict:
    """Read one item."""
    return {}


@router.post("/api/v1/items")
def create_item(body: dict) -> dict:
    """Create an item.

    Only the first docstring line is the doc.
    """
    return body


@app.api_route("/api/v1/sync", methods=READ_WRITE)
def sync() -> None:
    pass


@app.api_route("/api/v1/legacy")
def legacy() -> None:
    pass


@app.websocket("/ws/{desk}")
async def ws(socket) -> None:
    """Live feed for one desk."""


@app.get("relative/is/not/a/route")
def not_a_route() -> None:
    pass


@cache.get("key")
def cached() -> None:
    pass
