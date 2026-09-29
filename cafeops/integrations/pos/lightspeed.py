"""Push an online order into Lightspeed K-Series (CONTRACT §3b.2-3).

**Untested against the live API: no credentials here.** Written against the K-Series
API reference (https://api-docs.lsk.lightspeed.app/, read 2026-09-29): the Order &
Pay group's `POST /o/op/1/order/toGo` (operation `apePlaceToGoOrder`) and
`POST /o/op/1/pay` (`apeMakePayment`), plus `GET /o/op/1/onlineOrderReadiness`. Every
field name below is the documented one; the mapping from OUR rows to THEIR ids is the
part to confirm on the first real call, like the loyalty POS mapping was:

- `items[].sku` <- `menu_item.lightspeed_id` (the id the item sync stores; if the
  till's SKU field differs from the product id, this is the one line to change);
- `items[].modifiers[].modifierId` <- `modifier.lightspeed_modifier_id`; an option
  with no linked ops modifier, or a modifier with no K-Series id, goes into
  `orderNote` instead so the barista still reads it;
- `thirdPartyReference` = `"web:<code>"` (unique, <= 48 chars) -- the same string as
  `sale.lightspeed_receipt_id` for the sale written at COLLECTED;
- `collectionCode` = the order code (<= 8 chars), `orderCollectionTimeAsIso8601` =
  `requested_at`, `customerInfo` from the order (E.164 phone, email, first name), with
  `customerInfo.thirdPartyReference = "member:<id>"` for a signed-in member;
- `payment {paymentMethod, paymentAmount}` only when the order is already PAID
  (Stripe): the API RECORDS a payment, it never charges one.

Configuration: the existing OAuth client (`integrations/lightspeed/client.py`, four
`CAFEOPS_LIGHTSPEED_*` credentials, rate limit, retries) plus
`CAFEOPS_LIGHTSPEED_BUSINESS_LOCATION_ID` and
`CAFEOPS_LIGHTSPEED_ONLINE_ORDER_ENDPOINT_ID` (the Order & Pay webhook endpoint id,
required on every push; webhook management moved out of the API on 2026-05-29 per the
changelog, so it is configured in Lightspeed's back office). The OAuth scope needed is
`orders-api`; the current refresh token may not carry it.

**Double-count risk, documented not solved.** A pushed order becomes a K-Series
receipt; if the nightly Lightspeed sales sync later imports that receipt it will land
as a second `sale` (channel EPOS) beside the shop's own `web:<code>` rows. Dedupe by
`pos_ref` / external reference in `services/ingest_sales.py` is out of scope here.
Until it exists, run the Lightspeed sink only with the sync on fixtures, or accept
that online orders count twice in Sales.

`mark_paid` needs the account identifier K-Series assigns to the pushed order, which
only its `onlineordernotification` webhook returns; we do not receive that webhook
today, so `mark_paid` reports "not possible" unless `pos_ref` was updated by hand.
`cancel` has no endpoint: void on the till.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import UTC
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session, object_session

from cafeops.config import settings
from cafeops.db.models import MenuItem, Modifier, PaymentStatus, ShopOrder
from cafeops.integrations.lightspeed.client import (
    LightspeedAPIError,
    LightspeedClient,
    LightspeedNotConfiguredError,
)
from cafeops.integrations.pos.base import PosPushResult

__all__ = ["LightspeedSink", "build_togo_payload"]

log = logging.getLogger("cafeops.pos.lightspeed")

TOGO_PATH = "/o/op/1/order/toGo"
PAY_PATH = "/o/op/1/pay"
READINESS_PATH = "/o/op/1/onlineOrderReadiness"


def build_togo_payload(session: Session, order: ShopOrder) -> dict[str, Any]:
    """The documented `apePlaceToGoOrder` body from an order. Pure mapping, no I/O."""
    items = {
        i.id: i
        for i in session.scalars(
            select(MenuItem).where(MenuItem.id.in_({ln.menu_item_id for ln in order.lines}))
        )
    }
    modifier_ids = {
        int(o["modifier_id"])
        for ln in order.lines
        for o in ln.options
        if isinstance(o, dict) and o.get("modifier_id") is not None
    }
    modifiers = (
        {m.id: m for m in session.scalars(select(Modifier).where(Modifier.id.in_(modifier_ids)))}
        if modifier_ids
        else {}
    )
    notes: list[str] = []
    lines: list[dict[str, Any]] = []
    for ln in order.lines:
        item = items.get(ln.menu_item_id)
        sku = item.lightspeed_id if item is not None else None
        entry: dict[str, Any] = {"quantity": ln.qty}
        if sku:
            entry["sku"] = sku[:25]
        else:
            # No K-Series id: send it as a custom line so the docket is still complete.
            entry["sku"] = "ONLINE"
            entry["customItemName"] = (f"{ln.name} {ln.size_label}".strip())[:60]
            entry["customItemPrice"] = ln.unit_price_pence / 100
        mods: list[dict[str, str]] = []
        for opt in ln.options:
            if not isinstance(opt, dict):
                continue
            mid = opt.get("modifier_id")
            lsid = (
                modifiers[int(mid)].lightspeed_modifier_id
                if mid is not None and int(mid) in modifiers
                else None
            )
            if lsid:
                mods.append({"modifierId": lsid})
            else:
                notes.append(f"{ln.name}: {opt.get('name', '')}")
        if mods:
            entry["modifiers"] = mods
        lines.append(entry)
    note_parts = [f"Online order SC-{order.code}"]
    if order.dining.value == "EAT_IN":
        note_parts.append("EAT IN")
    note_parts.extend([f"TABLE {order.table}"] if order.table else [])
    if order.note:
        note_parts.append(order.note)
    note_parts.extend(notes)
    customer: dict[str, Any] = {"firstName": order.customer_name[:128] or "Guest"}
    if order.customer_email:
        customer["email"] = order.customer_email
    if order.customer_phone:
        customer["contactNumberAsE164"] = order.customer_phone
    if order.member_id is not None:
        customer["thirdPartyReference"] = f"member:{order.member_id}"
    payload: dict[str, Any] = {
        "businessLocationId": settings.lightspeed_business_location_id,
        "thirdPartyReference": f"web:{order.code}",
        "endpointId": settings.lightspeed_online_order_endpoint_id,
        "customerInfo": customer,
        "orderNote": " | ".join(note_parts)[:500],
        "collectionCode": order.code[:8],
        "orderCollectionTimeAsIso8601": order.requested_at.astimezone(UTC).isoformat(),
        "items": lines,
    }
    if order.payment_status is PaymentStatus.PAID:
        payload["payment"] = {
            "paymentMethod": settings.lightspeed_payment_method_code or "ONLINE",
            "paymentAmount": order.total_pence / 100,
        }
    return payload


class LightspeedSink:
    key = "lightspeed"
    display_name = "Lightspeed K-Series (Order & Pay)"

    def configured(self) -> bool:
        return bool(
            settings.lightspeed_configured
            and settings.lightspeed_business_location_id
            and settings.lightspeed_online_order_endpoint_id
        )

    def _session(self, order: ShopOrder) -> Session:
        session = object_session(order)
        if session is None:
            raise RuntimeError("the order must be attached to a session")
        return session

    def push_order(self, order: ShopOrder) -> PosPushResult:
        if not self.configured():
            return PosPushResult(
                False,
                None,
                "Lightspeed Order & Pay is not configured (credentials, business location id, "
                "endpoint id).",
            )
        payload = build_togo_payload(self._session(order), order)
        try:
            body = asyncio.run(_post(TOGO_PATH, payload))
        except LightspeedNotConfiguredError as exc:
            return PosPushResult(False, None, str(exc))
        except LightspeedAPIError as exc:
            if exc.status_code == 409:
                # `reference has already been used`: the till has it from an earlier try.
                return PosPushResult(True, f"web:{order.code}", "already on the till")
            return PosPushResult(False, None, str(exc)[:300])
        if str(body.get("status", "")).lower() != "ok":
            return PosPushResult(False, None, f"K-Series answered {body}"[:300])
        return PosPushResult(True, f"web:{order.code}", "pushed to the till as a to-go order")

    def mark_paid(self, order: ShopOrder) -> PosPushResult:
        if not self.configured():
            return PosPushResult(False, None, "Lightspeed Order & Pay is not configured.")
        ref = order.pos_ref or ""
        if not ref or ref.startswith("web:"):
            return PosPushResult(
                False,
                None,
                "K-Series needs the account identifier from its order webhook to apply a "
                "payment; not received. Take the payment on the till.",
            )
        payload = {
            "iKaccountIdentifier": ref,
            "thirdPartyPaymentReference": f"web:{order.code}:pay",
            "endpointId": settings.lightspeed_online_order_endpoint_id,
            "businessLocationId": settings.lightspeed_business_location_id,
            "paymentMethod": settings.lightspeed_payment_method_code or "ONLINE",
            "paymentAmount": order.total_pence / 100,
        }
        try:
            body = asyncio.run(_post(PAY_PATH, payload))
        except (LightspeedNotConfiguredError, LightspeedAPIError) as exc:
            return PosPushResult(False, None, str(exc)[:300])
        ok = str(body.get("status", "")).lower() == "ok"
        return PosPushResult(ok, ref, "payment recorded on the till" if ok else str(body)[:300])

    def cancel(self, order: ShopOrder) -> PosPushResult:
        return PosPushResult(
            False, order.pos_ref, "K-Series has no cancel endpoint: void the order on the till."
        )


async def _post(path: str, payload: dict[str, Any]) -> dict[str, Any]:
    """One authenticated POST through the existing client (rate limit, retries, refresh).

    The client's `_request` takes query params only, so the JSON body goes through its
    underlying `httpx` client with the token it minted. Same auth, same limiter.
    """
    async with LightspeedClient() as client:
        client._require_configured()
        token = await client._ensure_access_token()
        await client._rate_limiter.wait()
        response = await client._http.post(
            path,
            json=payload,
            headers={"Authorization": f"Bearer {token}", "Accept": "application/json"},
        )
        if response.status_code == 401:
            token = await client._ensure_access_token(force=True)
            response = await client._http.post(
                path,
                json=payload,
                headers={"Authorization": f"Bearer {token}", "Accept": "application/json"},
            )
        if response.status_code >= 400:
            raise LightspeedAPIError(
                f"POST {path} failed: HTTP {response.status_code} {response.text[:200]}",
                status_code=response.status_code,
            )
        data = response.json()
        return dict(data) if isinstance(data, dict) else {"status": str(data)}
