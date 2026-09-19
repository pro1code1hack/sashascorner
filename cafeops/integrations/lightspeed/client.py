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
"""

from __future__ import annotations

import asyncio
import time
from collections.abc import AsyncIterator, Mapping
from datetime import UTC, datetime, timedelta
from typing import Any

import httpx

from cafeops.config import Settings
from cafeops.config import settings as default_settings

__all__ = [
    "LightspeedAPIError",
    "LightspeedClient",
    "LightspeedNotConfiguredError",
]

#: Best-effort default. NOT confirmed for production -- see module docstring
#: and the "Open items" section of the final report. Override via
#: `LightspeedClient(token_url=...)` once a merchant's real OAuth realm is known.
_DEFAULT_TOKEN_URL = "https://auth.lsk.lightspeed.app/realms/k-series/protocol/openid-connect/token"

_RETRYABLE_STATUS = {429, 500, 502, 503, 504}


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

    async def _ensure_access_token(self) -> str:
        self._require_configured()
        now = datetime.now(UTC)
        token, expires_at = self._access_token, self._access_token_expires_at
        if token and expires_at and now < expires_at:
            return token

        s = self._settings
        await self._rate_limiter.wait()
        response = await self._http.post(
            self._token_url,
            data={
                "grant_type": "refresh_token",
                "refresh_token": s.lightspeed_refresh_token,
                "client_id": s.lightspeed_client_id,
                "client_secret": s.lightspeed_client_secret,
            },
        )
        if response.status_code >= 400:
            raise LightspeedAPIError(
                f"token refresh failed: HTTP {response.status_code} {response.text[:200]}",
                status_code=response.status_code,
            )
        payload: dict[str, Any] = response.json()
        access_token = payload.get("access_token")
        expires_in = payload.get("expires_in", 300)
        if not access_token:
            raise LightspeedAPIError("token refresh response had no access_token")
        access_token = str(access_token)
        self._access_token = access_token
        # Refresh a little early so an in-flight request never races expiry.
        self._access_token_expires_at = now + timedelta(seconds=max(int(expires_in) - 30, 30))
        return access_token

    # -- request plumbing: retry, backoff, rate limiting ---------------------

    async def _request(
        self, method: str, path: str, *, params: Mapping[str, Any] | None = None
    ) -> httpx.Response:
        self._require_configured()
        token = await self._ensure_access_token()
        headers = {
            "Authorization": f"Bearer {token}",
            "Accept": "application/json",
        }
        business_id = self._settings.lightspeed_business_id
        query = dict(params or {})
        query.setdefault("businessId", business_id)

        attempt = 0
        max_attempts = max(self._settings.lightspeed_max_retries, 1)
        last_error: Exception | None = None
        while attempt < max_attempts:
            attempt += 1
            await self._rate_limiter.wait()
            try:
                response = await self._http.request(method, path, params=query, headers=headers)
            except httpx.TransportError as exc:
                last_error = exc
                await asyncio.sleep(_backoff_seconds(attempt))
                continue

            if response.status_code not in _RETRYABLE_STATUS:
                if response.status_code >= 400:
                    raise LightspeedAPIError(
                        f"{method} {path} failed: HTTP {response.status_code} "
                        f"{response.text[:200]}",
                        status_code=response.status_code,
                    )
                return response

            retry_after = _parse_retry_after(response.headers.get("Retry-After"))
            delay = retry_after if retry_after is not None else _backoff_seconds(attempt)
            await asyncio.sleep(delay)
            last_error = LightspeedAPIError(
                f"{method} {path} returned HTTP {response.status_code}",
                status_code=response.status_code,
            )

        raise LightspeedAPIError(
            f"{method} {path} failed after {max_attempts} attempt(s): {last_error}"
        )

    # -- pagination -----------------------------------------------------------

    async def paginate(
        self, path: str, *, params: Mapping[str, Any] | None = None
    ) -> AsyncIterator[dict[str, Any]]:
        """Yield every page's JSON body, following a `nextPage` cursor."""
        query = dict(params or {})
        while True:
            response = await self._request("GET", path, params=query)
            body: dict[str, Any] = response.json()
            yield body
            next_page = body.get("nextPage")
            if not next_page:
                return
            query = {**query, "page": next_page}

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
    if raw is None:
        return None
    try:
        return max(float(raw), 0.0)
    except ValueError:
        return None
