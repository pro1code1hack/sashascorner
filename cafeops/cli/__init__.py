"""Back-office CLI. The owner never sees this -- her only interface is Telegram.

One command module per section, each exposing `register(app)`, and one module per
sub-app (`agent`, `portal`, `pos`, `channels`, `payments`, `supplier`, `loyalty`, `shop`,
`wallet`, `transactions`). This module builds the root Typer, registers the commands in
the order the help has always listed them, and mounts the sub-apps. Every Typer module
lives here; the packages they drive (`agent/`, `integrations/`, `services/`) hold no CLI.
"""

from __future__ import annotations

import typer

from cafeops.cli import batches, bot, composition, reports, seed, serve, simulate, stock
from cafeops.cli._common import console
from cafeops.cli.agent import app as _agent_app
from cafeops.cli.channels import app as _channels_app
from cafeops.cli.loyalty import loyalty_app as _loyalty_app
from cafeops.cli.payments import app as _payments_app
from cafeops.cli.portal import portal_app as _portal_app
from cafeops.cli.portal import worker_command as _worker_command
from cafeops.cli.pos import app as _pos_app
from cafeops.cli.posters import qr_posters as _qr_posters
from cafeops.cli.shop import shop_app as _shop_app
from cafeops.cli.supplier import shelf_life_app as _shelf_life_app
from cafeops.cli.supplier import supplier_app as _supplier_app
from cafeops.cli.transactions import app as _transactions_app
from cafeops.cli.wallet import wallet_app as _wallet_app

__all__ = ["app", "console"]

app = typer.Typer(
    add_completion=False,
    no_args_is_help=True,
    help="Cafe Ops -- inventory and auto-ordering for Sasha's Corner.",
)

# Commands, in the order the help lists them. Typer prints commands before groups, so
# the `password` group registered inside `stock` still lands with the other groups.
seed.register(app)
stock.register(app)
composition.register(app)
simulate.register(app)
batches.register(app)
# Supplier web-shop integrations (docs/agents/BROWSER-ORDERING.md): the worker that is
# the only process to open a browser.
app.command(name="browser-worker")(_worker_command)
reports.register(app)
serve.register(app)
bot.register(app)

# Sub-apps (spec 4.6, spec 9).
app.add_typer(_channels_app, name="channels")
# The operator path for the doctor's two standing warnings. Without these the
# only way to confirm a supplier term or a shelf life was to edit a seed file.
app.add_typer(_supplier_app, name="supplier")
app.add_typer(_shelf_life_app, name="shelf-life")
app.add_typer(_agent_app, name="agent")
# Supplier web-shop integrations: sign-ins and basket staging jobs.
app.add_typer(_portal_app, name="portal")
app.add_typer(_pos_app, name="pos")
# The missing half of Money & P&L: what the cafe actually took (ARCHITECTURE 8T).
app.add_typer(_payments_app, name="payments")
app.add_typer(_transactions_app, name="transactions")

# Sasha's Corner Rewards (docs/loyalty/CONTRACT.md §7).
app.add_typer(_loyalty_app, name="loyalty")
app.add_typer(_shop_app, name="shop")
_loyalty_app.command(name="qr-posters")(_qr_posters)
app.add_typer(_wallet_app, name="wallet")
