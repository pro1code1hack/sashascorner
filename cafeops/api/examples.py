"""Dump one real JSON response per endpoint, so the frontend can build against fixtures.

These are not hand-written samples. Each file is the **actual response** of the actual
app, obtained by driving it in-process over `httpx.ASGITransport` -- no network, no
server to start. A hand-written fixture is a document that drifts from the code silently;
this one cannot, because generating it runs the code.

That matters most for the shapes a UI is likely to get wrong: a `Cost` with `pence: null`,
a `Forecast` with `qty: null` and reasons, an order line carrying `cap_reason`. Those are
in the dump because they are in the seeded database, and if a refactor stopped emitting
them the diff would show it.

`index.json` lists what was written, with the status code and the query used, so a
frontend can see which endpoint each file answers and which ones returned an error.
"""

from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

__all__ = ["EXAMPLES", "Example", "dump_examples"]


@dataclass(frozen=True, slots=True)
class Example:
    name: str
    method: str
    path: str
    params: dict[str, Any] | None = None
    body: dict[str, Any] | None = None
    note: str = ""


#: One per endpoint, plus the variants that carry an invariant's edge shape. `params` is
#: chosen to keep each file small enough to read: the full `/api/stock` over 113
#: ingredients with a forecast each is a large document and a slow one, so the default
#: dump is tier A.
EXAMPLES: tuple[Example, ...] = (
    Example("health", "GET", "/api/health", note="Open, unauthenticated."),
    Example("meta", "GET", "/api/meta", note="Enums and the encoding contract."),
    Example("templates", "GET", "/api/templates"),
    Example(
        "template-detail",
        "GET",
        "/api/templates/1",
        note="The composition editor's three panes.",
    ),
    Example(
        "template-preview",
        "POST",
        "/api/templates/1/preview",
        body={"component_id": 2, "qty_by_size": {"S": "0.14", "M": "0.20", "XL": "0.30"}},
        note="Impact preview. Writes nothing.",
    ),
    Example(
        "stock-tier-a",
        "GET",
        "/api/stock",
        params={"tier": "A", "as_of": "today"},
        note="Theoretical on-hand, batches, drift attribution, projected run-out.",
    ),
    Example(
        "stock-no-runout",
        "GET",
        "/api/stock",
        params={"tier": "A", "run_out": "false"},
        note="The fast variant: no forecast per ingredient.",
    ),
    Example("stock-detail", "GET", "/api/stock/1", note="One ingredient with drift history."),
    Example("suppliers", "GET", "/api/suppliers"),
    Example(
        "orders-draft",
        "GET",
        "/api/orders/draft",
        note=(
            "N drafts by supplier, with cap reasons and the Tesco emergency list. Writes nothing."
        ),
    ),
    Example(
        "margin",
        "GET",
        "/api/margin",
        note="Both rankings, the disagreements, and the uncosted items (invariant 8).",
    ),
    Example(
        "margin-one-template",
        "GET",
        "/api/margin",
        params={"template": "Flavoured Latte"},
        note="Smaller payload: one template.",
    ),
    Example("channels", "GET", "/api/channels"),
    Example("today", "GET", "/api/today"),
    Example(
        "today-with-orders",
        "GET",
        "/api/today",
        params={"with_orders": "true"},
        note="The slow variant: includes a full ordering run.",
    ),
    Example("openapi", "GET", "/api/openapi.json", note="The schema, for codegen."),
)


async def _collect(
    out_dir: Path, *, password: str | None
) -> tuple[list[tuple[str, int, int]], list[dict[str, Any]]]:
    """Drive the app over ASGI and write each response.

    Async because `httpx.ASGITransport` is async-only -- there is no sync ASGI transport,
    and this is one of the few places in the codebase where async is the right answer for
    a reason other than I/O concurrency: it is the transport's contract.
    """
    import httpx

    from cafeops.api.app import create_app

    app = create_app()
    headers = {"X-API-Key": password} if password else {}
    written: list[tuple[str, int, int]] = []
    index: list[dict[str, Any]] = []

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app),
        base_url="http://fixtures.local",
        timeout=600.0,
    ) as client:
        for example in EXAMPLES:
            response = await client.request(
                example.method,
                example.path,
                params=example.params,
                json=example.body,
                headers=headers,
            )
            try:
                payload = response.json()
            except ValueError:
                payload = {"_raw": response.text}
            target = out_dir / f"{example.name}.json"
            text = json.dumps(payload, indent=2, sort_keys=False, ensure_ascii=False) + "\n"
            target.write_text(text, encoding="utf-8")
            written.append((example.name, response.status_code, len(text)))
            index.append(
                {
                    "name": example.name,
                    "file": target.name,
                    "method": example.method,
                    "path": example.path,
                    "params": example.params,
                    "body": example.body,
                    "status": response.status_code,
                    "note": example.note,
                }
            )
    return written, index


def dump_examples(out_dir: Path, *, password: str | None = None) -> list[tuple[str, int, int]]:
    """Write one JSON file per example. Returns (name, status, bytes) for each.

    Driven over `ASGITransport`, so nothing listens on a port and the whole dump is one
    process. The password is passed as a header exactly as a client would, which means
    the auth path is exercised rather than bypassed -- a fixture dump that skipped auth
    would not prove the frontend can get these bytes.
    """
    out_dir.mkdir(parents=True, exist_ok=True)
    written, index = asyncio.run(_collect(out_dir, password=password))

    (out_dir / "index.json").write_text(
        json.dumps(
            {
                "generated_by": "cafeops api-fixtures",
                "encoding": (
                    "Money: integer pence where stored as an integer, an exact decimal STRING "
                    "of pence where derived. Quantities: strings. A missing cost is null. A "
                    "low-confidence forecast has qty null and reasons populated."
                ),
                "examples": index,
            },
            indent=2,
            ensure_ascii=False,
        )
        + "\n",
        encoding="utf-8",
    )
    return written
