"""Order online (click & collect): every write the shop makes goes through here.

docs/shop/CONTRACT.md is binding. The public API (`api/areas/shop*`), the admin API
(`api/areas/shop_admin*`, Agent B) and the scheduler are all clients of these modules;
none of them touches a `shop_*` table directly (CLAUDE.md §8).

The arithmetic lives in `cafeops/domain/shop.py` and is pure. What lives here is the
part with a session in it: which rows, in which order, in one transaction.

Modules: `catalog` (sync from the ops menu + the customer's catalogue), `slots`
(the shop's own hours -> collection slots), `pricing` (a basket priced on the server),
`orders` (place, transition, the sale + stamps + reward at COLLECTED), `payments`
(Stripe Checkout over plain httpx), `notify` (Telegram to the owner, email/SMS to the
customer).

Every refusal is a `ShopError(status, code, detail)` -- a `LoyaltyError`, so the API
renders it as `{"error": code, "detail": detail}` (§3.10) with one handler.
"""

from cafeops.services.shop.errors import ShopError

__all__ = ["ShopError"]
