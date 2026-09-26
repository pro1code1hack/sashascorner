"""In-memory sliding-window rate limit for POSTs, per client IP.

One process, one box (see cafeops CLAUDE.md §3): in-memory is sufficient, and a
restart resetting the counters is acceptable for a spam brake.
"""

from __future__ import annotations

import threading
import time
from collections import defaultdict, deque

from fastapi import HTTPException, Request

from sashasite.config import get_settings


def client_ip(request: Request) -> str:
    if get_settings().trust_proxy:
        fwd = request.headers.get("x-forwarded-for")
        if fwd:
            return fwd.split(",")[0].strip()
    return request.client.host if request.client else "unknown"


class SlidingWindow:
    """Per-IP sliding window with its own counters. The limit is read from
    settings on every call."""

    def __init__(self, count_attr: str, window_attr: str) -> None:
        self._count_attr = count_attr
        self._window_attr = window_attr
        self._lock = threading.Lock()
        self._hits: dict[str, deque[float]] = defaultdict(deque)

    def __call__(self, request: Request) -> None:
        s = get_settings()
        limit: int = getattr(s, self._count_attr)
        window: int = getattr(s, self._window_attr)
        ip = client_ip(request)
        now = time.monotonic()
        with self._lock:
            q = self._hits[ip]
            while q and now - q[0] >= window:
                q.popleft()
            if len(q) >= limit:
                retry = int(window - (now - q[0])) + 1
                raise HTTPException(
                    status_code=429,
                    detail="Too many requests — please try again in a few minutes.",
                    headers={"Retry-After": str(retry)},
                )
            q.append(now)
            if len(self._hits) > 10_000:  # bound memory against address spraying
                for k in [k for k, v in self._hits.items() if not v or now - v[-1] >= window]:
                    del self._hits[k]


#: Public form POSTs (bookings, contact).
rate_limit = SlidingWindow("rate_limit_count", "rate_limit_window_seconds")
#: Admin photo uploads: separate counters, looser limit.
upload_rate_limit = SlidingWindow("upload_rate_limit_count", "upload_rate_limit_window_seconds")
