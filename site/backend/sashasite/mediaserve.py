"""Serving the variant files, and the request-body cap for uploads.

``/media`` is the canonical URL (Caddy serves it straight from SITE_MEDIA_DIR in
production); ``/api/media-files`` is the same files under /api, because the Astro
dev server proxies only /api. Only ``{id}-{w}.webp`` names are served, never a
temp file or anything else that lands in the directory.
"""

from __future__ import annotations

import json
import re
from typing import Any

from starlette.exceptions import HTTPException as StarletteHTTPException
from starlette.responses import Response
from starlette.staticfiles import StaticFiles
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from sashasite.media import VARIANT_NAME

_NAME = re.compile(VARIANT_NAME)
#: A variant is never rewritten (ids are never reused), so it can be cached forever.
IMMUTABLE = "public, max-age=31536000, immutable"


class VariantFiles(StaticFiles):
    async def get_response(self, path: str, scope: Scope) -> Response:
        if not _NAME.match(path):
            raise StarletteHTTPException(status_code=404)
        response = await super().get_response(path, scope)
        if response.status_code in (200, 304):
            response.headers["Cache-Control"] = IMMUTABLE
            response.headers["X-Content-Type-Options"] = "nosniff"
        return response


class _TooLarge(Exception):
    pass


class BodyLimit:
    """Refuse request bodies over ``limit`` bytes on the given (method, path
    prefix) with 413 -- from Content-Length up front, and by counting when the
    client streams without one. Keeps a 2 GB POST from being spooled to disk
    before the handler can say no."""

    def __init__(self, app: ASGIApp, *, method: str, path_prefix: str, limit: int) -> None:
        self.app = app
        self.method = method
        self.path_prefix = path_prefix
        self.limit = limit

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if (
            scope["type"] != "http"
            or scope["method"] != self.method
            or not scope["path"].startswith(self.path_prefix)
        ):
            await self.app(scope, receive, send)
            return

        headers: dict[bytes, bytes] = dict(scope.get("headers", []))
        declared = headers.get(b"content-length")
        if declared is not None and declared.isdigit() and int(declared) > self.limit:
            await self._reject(send)
            return

        seen = 0
        over = False
        started = False

        async def counting_receive() -> Message:
            nonlocal seen, over
            msg = await receive()
            if msg["type"] == "http.request":
                seen += len(msg.get("body", b""))
                if seen > self.limit:
                    over = True
                    raise _TooLarge
            return msg

        async def guarded_send(msg: Message) -> None:
            # The app may turn our exception into its own error response (FastAPI
            # reports a failed form parse as 400); replace that with the 413.
            nonlocal started
            if over:
                if msg["type"] == "http.response.start" and not started:
                    started = True
                    await self._reject(send)
                return
            if msg["type"] == "http.response.start":
                started = True
            await send(msg)

        try:
            await self.app(scope, counting_receive, guarded_send)
        except _TooLarge:
            if not started:
                await self._reject(send)

    async def _reject(self, send: Send) -> None:
        mb = self.limit / (1024 * 1024)
        body = json.dumps({"detail": f"Upload too large: the limit is {mb:.1f} MB."}).encode()
        start: dict[str, Any] = {
            "type": "http.response.start",
            "status": 413,
            "headers": [
                (b"content-type", b"application/json"),
                (b"content-length", str(len(body)).encode()),
                (b"connection", b"close"),
            ],
        }
        await send(start)
        await send({"type": "http.response.body", "body": body})
