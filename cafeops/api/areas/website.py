"""The public website's admin, reached through the back office (owner, 2026-09-28).

The website admin used to be its own app at `/admin` on the site, with its own
password and cookie. It now lives in the back office's Website group, so this area
forwards `/api/website/<path>` to the site API's `/api/admin/<path>` for anyone
signed in here -- one password, one app.

The site API is a separate process (`site/backend`, `sashasite`) that shares only the
SQLite file; it stays the owner of bookings, messages, events, photos and café
settings. Nothing here reads those tables. The forward carries:

* `X-Site-Service-Key`: the shared secret (`SITE_SERVICE_KEY`) the site accepts in
  place of its cookie. Without it configured, every call answers 503 and says so.
* `X-Admin: 1`: the site's CSRF header. The back office authenticates with a bearer
  header, not a cookie, so there is no cross-site form to guard against here.
* `X-Forwarded-For`: the browser's address, for the site's audit rows and limits.

The site's own sign-in, sign-out and password routes are refused: the back office's
password is the one in force, and a second password would be a second way in.

Photos are public on the site, and an `<img>` cannot send a bearer header, so the
library's files come through `open_router` (`/api/website-media/*`) without auth.
"""

from __future__ import annotations

import logging
from urllib.parse import quote

import httpx
from fastapi import APIRouter, HTTPException, Request, status
from fastapi.responses import Response

from cafeops.api.schemas import Out
from cafeops.api.security import ApiAuth, client_ip
from cafeops.config import settings

log = logging.getLogger(__name__)

router = APIRouter(prefix="/api/website", tags=["website"], dependencies=[ApiAuth])
open_router = APIRouter(prefix="/api/website-media", tags=["website"])

#: The site's own auth routes. The back office's password is the one in force.
_REFUSED = frozenset({"login", "logout", "password"})
#: Headers worth passing back to the browser. Hop-by-hop and length headers are
#: recomputed by the response itself.
_PASS_BACK = ("content-type", "cache-control", "etag", "last-modified")
_TIMEOUT = httpx.Timeout(30.0, connect=5.0)


def _base() -> str:
    return settings.site_api_url.rstrip("/")


def _not_configured() -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
        detail=(
            "The website isn't connected to the back office yet. Set SITE_SERVICE_KEY "
            "in .env to the same long random value for both apps, then restart them."
        ),
    )


def _unreachable(exc: Exception) -> HTTPException:
    """502. The site API's internal address goes to the log, not the body: the media
    route is unauthenticated, and a stranger has no use for the box's topology."""
    log.warning("site API unreachable at %s: %s", _base(), exc)
    return HTTPException(
        status_code=status.HTTP_502_BAD_GATEWAY,
        detail="The website's server isn't answering. Is it running?",
    )


def _passed_back(upstream: httpx.Response) -> Response:
    headers = {k: v for k in _PASS_BACK if (v := upstream.headers.get(k)) is not None}
    if upstream.status_code == status.HTTP_204_NO_CONTENT:
        return Response(status_code=204, headers=headers)
    return Response(content=upstream.content, status_code=upstream.status_code, headers=headers)


def _requote(path: str) -> str:
    """The decoded path FastAPI hands us, quoted again for the upstream URL.

    A photo or menu name with ``?``, ``#`` or ``%`` arrives decoded; pasted raw into
    the URL it would become a query, a fragment or a bad escape. Dot segments are
    refused so a forward can never climb out of the prefix it was given."""
    if any(seg in (".", "..") for seg in path.split("/")):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND)
    return quote(path, safe="/")


async def _forward(request: Request, upstream_path: str) -> Response:
    key = settings.site_service_key
    if not key:
        raise _not_configured()
    headers = {
        "X-Site-Service-Key": key,
        "X-Admin": "1",
        "X-Forwarded-For": client_ip(request),
        "Accept": "application/json",
    }
    content_type = request.headers.get("content-type")
    if content_type:
        # Multipart uploads keep their boundary this way.
        headers["Content-Type"] = content_type
    body = await request.body()
    try:
        async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
            upstream = await client.request(
                request.method,
                f"{_base()}{upstream_path}",
                params=request.query_params,
                content=body or None,
                headers=headers,
            )
    except httpx.HTTPError as exc:
        raise _unreachable(exc) from exc
    if upstream.status_code == status.HTTP_401_UNAUTHORIZED:
        # Our sign-in passed, so a 401 from the site means the key is wrong. Passing
        # a 401 back would sign the owner out of the back office for the site's fault.
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=(
                "The website refused the back office's key. SITE_SERVICE_KEY must be the "
                "same for both apps; restart both after changing it."
            ),
        )
    return _passed_back(upstream)


class WebsiteConnectionOut(Out):
    configured: bool
    reachable: bool
    public_url: str


@router.get("/connection", response_model=WebsiteConnectionOut)
async def connection() -> WebsiteConnectionOut:
    """Is the website wired up? Answers without failing, for a banner."""
    configured = bool(settings.site_service_key)
    reachable = False
    if configured:
        try:
            async with httpx.AsyncClient(timeout=httpx.Timeout(3.0)) as client:
                reachable = (await client.get(f"{_base()}/api/health")).is_success
        except httpx.HTTPError:
            reachable = False
    return WebsiteConnectionOut(
        configured=configured,
        reachable=reachable,
        public_url=settings.site_public_url.rstrip("/"),
    )


@router.get("/slots")
async def slots(request: Request) -> Response:
    """Every photo place on the site. Public on the site; read here for the Photos screen.

    The site caches this for 30 s for visitors; the editor must see its own save at once."""
    response = await _forward(request, "/api/slots")
    response.headers["Cache-Control"] = "no-store"
    return response


@router.api_route("/{path:path}", methods=["GET", "POST", "PUT", "PATCH", "DELETE"])
async def admin(path: str, request: Request) -> Response:
    if path.split("/", 1)[0] in _REFUSED:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="The website uses the back office's sign-in; change it in Settings.",
        )
    return await _forward(request, f"/api/admin/{_requote(path)}")


@open_router.get("/{path:path}")
async def media(path: str) -> Response:
    """A photo from the site's library (public on the site anyway)."""
    try:
        async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
            upstream = await client.get(f"{_base()}/api/media-files/{_requote(path)}")
    except httpx.HTTPError as exc:
        raise _unreachable(exc) from exc
    return _passed_back(upstream)
