"""Reading a raw upload body without trusting the client about its size.

Every photo route takes the image as the raw request body. Checking `Content-Length`
alone is not a limit: the header is optional (chunked transfer sends none) and it is the
client's claim, and `await request.body()` then reads whatever arrives into memory. This
streams the body and stops at the cap, so a 2 GB upload costs at most `max_bytes` plus
one chunk.
"""

from __future__ import annotations

from fastapi import HTTPException, Request

from cafeops.services.media_store import MAX_BYTES

__all__ = ["PHOTO_TOO_LARGE", "read_bounded_body"]

#: The sentence the photo routes answer 413 with.
PHOTO_TOO_LARGE = "that photo is over 2 MB; resize it (1200px wide is plenty) and try again"


def _too_large(detail: str) -> HTTPException:
    return HTTPException(status_code=413, detail=detail)


async def read_bounded_body(
    request: Request, *, max_bytes: int = MAX_BYTES, detail: str = PHOTO_TOO_LARGE
) -> bytes:
    """The request body, or 413 with `detail` as soon as it is known to exceed `max_bytes`.

    A declared `Content-Length` over the cap is refused before reading anything; the
    stream is then counted regardless of what was declared.
    """
    declared = request.headers.get("content-length")
    if declared is not None and declared.isdigit() and int(declared) > max_bytes:
        raise _too_large(detail)
    chunks: list[bytes] = []
    size = 0
    async for chunk in request.stream():
        size += len(chunk)
        if size > max_bytes:
            raise _too_large(detail)
        chunks.append(chunk)
    return b"".join(chunks)
