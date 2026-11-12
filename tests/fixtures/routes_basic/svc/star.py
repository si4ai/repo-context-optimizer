"""Route table, starlette style: one catch-all for every verb."""
from starlette.applications import Starlette
from starlette.routing import Route, WebSocketRoute


async def ws_endpoint(socket) -> None:
    """Desk socket."""


async def ping(request):
    """Ping."""


def build():
    _http = ["GET", "POST", "HEAD"]

    async def http_endpoint(request):
        """Every HTTP verb goes to the same dispatcher."""

    return Starlette(
        routes=[
            WebSocketRoute("/ws/{desk}", ws_endpoint),
            Route("/ping", ping),
            Route("/{path:path}", http_endpoint, methods=_http),
        ],
    )
