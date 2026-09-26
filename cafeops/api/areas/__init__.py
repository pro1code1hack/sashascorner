"""Per-area routers for the 2026-09 back-office redesign (docs/design/specs/).

One module per area so the areas can be built independently. Every router here requires
auth (`ApiAuth`); an area that needs an unauthenticated route (only `shell`, for sign-in)
exposes a separate `open_router`.
"""

from cafeops.api.areas import finance, menu, shell, stock

__all__ = ["finance", "menu", "shell", "stock"]
