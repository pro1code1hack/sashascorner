"""Async httpx client for Lightspeed Restaurant K-Series.

Fixtures-first (ARCHITECTURE.md 0, CLAUDE.md 1): every `lightspeed_*` setting in
`cafeops.config` is optional, and this module must never be reached by
`cafeops sync --fixtures`. It exists so a future `--live` sync has somewhere to
go, and so it *fails cleanly* -- a clear `LightspeedNotConfiguredError`, not a
network call -- when credentials are absent.

What this implements, and why:

* **OAuth2 refresh-token flow.** K-Series uses an authorization-code grant to
  mint the *first* access+refresh token pair (out of band, via the developer
  portal), then a `refresh_token` grant to mint new access tokens thereafter.
  Source: the authentication tutorial's Postman collection shows `client_id`,
  `client_secret`, `code` and `refresh_token` fields, and a distinct "Refresh
  Token" request
  (https://api-portal.lsk.lightspeed.app/quick-start/authentication/authentication-tutorial).
  The tutorial's own realm (`auth.lsk-demo.app`, a sandbox) is not a production
  URL, so `_token_url` is a best-effort default that MUST be confirmed against
  the merchant's actual OAuth realm before any live use -- flagged again in the
  final report as an open item, not asserted as fact here.
* **Retry with backoff.** No published K-Series rate-limit policy was found
  (see the final report). Absent one, this retries 429 and 5xx responses with
  exponential backoff, honouring `Retry-After` when the server sends it, up to
  `settings.lightspeed_max_retries`.
* **Rate limiting.** A simple leaky-bucket gate at
  `settings.lightspeed_rate_limit_per_second`, applied to every outbound
  request including token refreshes.
* **Pagination.** K-Series list responses page via a `nextPage` cursor
  (mirrored in the fixture payloads this integration also reads). `paginate()`
  follows it until exhausted.

Phase 3 hardening (`docs/phase3/agent-k-integrations.md` 2, ARCHITECTURE.md 8K).
Every item below is a failure this client would have met on its first real day and
did not survive, each reproduced from a recorded transport in `resilience.py` --
never from a live call:

* **A 401 mid-window refreshes and continues.** `expires_in` is the server's
  opinion of its own token, and a token can die earlier than advertised (a clock
  that disagrees, a revocation, a realm restart). The old code only refreshed on
  *local* expiry and treated 401 as fatal, so a token that expired on page 7 of 9
  aborted the window and the next run started from the beginning. Now a 401 clears
  the cached token, re-mints it **once** per request, and replays that one request.
  Once, deliberately: a 401 that survives a fresh token is a credential problem, and
  retrying it in a loop is how you get an account locked.
* **The token endpoint is retried too.** It is a network call like any other, and a
  429 on it used to kill a whole sync before a single page was read.
* **`Retry-After` is honoured but capped** (`_MAX_RETRY_AFTER`, 60s) and understood in
  both documented forms, seconds and an HTTP date. A cron job must not sleep for an
  hour because a proxy said `Retry-After: 3600`; it should give up and let the next
  run have the window, which the overlap in `jobs/daily_sync.py` makes free.
* **Pagination terminates.** A cursor that repeats, or a page that hands back the
  cursor it was fetched with, used to loop forever hammering the API -- the worst
  possible response to a server already in trouble. `paginate` now remembers the
  cursors it has followed, stops on the first repeat, and is capped at
  `_MAX_PAGES` regardless.
"""

from __future__ import annotations

import asyncio
import time
from collections.abc import AsyncIterator, Mapping
from datetime import UTC, datetime, timedelta
from email.utils import parsedate_to_datetime
from typing import Any

import httpx

from cafeops.config import Settings
from cafeops.config import settings as default_settings

__all__ = [
    "LightspeedAPIError",
    "LightspeedClient",
    "LightspeedNotConfiguredError",
    "LightspeedPaginationError",
]

