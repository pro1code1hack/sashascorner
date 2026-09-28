"""The one refusal type. `code` is the machine-readable key the pages switch on."""

from __future__ import annotations

__all__ = ["LoyaltyError"]


class LoyaltyError(Exception):
    """A refusal with an HTTP answer. `detail` is a sentence for a person, shown verbatim.

    Not a `ValueError`: the app maps `ValueError` to a bare 422, and these carry their own
    status (409 for "manager PIN needed", 429 for "slow down") that must not be flattened.
    """

    def __init__(self, status: int, code: str, detail: str) -> None:
        super().__init__(detail)
        self.status = status
        self.code = code
        self.detail = detail
