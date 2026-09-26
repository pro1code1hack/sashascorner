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
from collections.abc import Callable, Mapping
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
    #: For a response that depends on an earlier one (the recipe the Recipes screen
    #: opens first, the month the Money tabs default to). Given the payloads written so
    #: far, by name, it returns the `(path, params)` to request, or None to skip.
    derive: Callable[[Mapping[str, Any]], tuple[str, dict[str, Any] | None] | None] | None = None


def _first(payload: Any, key: str | None, id_key: str) -> Any:
    rows = payload.get(key) if key is not None and isinstance(payload, dict) else payload
    if not isinstance(rows, list) or not rows or not isinstance(rows[0], dict):
        return None
    return rows[0].get(id_key)


def _recipe_editor(seen: Mapping[str, Any]) -> tuple[str, dict[str, Any] | None] | None:
    # RecipesScreen opens "Flavoured Latte" if it exists, else the first template.
    templates = (seen.get("recipes") or {}).get("templates") or []
    chosen = next((t for t in templates if t.get("name") == "Flavoured Latte"), None)
    chosen = chosen or (templates[0] if templates else None)
    return None if chosen is None else (f"/api/templates/{chosen['template_id']}/editor", None)


def _ingredient(seen: Mapping[str, Any]) -> tuple[str, dict[str, Any] | None] | None:
    # IngredientsScreen opens the first row sorted A-Z.
    rows = (seen.get("ingredients") or {}).get("rows") or []
    if not rows:
        return None
    first = sorted(rows, key=lambda r: str(r.get("name", "")).casefold())[0]
    return f"/api/ingredients/{first['ingredient_id']}", None


def _menu_item(seen: Mapping[str, Any]) -> tuple[str, dict[str, Any] | None] | None:
    anchor = _first(seen.get("menu-items"), "groups", "anchor_id")
    return None if anchor is None else (f"/api/menu-items/{anchor}", None)


def _stock_row(seen: Mapping[str, Any]) -> tuple[str, dict[str, Any] | None] | None:
    ing = _first(seen.get("stock-all"), "rows", "ingredient_id")
    return None if ing is None else (f"/api/stock/{ing}", {"as_of": "today", "history": "20"})


def _supplier_products(seen: Mapping[str, Any]) -> tuple[str, dict[str, Any] | None] | None:
    # SuppliersScreen opens the first supplier in the list.
    sid = _first(seen.get("suppliers"), None, "supplier_id")
    return None if sid is None else (f"/api/suppliers/{sid}/products", None)


