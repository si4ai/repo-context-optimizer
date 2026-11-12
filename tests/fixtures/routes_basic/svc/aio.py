"""Route table, aiohttp style."""
from aiohttp import web


async def index(request):
    """Front page."""


async def stats(request):
    """Counters."""


app = web.Application()
app.add_routes([web.get("/", index), web.post("/submit", index)])
app.router.add_get("/stats", stats)
app.router.add_route("PUT", "/stats", stats)
app.router.add_route("*", "/any", index)
