"""Per-area routers for the 2026-09 back-office redesign (docs/design/specs/).

One module per area so the areas can be built independently. Every router here requires
auth (`ApiAuth`); an area that needs an unauthenticated route (only `shell`, for sign-in)
exposes a separate `open_router`.

Sasha's Corner Rewards adds two open routers, both reached from the PUBLIC site through
Caddy: `loyalty.open_router` (`/api/loyalty/*`, card-token scoped) and `staff.open_router`
(`/api/staff/*`, device token + PIN session). `members.router` is the back office's side
and sits behind `ApiAuth` like every other area.

`website.router` forwards the public website's admin (`/api/website/*`) to the site API;
`website.open_router` serves its photo files, which are public on the site anyway.
"""

from cafeops.api.areas import finance, loyalty, members, menu, shell, staff, stock, website

__all__ = ["finance", "loyalty", "members", "menu", "shell", "staff", "stock", "website"]