#: Best-effort default. NOT confirmed for production -- see module docstring
#: and the "Open items" section of the final report. Override via
#: `LightspeedClient(token_url=...)` once a merchant's real OAuth realm is known.
_DEFAULT_TOKEN_URL = "https://auth.lsk.lightspeed.app/realms/k-series/protocol/openid-connect/token"

_RETRYABLE_STATUS = {429, 500, 502, 503, 504}
#: Longest we will obey a `Retry-After`. Beyond this, give up and let the next run
#: take the window -- `jobs/daily_sync.OVERLAP_DAYS` means nothing is lost by waiting.
_MAX_RETRY_AFTER = 60.0
#: Hard stop on pagination. 500 pages is far past any window this cafe can produce,
#: so reaching it means the cursor is not advancing and the server is misbehaving.
_MAX_PAGES = 500
#: Mint a new token this many seconds before the server says the old one dies.
_TOKEN_EXPIRY_SKEW_SECONDS = 30


class LightspeedNotConfiguredError(RuntimeError):
    """Raised when a live call is attempted without full credentials.

    Fixtures-first: this must be the *only* way `cafeops sync` without
    `--fixtures` fails in this environment, because no credentials are ever
    set here. Never let an unconfigured client silently no-op.
    """


class LightspeedAPIError(RuntimeError):
    """A request to the Lightspeed API failed after all retries."""

    def __init__(self, message: str, *, status_code: int | None = None) -> None:
        super().__init__(message)
        self.status_code = status_code


class LightspeedPaginationError(LightspeedAPIError):
    """The server's `nextPage` cursor did not advance, so paging was stopped.

    A distinct type because the caller's correct response differs: a 500 is worth
    retrying, a cursor that repeats forever is not, and the pages already yielded
    are still good data. `sync.py` treats it as a partial window and says so rather
    than discarding what it read.
    """


class _RateLimiter:
    """Leaky-bucket gate: at most `rate` requests per second, this process only."""

    def __init__(self, rate_per_second: float) -> None:
        self._min_interval = 1.0 / rate_per_second if rate_per_second > 0 else 0.0
        self._last_at: float | None = None
        self._lock = asyncio.Lock()

    async def wait(self) -> None:
        if self._min_interval <= 0:
            return
        async with self._lock:
            now = time.monotonic()
            if self._last_at is not None:
                elapsed = now - self._last_at
                remaining = self._min_interval - elapsed
                if remaining > 0:
                    await asyncio.sleep(remaining)
            self._last_at = time.monotonic()


