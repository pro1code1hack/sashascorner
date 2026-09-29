"""Where a NEW online order goes besides our own tables (CONTRACT §3b.3).

A `PosOrderSink` pushes the order into the till so staff see it where they see
everything else. `null` (the default) does nothing; `lightspeed` uses the K-Series
Order & Pay API. Chosen by `shop_settings.pos_sink` through `registry.sink()`.

The push is best effort and after the fact: the order exists in `shop_order` whether
or not the till heard about it, and the `sale` rows written at COLLECTED are the
shop's own record either way (see `lightspeed.py` on the double-count risk).
"""

from cafeops.integrations.pos.base import PosOrderSink, PosPushResult

__all__ = ["PosOrderSink", "PosPushResult"]
