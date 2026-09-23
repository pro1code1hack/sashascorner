"""Recorded-transport scenarios: what the POS edge does on a bad day.

**Nothing here opens a socket.** Every scenario is an `httpx.MockTransport` serving
responses written down in this file, so the client's decisions -- how many attempts,
when to re-mint a token, whether paging terminates -- are exercised against a server
that misbehaves on purpose. That is the only honest way to test this edge today: no
Lightspeed credentials exist, and `docs/phase3/agent-k-integrations.md` forbids a live
call outright.

Why a harness and not tests: §1 of `ARCHITECTURE.md` and the brief both say this
project has no test suite. So the scenarios are a **command** -- `cafeops pos probe`
-- whose output a human reads. That is a weaker guarantee than a red build, and it is
recorded as such; what it does buy is that the failure modes are written down
somewhere executable instead of somewhere hopeful.

Credentials: `RECORDED_SETTINGS` fills the four `lightspeed_*` fields with strings
that are visibly fake and points `lightspeed_base_url` at a `.invalid` host, which by
RFC 6761 can never resolve. So even if the mock transport were removed by accident,
these scenarios still cannot reach Lightspeed. The process's real settings are never
consulted -- if a credential ever does appear in this environment, this file will not
pick it up and must not.
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable, Iterator, Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any

import httpx

from cafeops.config import Settings
from cafeops.integrations.lightspeed.client import (
    LightspeedAPIError,
    LightspeedClient,
    LightspeedPaginationError,
)

__all__ = [
    "SCENARIOS",
    "RecordedResponse",
    "RecordedServer",
    "Scenario",
    "ScenarioResult",
    "run_scenarios",
]

#: A visibly fake credential set against an unresolvable host. See module docstring.
RECORDED_SETTINGS = Settings(
    lightspeed_base_url="https://recorded.invalid/v3",
    lightspeed_client_id="RECORDED-NOT-A-CREDENTIAL",
    lightspeed_client_secret="RECORDED-NOT-A-CREDENTIAL",
    lightspeed_refresh_token="RECORDED-NOT-A-CREDENTIAL",
    lightspeed_business_id="RECORDED-BUSINESS",
    # 0 disables the leaky bucket. The gate is real code and worth keeping in
    # production; making the harness wait 200ms per request to re-prove that would
    # only discourage anyone from running it.
    lightspeed_rate_limit_per_second=0.0,
    lightspeed_max_retries=4,
)

_TOKEN_PATH = "/realms/recorded/protocol/openid-connect/token"
_TOKEN_URL = f"https://recorded.invalid{_TOKEN_PATH}"
#: `lightspeed_base_url` carries the `/v3` prefix, so the recorded server is keyed on
#: the full path the client actually requests -- not on the relative path in `client.py`.
_SALES_PATH = "/v3/sales"


@dataclass(frozen=True, slots=True)
class RecordedResponse:
    """One response the recorded server will hand back.

    `reset=True` records a connection reset instead of a response -- the mid-page
    failure the brief asks for, which is not a status code at all.
    """

    status: int = 200
    body: Mapping[str, Any] = field(default_factory=dict)
    headers: Mapping[str, str] = field(default_factory=dict)
    reset: bool = False

    def describe(self) -> str:
        if self.reset:
            return "connection reset"
        retry_after = self.headers.get("Retry-After")
        suffix = f" Retry-After: {retry_after}" if retry_after else ""
        return f"{self.status}{suffix}"


class RecordedServer:
    """A scripted server. Each path gets a queue; the last entry repeats forever.

    The last entry repeating is deliberate: a script that runs dry mid-scenario would
    fail with a confusing `StopIteration` from inside httpx, and "the server keeps
    doing the same broken thing" is what a real outage looks like anyway.
    """

    def __init__(
        self,
        *,
        token: Sequence[RecordedResponse] | None = None,
        paths: Mapping[str, Sequence[RecordedResponse]] | None = None,
    ) -> None:
        self._token = list(token or [RecordedResponse(body=_token_body())])
        self._paths = {path: list(responses) for path, responses in (paths or {}).items()}
        #: Every request the client made, in order: (method, path, page cursor or "").
        self.calls: list[tuple[str, str, str]] = []
        self._served: dict[str, int] = {}

    @property
    def token_calls(self) -> int:
        return sum(1 for method, path, _ in self.calls if path == _TOKEN_PATH and method == "POST")

    def transport(self) -> httpx.MockTransport:
        return httpx.MockTransport(self._handle)

    def _handle(self, request: httpx.Request) -> httpx.Response:
        path = request.url.path
        cursor = request.url.params.get("page", "")
        self.calls.append((request.method, path, cursor))
        script = self._token if path == _TOKEN_PATH else self._paths.get(path, [])
        if not script:
            return httpx.Response(404, json={"error": f"nothing recorded for {path}"})
        index = min(self._served.get(path, 0), len(script) - 1)
        self._served[path] = index + 1
        recorded = script[index]
        if recorded.reset:
            raise httpx.ReadError("recorded connection reset", request=request)
        return httpx.Response(
            recorded.status, json=dict(recorded.body), headers=dict(recorded.headers)
        )

    def script_lines(self) -> list[str]:
        """What this server was told to do, for the report."""
        out = [f"token: {', '.join(r.describe() for r in self._token)}"]
        out += [
            f"{path}: {', '.join(r.describe() for r in responses)}"
            for path, responses in self._paths.items()
        ]
        return out


def _token_body(*, expires_in: int = 3600, token: str = "recorded-access-token") -> dict[str, Any]:
    return {"access_token": token, "expires_in": expires_in, "token_type": "Bearer"}


def _sales_page(*, receipt_id: str, next_page: str | None = None) -> dict[str, Any]:
    page: dict[str, Any] = {
        "receipts": [
            {
                "id": receipt_id,
                "closedAt": "2026-09-16T08:32:00Z",
                "channel": "EPOS",
                "lines": [
                    {
                        "id": f"{receipt_id}-L1",
                        "itemId": "LSK-ITEM-0007",
                        "name": "Black Americano",
                        "size": "M",
                        "quantity": "1",
                        "totalAmountPence": 310,
                    }
                ],
            }
        ]
    }
    if next_page is not None:
        page["nextPage"] = next_page
    return page


# ==========================================================================
# Scenarios
# ==========================================================================


@dataclass(frozen=True, slots=True)
class ScenarioResult:
    name: str
    why: str
    server_script: tuple[str, ...]
    outcome: tuple[str, ...]
    requests: int
    token_mints: int
    passed: bool
    verdict: str


Driver = Callable[[LightspeedClient, RecordedServer], Awaitable[tuple[list[str], bool, str]]]


@dataclass(frozen=True, slots=True)
class Scenario:
    name: str
    why: str
    build: Callable[[], RecordedServer]
    drive: Driver


async def _collect_receipt_ids(client: LightspeedClient) -> list[str]:
    ids: list[str] = []
    async for page in client.get_sales(since="2026-09-16", until="2026-09-16"):
        ids.extend(str(r.get("id")) for r in page.get("receipts", []))
    return ids


async def _drive_happy(
    client: LightspeedClient, server: RecordedServer
) -> tuple[list[str], bool, str]:
    ids = await _collect_receipt_ids(client)
    ok = ids == ["R-1", "R-2", "R-3"]
    return (
        [f"read {len(ids)} receipt(s) across 3 pages: {', '.join(ids)}"],
        ok,
        "three pages followed, cursor exhausted, one token minted",
    )


async def _drive_token_expiry(
    client: LightspeedClient, server: RecordedServer
) -> tuple[list[str], bool, str]:
    ids = await _collect_receipt_ids(client)
    lines = [
        f"read {len(ids)} receipt(s): {', '.join(ids)}",
        f"access tokens minted: {server.token_calls} (one at the start, one after the 401)",
    ]
    ok = ids == ["R-1", "R-2", "R-3"] and server.token_calls == 2
    return (
        lines,
        ok,
        "refreshed mid-window on page 2 and carried on from page 2 -- the window "
        "was NOT restarted, and page 1 was read once",
    )


async def _drive_429_with_header(
    client: LightspeedClient, server: RecordedServer
) -> tuple[list[str], bool, str]:
    ids = await _collect_receipt_ids(client)
    lines = [
        f"read {len(ids)} receipt(s) after the throttle cleared",
        f"Retry-After read from the header: {client.last_retry_after}s (obeyed, capped at 60s)",
    ]
    ok = ids == ["R-1"] and client.last_retry_after == 12.0
    return lines, ok, "the server's own 12s was used instead of our 1s backoff"


async def _drive_429_no_header(
    client: LightspeedClient, server: RecordedServer
) -> tuple[list[str], bool, str]:
    ids = await _collect_receipt_ids(client)
    lines = [
        f"read {len(ids)} receipt(s) after two throttled attempts",
        f"Retry-After: {client.last_retry_after} -- none sent, so exponential backoff was used",
    ]
    ok = ids == ["R-1"] and client.last_retry_after is None
    return lines, ok, "no header is not a reason to give up, and not a reason to hammer"


async def _drive_retry_after_absurd(
    client: LightspeedClient, server: RecordedServer
) -> tuple[list[str], bool, str]:
    ids = await _collect_receipt_ids(client)
    lines = [
        f"server asked for {client.last_retry_after}s; capped at {60.0}s before sleeping",
        f"read {len(ids)} receipt(s)",
    ]
    ok = ids == ["R-1"] and client.last_retry_after == 3600.0
    return (
        lines,
        ok,
        "a cron job must not sleep for an hour; the overlap window makes waiting free",
    )


async def _drive_500_then_ok(
    client: LightspeedClient, server: RecordedServer
) -> tuple[list[str], bool, str]:
    ids = await _collect_receipt_ids(client)
    return (
        [f"500 on attempt 1, 200 on attempt 2; read {len(ids)} receipt(s)"],
        ids == ["R-1"],
        "a transient 5xx costs a retry, not the night's sync",
    )


async def _drive_reset_mid_page(
    client: LightspeedClient, server: RecordedServer
) -> tuple[list[str], bool, str]:
    ids = await _collect_receipt_ids(client)
    return (
        [f"connection reset on page 2, retried, read {len(ids)} receipt(s): {', '.join(ids)}"],
        ids == ["R-1", "R-2"],
        "a reset is retried at the page that failed; GET has no side effects to undo",
    )


async def _drive_repeating_cursor(
    client: LightspeedClient, server: RecordedServer
) -> tuple[list[str], bool, str]:
    # Collected page by page rather than via `_collect_receipt_ids`, because the point
    # of the refusal is that the pages read BEFORE it are kept. Binding the whole list
    # after the generator raised would report zero and hide exactly that.
    ids: list[str] = []
    try:
        async for page in client.get_sales(since="2026-09-16", until="2026-09-16"):
            ids.extend(str(r.get("id")) for r in page.get("receipts", []))
    except LightspeedPaginationError as exc:
        return (
            [f"kept {len(ids)} page(s) already read, then STOPPED", f"refusal: {exc}"],
            server.calls.count(("GET", _SALES_PATH, "CURSOR-SAME")) <= 2,
            "the cursor repeated; paging stopped instead of looping forever",
        )
    return [f"read {len(ids)} receipt(s)"], False, "NO REFUSAL -- pagination did not terminate"


async def _drive_missing_cursor(
    client: LightspeedClient, server: RecordedServer
) -> tuple[list[str], bool, str]:
    ids = await _collect_receipt_ids(client)
    return (
        [f"page 2 omitted nextPage; stopped there with {len(ids)} receipt(s)"],
        ids == ["R-1", "R-2"],
        "an absent cursor is the ordinary end of a window, not an error",
    )


async def _drive_token_throttled(
    client: LightspeedClient, server: RecordedServer
) -> tuple[list[str], bool, str]:
    ids = await _collect_receipt_ids(client)
    return (
        [f"token endpoint 429'd once, retried, then read {len(ids)} receipt(s)"],
        ids == ["R-1"],
        "the token endpoint is a network call like any other",
    )


async def _drive_bad_credentials(
    client: LightspeedClient, server: RecordedServer
) -> tuple[list[str], bool, str]:
    try:
        await _collect_receipt_ids(client)
    except LightspeedAPIError as exc:
        return (
            [f"stopped at once: {exc}"],
            server.token_calls == 1,
            "a rejected refresh token is not retried -- hammering it locks the account",
        )
    return [], False, "NO REFUSAL -- a 400 from the token endpoint was swallowed"


async def _drive_persistent_401(
    client: LightspeedClient, server: RecordedServer
) -> tuple[list[str], bool, str]:
    try:
        await _collect_receipt_ids(client)
    except LightspeedAPIError as exc:
        return (
            [f"one refresh, then gave up: {exc}", f"tokens minted: {server.token_calls}"],
            server.token_calls == 2,
            "a 401 that survives a fresh token is a permissions problem, not a retry",
        )
    return [], False, "NO REFUSAL -- a permanent 401 looped or was ignored"


SCENARIOS: tuple[Scenario, ...] = (
    Scenario(
        name="happy-path-pagination",
        why="The baseline. Three pages, one token, cursor exhausted normally.",
        build=lambda: RecordedServer(
            paths={
                _SALES_PATH: [
                    RecordedResponse(body=_sales_page(receipt_id="R-1", next_page="P2")),
                    RecordedResponse(body=_sales_page(receipt_id="R-2", next_page="P3")),
                    RecordedResponse(body=_sales_page(receipt_id="R-3")),
                ]
            }
        ),
        drive=_drive_happy,
    ),
    Scenario(
        name="token-expires-mid-window",
        why="A window that spans a token expiry: refresh and continue, do NOT restart.",
        build=lambda: RecordedServer(
            token=[
                RecordedResponse(body=_token_body(token="first")),
                RecordedResponse(body=_token_body(token="second")),
            ],
            paths={
                _SALES_PATH: [
                    RecordedResponse(body=_sales_page(receipt_id="R-1", next_page="P2")),
                    RecordedResponse(status=401, body={"error": "token expired"}),
                    RecordedResponse(body=_sales_page(receipt_id="R-2", next_page="P3")),
                    RecordedResponse(body=_sales_page(receipt_id="R-3")),
                ]
            },
        ),
        drive=_drive_token_expiry,
    ),
    Scenario(
        name="429-with-retry-after",
        why="Throttled, and the server said how long to wait. Obey it, up to 60s.",
        build=lambda: RecordedServer(
            paths={
                _SALES_PATH: [
                    RecordedResponse(status=429, headers={"Retry-After": "12"}),
                    RecordedResponse(body=_sales_page(receipt_id="R-1")),
                ]
            }
        ),
        drive=_drive_429_with_header,
    ),
    Scenario(
        name="429-without-retry-after",
        why="Throttled with no header at all -- fall back to exponential backoff.",
        build=lambda: RecordedServer(
            paths={
                _SALES_PATH: [
                    RecordedResponse(status=429),
                    RecordedResponse(status=429),
                    RecordedResponse(body=_sales_page(receipt_id="R-1")),
                ]
            }
        ),
        drive=_drive_429_no_header,
    ),
    Scenario(
        name="retry-after-an-hour",
        why="A proxy asking for 3600s. Cap it; the next run's overlap loses nothing.",
        build=lambda: RecordedServer(
            paths={
                _SALES_PATH: [
                    RecordedResponse(status=503, headers={"Retry-After": "3600"}),
                    RecordedResponse(body=_sales_page(receipt_id="R-1")),
                ]
            }
        ),
        drive=_drive_retry_after_absurd,
    ),
    Scenario(
        name="500-then-success",
        why="A transient server error that clears on the next attempt.",
        build=lambda: RecordedServer(
            paths={
                _SALES_PATH: [
                    RecordedResponse(status=500, body={"error": "internal"}),
                    RecordedResponse(body=_sales_page(receipt_id="R-1")),
                ]
            }
        ),
        drive=_drive_500_then_ok,
    ),
    Scenario(
        name="connection-reset-mid-page",
        why="The socket dies while page 2 is in flight.",
        build=lambda: RecordedServer(
            paths={
                _SALES_PATH: [
                    RecordedResponse(body=_sales_page(receipt_id="R-1", next_page="P2")),
                    RecordedResponse(reset=True),
                    RecordedResponse(body=_sales_page(receipt_id="R-2")),
                ]
            }
        ),
        drive=_drive_reset_mid_page,
    ),
    Scenario(
        name="cursor-repeats-forever",
        why="The commonest pagination bug there is. Must terminate, not loop.",
        build=lambda: RecordedServer(
            paths={
                _SALES_PATH: [
                    RecordedResponse(
                        body=_sales_page(receipt_id="R-1", next_page="CURSOR-SAME"),
                    ),
                    RecordedResponse(
                        body=_sales_page(receipt_id="R-2", next_page="CURSOR-SAME"),
                    ),
                ]
            }
        ),
        drive=_drive_repeating_cursor,
    ),
    Scenario(
        name="cursor-goes-missing",
        why="A page that simply omits nextPage. The ordinary end, not a failure.",
        build=lambda: RecordedServer(
            paths={
                _SALES_PATH: [
                    RecordedResponse(body=_sales_page(receipt_id="R-1", next_page="P2")),
                    RecordedResponse(body=_sales_page(receipt_id="R-2")),
                ]
            }
        ),
        drive=_drive_missing_cursor,
    ),
    Scenario(
        name="token-endpoint-throttled",
        why="A 429 on the OAuth refresh itself. Used to kill the whole sync.",
        build=lambda: RecordedServer(
            token=[
                RecordedResponse(status=429, headers={"Retry-After": "2"}),
                RecordedResponse(body=_token_body()),
            ],
            paths={_SALES_PATH: [RecordedResponse(body=_sales_page(receipt_id="R-1"))]},
        ),
        drive=_drive_token_throttled,
    ),
    Scenario(
        name="refresh-token-rejected",
        why="A 400 from the token endpoint is a credential problem. Stop, do not retry.",
        build=lambda: RecordedServer(
            token=[RecordedResponse(status=400, body={"error": "invalid_grant"})],
            paths={_SALES_PATH: [RecordedResponse(body=_sales_page(receipt_id="R-1"))]},
        ),
        drive=_drive_bad_credentials,
    ),
    Scenario(
        name="permanent-401",
        why="A 401 a fresh token does not fix. Refresh once, then give up.",
        build=lambda: RecordedServer(
            paths={_SALES_PATH: [RecordedResponse(status=401, body={"error": "forbidden"})]}
        ),
        drive=_drive_persistent_401,
    ),
)


def scenario(name: str) -> Scenario:
    for candidate in SCENARIOS:
        if candidate.name == name:
            return candidate
    raise LookupError(f"no recorded scenario named {name!r}")


async def _run_one(spec: Scenario) -> ScenarioResult:
    server = spec.build()
    client = LightspeedClient(
        RECORDED_SETTINGS,
        token_url=_TOKEN_URL,
        transport=server.transport(),
        # Exercise the decisions, not the wall clock. See `LightspeedClient.__init__`.
        backoff_scale=0.0,
    )
    try:
        outcome, passed, verdict = await spec.drive(client, server)
    except Exception as exc:  # a scenario that explodes is itself a result
        outcome, passed, verdict = (
            [f"{type(exc).__name__}: {exc}"],
            False,
            "UNEXPECTED -- the client raised something this scenario did not model",
        )
    finally:
        await client.aclose()
    return ScenarioResult(
        name=spec.name,
        why=spec.why,
        server_script=tuple(server.script_lines()),
        outcome=tuple(outcome),
        requests=len(server.calls),
        token_mints=server.token_calls,
        passed=passed,
        verdict=verdict,
    )


async def _run_all(specs: Sequence[Scenario]) -> list[ScenarioResult]:
    return [await _run_one(spec) for spec in specs]


def run_scenarios(names: Sequence[str] | None = None) -> list[ScenarioResult]:
    """Run every recorded scenario (or the named ones) and return the results.

    Synchronous on purpose: the CLI is synchronous and this is the only async thing
    it touches, exactly as `sync.py` does it.
    """
    specs = [scenario(n) for n in names] if names else list(SCENARIOS)
    return asyncio.run(_run_all(specs))


def iter_names() -> Iterator[str]:
    for spec in SCENARIOS:
        yield spec.name