def _finance(path: str) -> Callable[[Mapping[str, Any]], tuple[str, dict[str, Any] | None] | None]:
    # The four month-scoped Money tabs open on the server's default month.
    def derive(seen: Mapping[str, Any]) -> tuple[str, dict[str, Any] | None] | None:
        month = (seen.get("finance-months") or {}).get("default_month")
        return path, {"period": month or "all"}

    return derive


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
        note=(
            "Impact preview. Writes nothing. `component_id` 2 is the MILK slot of the freshly "
            "seeded Flavoured Latte; once that slot has been edited the id has moved on and "
            "this example returns 409 component_superseded -- which is the right fixture for "
            "that case. `GET /api/templates/1` lists the live ids."
        ),
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
        "takings",
        "GET",
        "/api/takings",
        params={"days": "14"},
        note=(
            "The missing half of Money & P&L: what the cafe actually took, by day and "
            "method. Note `net_pence: null` -- one export omitted discounts, so the net "
            "is withheld rather than computed from the rows that did report."
        ),
    ),
    Example(
        "proposals",
        "GET",
        "/api/proposals",
        note=(
            "Import review (spec 6): 27 detected patterns waiting for a human, 11 of "
            "them with quantity conflicts the legacy rows disagree about. Reading "
            "writes nothing; confirming is what creates a template."
        ),
    ),
    Example(
        "orders-draft",
        "GET",
        "/api/orders/draft",
        note=(
            "N drafts by supplier, with cap reasons and the Tesco emergency list. Writes nothing."
        ),
    ),
    Example(
        "orders-draft-capped",
        "GET",
        "/api/orders/draft",
        params={"order_date": "2026-09-17"},
        note=(
            "Invariant 4 on the wire: a Whole milk line capped at 5 days by shelf life, with "
            "`cap_reason`, `cap_detail` and `capped_out_qty`. A past order_date is used "
            "because whether the cap produces a LINE depends on the day's stock -- on a day "
            "when the shortened window is already covered, the cap still applies and the "
            "candidate appears under `skipped` with `is_capped` instead. Both shapes matter "
            "and this fixture guarantees the first one exists."
        ),
    ),
    Example(
        "orders-draft-emergency",
        "GET",
        "/api/orders/draft",
        params={"order_date": "2026-09-21"},
        note=(
            "The Tesco emergency list with a real line on it: what could not wait for a "
            "scheduled delivery, and the retail premium that cost. A past order_date because "
            "the seeded shortfalls are a disrupted supplier round, not an everyday state -- "
            "on most days `emergency` is correctly empty, which is the other shape."
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
    # ---- the back-office redesign: every read the v2 screens make on first render.
    Example("shell", "GET", "/api/shell", note="Sidebar sync line, badges, banners."),
    Example("settings", "GET", "/api/settings"),
    Example("setup", "GET", "/api/setup", note="The Setup checklist."),
    Example("agent-proposals", "GET", "/api/agents/proposals", params={"limit": "6"}),
    Example("agent-runs", "GET", "/api/agents/runs", params={"limit": "30"}),
    Example(
        "stock-all",
        "GET",
        "/api/stock",
        params={"as_of": "today", "include_untracked": "true"},
        note="The Stock screen: every ingredient, tracked or not.",
    ),
    Example("stock-row", "GET", "", note="The Stock drawer for the first row.", derive=_stock_row),
    Example("orders", "GET", "/api/orders", note="Order history, every supplier."),
    Example("shop-runs", "GET", "/api/orders/shop-runs", params={"months": "8"}),
    Example("supplier-products", "GET", "", derive=_supplier_products),
    Example("recipes", "GET", "/api/recipes", note="The Recipes rail."),
    Example("recipe-editor", "GET", "", derive=_recipe_editor),
    Example("seasons", "GET", "/api/seasons"),
    Example("menu-items", "GET", "/api/menu-items"),
    Example("menu-item", "GET", "", note="The item drawer for the first card.", derive=_menu_item),
    Example("ingredients", "GET", "/api/ingredients"),
    Example("ingredient", "GET", "", derive=_ingredient),
    Example("finance-months", "GET", "/api/finance/months"),
    Example("finance-meta", "GET", "/api/finance/meta"),
    Example("finance-alerts", "GET", "/api/finance/alerts"),
    Example("finance-pl", "GET", "/api/finance/pl"),
    Example("finance-director", "GET", "/api/finance/director"),
    Example("finance-overview", "GET", "", derive=_finance("/api/finance/overview")),
    Example("finance-sales", "GET", "", derive=_finance("/api/finance/sales")),
    Example("finance-expenses", "GET", "", derive=_finance("/api/finance/expenses")),
    Example("finance-reconcile", "GET", "", derive=_finance("/api/finance/reconcile")),
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
    seen: dict[str, Any] = {}

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app),
        base_url="http://fixtures.local",
        timeout=600.0,
    ) as client:
        for example in EXAMPLES:
            path, params = example.path, example.params
            if example.derive is not None:
                derived = example.derive(seen)
                if derived is None:
                    continue
                path, params = derived
            response = await client.request(
                example.method,
                path,
                params=params,
                json=example.body,
                headers=headers,
            )
            try:
                payload = response.json()
            except ValueError:
                payload = {"_raw": response.text}
            seen[example.name] = payload
            target = out_dir / f"{example.name}.json"
            text = json.dumps(payload, indent=2, sort_keys=False, ensure_ascii=False) + "\n"
            target.write_text(text, encoding="utf-8")
            written.append((example.name, response.status_code, len(text)))
            index.append(
                {
                    "name": example.name,
                    "file": target.name,
                    "method": example.method,
                    "path": path,
                    "params": params,
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