class LightspeedClient:
    """Async client for the K-Series REST API. Construct freely; every network
    method raises `LightspeedNotConfiguredError` first if credentials are missing.
    """

    def __init__(
        self,
        settings: Settings | None = None,
        *,
        token_url: str | None = None,
        transport: httpx.AsyncBaseTransport | None = None,
        timeout: float = 15.0,
        backoff_scale: float = 1.0,
    ) -> None:
        self._settings = settings or default_settings
        self._token_url = token_url or _DEFAULT_TOKEN_URL
        self._http = httpx.AsyncClient(
            base_url=self._settings.lightspeed_base_url,
            timeout=timeout,
            transport=transport,
        )
        self._rate_limiter = _RateLimiter(self._settings.lightspeed_rate_limit_per_second)
        self._access_token: str | None = None
        self._access_token_expires_at: datetime | None = None
        #: Multiplier on every computed backoff and every honoured `Retry-After`.
        #: 1.0 in production. `resilience.py` sets it to 0 so the recorded scenarios
        #: exercise the *decisions* -- how many attempts, which refresh, when to stop
        #: paging -- in milliseconds instead of minutes. It scales the delay, never
        #: the number of attempts, so nothing about the control flow changes.
        self._backoff_scale = max(backoff_scale, 0.0)
        #: The last `Retry-After` this client read, in seconds, or None if the server
        #: sent none. Observability only; nothing branches on it.
        self.last_retry_after: float | None = None

    # -- configuration ------------------------------------------------------

    @property
    def configured(self) -> bool:
        """True only when every credential a live call needs is present.

        Stricter than `settings.lightspeed_configured` (which only checks
        client id/secret, for the CLI's "fixtures only" banner) -- a refresh
        flow additionally needs the refresh token and the business id.
        """
        s = self._settings
        return bool(
            s.lightspeed_client_id
            and s.lightspeed_client_secret
            and s.lightspeed_refresh_token
            and s.lightspeed_business_id
        )

    def _require_configured(self) -> None:
        if self.configured:
            return
        s = self._settings
        missing = [
            name
            for name, value in (
                ("lightspeed_client_id", s.lightspeed_client_id),
                ("lightspeed_client_secret", s.lightspeed_client_secret),
                ("lightspeed_refresh_token", s.lightspeed_refresh_token),
                ("lightspeed_business_id", s.lightspeed_business_id),
            )
            if not value
        ]
        raise LightspeedNotConfiguredError(
            "Lightspeed is not configured (fixtures only): missing "
            f"{', '.join(missing)}. Set CAFEOPS_LIGHTSPEED_* in .env, or run "
            "with --fixtures."
        )

    async def aclose(self) -> None:
        await self._http.aclose()

    async def __aenter__(self) -> LightspeedClient:
        return self

    async def __aexit__(self, *exc: object) -> None:
        await self.aclose()

    # -- OAuth2 refresh-token flow -------------------------------------------

    async def _ensure_access_token(self, *, force: bool = False) -> str:
        """The cached access token, minting a new one when it is (or looks) dead.

        `force=True` is the 401 path: the server has told us the token is no good
        regardless of what `expires_in` claimed, so the cache must be discarded
        rather than trusted for another `expires_in` seconds.
        """
        self._require_configured()
        if force:
            self._access_token = None
            self._access_token_expires_at = None
        now = datetime.now(UTC)
        token, expires_at = self._access_token, self._access_token_expires_at
        if token and expires_at and now < expires_at:
            return token

        payload = await self._post_token()
        access_token = payload.get("access_token")
        expires_in = payload.get("expires_in", 300)
        if not access_token:
            raise LightspeedAPIError("token refresh response had no access_token")
        access_token = str(access_token)
        try:
            lifetime = int(expires_in)
        except (TypeError, ValueError):
            # A token whose lifetime we cannot read is treated as short-lived rather
            # than as invalid: the token itself is probably fine, and the next call
            # re-minting one costs a request. Guessing it lasts an hour does not.
            lifetime = 300
        self._access_token = access_token
        # Refresh a little early so an in-flight request never races expiry, and never
        # trust a lifetime so short that the skew below would make it negative.
        self._access_token_expires_at = datetime.now(UTC) + timedelta(
            seconds=max(lifetime - _TOKEN_EXPIRY_SKEW_SECONDS, 30)
        )
        return access_token

    async def _post_token(self) -> dict[str, Any]:
        """POST the refresh grant, with the same retry policy as any other call.

        The token endpoint used to be the one unretried request in this client, which
        made a single 429 on it fatal to a whole night's sync. A 4xx that is not 429
        is still fatal, and should be: a rejected refresh token is a credential
        problem, and hammering it is how an account gets locked.
        """
        s = self._settings
        data = {
            "grant_type": "refresh_token",
            "refresh_token": s.lightspeed_refresh_token,
            "client_id": s.lightspeed_client_id,
            "client_secret": s.lightspeed_client_secret,
        }
        attempt = 0
        max_attempts = self._max_attempts
        last: str = "no attempt was made"
        while attempt < max_attempts:
            attempt += 1
            await self._rate_limiter.wait()
            try:
                response = await self._http.post(self._token_url, data=data)
            except httpx.TransportError as exc:
                last = f"{type(exc).__name__}: {exc}"
                if attempt >= max_attempts:
                    break
                await asyncio.sleep(self._backoff(attempt))
                continue
            if response.status_code < 400:
                body: dict[str, Any] = response.json()
                return body
            last = f"HTTP {response.status_code} {response.text[:200]}"
            if response.status_code not in _RETRYABLE_STATUS or attempt >= max_attempts:
                raise LightspeedAPIError(
                    f"token refresh failed: {last}", status_code=response.status_code
                )
            await asyncio.sleep(self._retry_delay(response, attempt))
        raise LightspeedAPIError(f"token refresh failed after {max_attempts} attempt(s): {last}")

    @property
    def _max_attempts(self) -> int:
        """`lightspeed_max_retries` is read as a total attempt count, not retries on
        top of a first try. Named as it is read so nobody has to work it out twice."""
        return max(self._settings.lightspeed_max_retries, 1)

    def _backoff(self, attempt: int) -> float:
        return _backoff_seconds(attempt) * self._backoff_scale

    def _retry_delay(self, response: httpx.Response, attempt: int) -> float:
        """How long to wait before the next attempt, and where `Retry-After` wins.

        Also records the decision on `last_retry_after` so the resilience harness can
        show that a `Retry-After` was read and obeyed rather than merely tolerated --
        a header that is silently ignored looks identical to one that is honoured.
        """
        retry_after = _parse_retry_after(response.headers.get("Retry-After"))
        self.last_retry_after = retry_after
        if retry_after is None:
            return self._backoff(attempt)
        return min(retry_after, _MAX_RETRY_AFTER) * self._backoff_scale

    # -- request plumbing: retry, backoff, rate limiting ---------------------

    async def _request(
        self, method: str, path: str, *, params: Mapping[str, Any] | None = None
    ) -> httpx.Response:
        """One request, with retries, backoff and a single mid-flight token refresh.

        The token is fetched **inside** the loop, not once above it: a retry can be
        preceded by up to `_MAX_RETRY_AFTER` seconds of sleep, and a token that was
        valid when the loop began need not be valid when the last attempt goes out.
        """
        self._require_configured()
        business_id = self._settings.lightspeed_business_id
        query = dict(params or {})
        query.setdefault("businessId", business_id)

        attempt = 0
        max_attempts = self._max_attempts
        refreshed_on_401 = False
        last_error: str = "no attempt was made"
        while attempt < max_attempts:
            attempt += 1
            token = await self._ensure_access_token()
            headers = {"Authorization": f"Bearer {token}", "Accept": "application/json"}
            await self._rate_limiter.wait()
            try:
                response = await self._http.request(method, path, params=query, headers=headers)
            except httpx.TransportError as exc:
                # A connection reset mid-page is retried, not fatal: the page has no
                # side effects, so re-reading it is free and losing the window is not.
                last_error = f"{type(exc).__name__}: {exc}"
                if attempt >= max_attempts:
                    break
                await asyncio.sleep(self._backoff(attempt))
                continue

            if response.status_code == 401 and not refreshed_on_401:
                # The token died earlier than `expires_in` promised. Mint a fresh one
                # and replay THIS request -- do not restart the window, and do not
                # count this against `max_attempts`, because nothing was wrong with
                # the request itself.
                refreshed_on_401 = True
                attempt -= 1
                await self._ensure_access_token(force=True)
                continue

            if response.status_code not in _RETRYABLE_STATUS:
                if response.status_code >= 400:
                    raise LightspeedAPIError(
                        f"{method} {path} failed: HTTP {response.status_code} "
                        f"{response.text[:200]}",
                        status_code=response.status_code,
                    )
                return response

            last_error = f"HTTP {response.status_code}"
            if attempt >= max_attempts:
                break
            # Sleep only when there is another attempt to sleep before. The old code
            # slept after the final one, which added up to 30s to every failed cron run
            # and bought nothing.
            await asyncio.sleep(self._retry_delay(response, attempt))

        raise LightspeedAPIError(
            f"{method} {path} failed after {max_attempts} attempt(s): {last_error}"
        )

    # -- pagination -----------------------------------------------------------

    async def paginate(
        self, path: str, *, params: Mapping[str, Any] | None = None
    ) -> AsyncIterator[dict[str, Any]]:
        """Yield every page's JSON body, following a `nextPage` cursor.

        Termination is guaranteed three ways, because "the cursor stopped advancing"
        is a normal server bug and an infinite loop against an API already in trouble
        is the worst available response:

        1. no `nextPage` -- the ordinary end;
        2. a cursor already followed (including one identical to the page's own) --
           raises `LightspeedPaginationError` *after* the good pages have been
           yielded, so the caller keeps what it read and is told the window is short;
        3. `_MAX_PAGES`, unconditionally.
        """
        query = dict(params or {})
        seen: set[str] = set()
        current: str | None = None
        for page_no in range(1, _MAX_PAGES + 1):
            response = await self._request("GET", path, params=query)
            body: dict[str, Any] = response.json()
            yield body
            raw_next = body.get("nextPage")
            if not raw_next:
                return
            next_page = str(raw_next)
            if next_page == current or next_page in seen:
                raise LightspeedPaginationError(
                    f"GET {path}: page {page_no} handed back cursor {next_page!r}, which "
                    "has already been followed. Paging stopped rather than looping; the "
                    f"{page_no} page(s) already read are good, the rest of the window is "
                    "not. Re-run once the API is behaving -- the sync is idempotent."
                )
            seen.add(next_page)
            current = next_page
            query = {**query, "page": next_page}
        raise LightspeedPaginationError(
            f"GET {path}: stopped after {_MAX_PAGES} pages with a cursor still pending. "
            "Either the window is implausibly large or the cursor is not advancing."
        )

    # -- endpoints used by sync.py --------------------------------------------

    async def get_sales(self, *, since: str, until: str) -> AsyncIterator[dict[str, Any]]:
        """Financial/reporting sales for a date window.

        NOTE (research finding, see the final report): this endpoint shape
        (`Get Sales` / `Get business day sales`) does not carry modifier data
        in the documented schema. A live sync that needs modifiers -- which it
        does, for oat milk -- cannot rely on this endpoint alone.
        """
        async for page in self.paginate("/sales", params={"from": since, "to": until}):
            yield page

    async def get_open_checks(self) -> AsyncIterator[dict[str, Any]]:
        """Real-time operational endpoint. Documented to carry `modifiers`
        (name + quantity only, no id -- see the final report) per line.
        """
        async for page in self.paginate("/checks"):
            yield page

    async def get_items(self) -> AsyncIterator[dict[str, Any]]:
        async for page in self.paginate("/items"):
            yield page


def _backoff_seconds(attempt: int) -> float:
    """Exponential backoff, capped, with a little jitter-free simplicity --
    this café does ~30 requests/day of sync traffic; a fancier jitter scheme
    buys nothing at this volume."""
    return min(2.0 ** (attempt - 1), 30.0)


def _parse_retry_after(raw: str | None) -> float | None:
    """`Retry-After` in either documented form: delta-seconds, or an HTTP date.

    RFC 9110 allows both and real proxies send both. The old code read only the
    integer form and silently fell back to its own backoff on a date -- which is
    survivable, but it meant a server saying "come back in 40 seconds" was answered
    after 1. Returned uncapped; the caller applies `_MAX_RETRY_AFTER`.
    """
    if raw is None:
        return None
    text = raw.strip()
    if not text:
        return None
    try:
        return max(float(text), 0.0)
    except ValueError:
        pass
    try:
        when = parsedate_to_datetime(text)
    except (TypeError, ValueError):
        return None
    if when.tzinfo is None:
        when = when.replace(tzinfo=UTC)
    return max((when - datetime.now(UTC)).total_seconds(), 0.0)
