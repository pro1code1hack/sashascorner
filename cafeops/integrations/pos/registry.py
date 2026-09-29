"""Which POS sink receives NEW orders: `shop_settings.pos_sink` -> a `PosOrderSink`."""

from __future__ import annotations

from collections.abc import Mapping
from typing import TypedDict

from cafeops.integrations.pos.base import PosOrderSink
from cafeops.integrations.pos.lightspeed import LightspeedSink
from cafeops.integrations.pos.null import NullSink

__all__ = ["DEFAULT_SINK_KEY", "SinkInfo", "available", "keys", "sink"]

DEFAULT_SINK_KEY = "none"

_SINKS: Mapping[str, PosOrderSink] = {"none": NullSink(), "lightspeed": LightspeedSink()}


class SinkInfo(TypedDict):
    key: str
    display_name: str
    configured: bool


def keys() -> tuple[str, ...]:
    return tuple(_SINKS)


def sink(key: str | None) -> PosOrderSink:
    """The chosen sink; an unknown key falls back to doing nothing, loudly enough
    (the admin validates the key on PUT; this is the runtime's safety net)."""
    return _SINKS.get((key or DEFAULT_SINK_KEY).strip().lower(), _SINKS[DEFAULT_SINK_KEY])


def available() -> list[SinkInfo]:
    return [
        {"key": s.key, "display_name": s.display_name, "configured": s.configured()}
        for s in _SINKS.values()
    ]
