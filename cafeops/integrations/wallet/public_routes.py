"""Open image routes for the passes. No auth: Google fetches these by URL.

`GET /api/loyalty/strip/{stamps}-{required}@{scale}x.png?v=<art>&r=1` -- the stamp
strip; Google's heroImage points here, and the web card may too. `r=1` adds the
reward sticker; `v` is the sticker-art hash that makes a redraw a new URL.
`GET /api/loyalty/pass-assets/{name}` -- the programme logo Google's class points at
(and the other committed pass art, for the web card).

Nothing personal is in either: a strip is "5 of 8" and nothing else, so it can be cached
by anyone for as long as the artwork lasts. `immutable` is safe because a change to the
artwork ships with a deploy -- and Google re-fetches only when the URL changes, so a
redesign should also change the URL (add `?v=`), which the long cache then respects.
"""

from __future__ import annotations

import asyncio

from fastapi import APIRouter, HTTPException, Response

from cafeops.integrations.wallet.assets import PUBLIC_FILES, pass_asset
from cafeops.integrations.wallet.strips import SCALES, strip_png

router = APIRouter(prefix="/api/loyalty", tags=["loyalty-public"], include_in_schema=False)

CACHE = "public, max-age=31536000, immutable"


@router.get("/strip/{stamps}-{required}@{scale}x.png")
async def strip(stamps: int, required: int, scale: int, r: int = 0, v: str = "") -> Response:
    # `r=1`: a stamp-card reward is ready (the reward sticker). `v` is only a cache key
    # (`strip_version()`); any value serves the current art.
    if not (1 <= required <= 20 and 0 <= stamps <= required and scale in SCALES and r in (0, 1)):
        raise HTTPException(status_code=404)
    del v
    # First render of a size is ~100 ms of Pillow; keep it off the event loop.
    data = await asyncio.to_thread(strip_png, stamps, required, scale, bool(r))
    return Response(content=data, media_type="image/png", headers={"Cache-Control": CACHE})


@router.get("/pass-assets/{name}")
async def pass_asset_route(name: str) -> Response:
    if name not in PUBLIC_FILES:
        raise HTTPException(status_code=404)
    try:
        data = pass_asset(name)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404) from exc
    media = "image/svg+xml" if name.endswith(".svg") else "image/png"
    return Response(content=data, media_type=media, headers={"Cache-Control": CACHE})


__all__ = ["router"]
