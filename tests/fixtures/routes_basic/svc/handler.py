"""Handler dispatch, http.server style, with an inner method."""
from http.server import BaseHTTPRequestHandler
from urllib.parse import urlparse


def make_handler():
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:
            try:
                self._get_inner()
            finally:
                pass

        def _get_inner(self) -> None:
            path = urlparse(self.path).path
            if path == "/health":
                return
            if path.startswith("/api/v1/"):
                path = "/api/" + path[len("/api/v1/"):]
            if path in ("/api/rows", "/api/strip"):
                return
            if "/api/labels" == path:
                return

        def do_POST(self) -> None:
            if self.path == "/api/collect":
                return
            if self.headers.get("x") == "/not/compared/to/a/path/name":
                return

        def _unreached(self) -> None:
            if self.path == "/never":
                return

    return Handler
