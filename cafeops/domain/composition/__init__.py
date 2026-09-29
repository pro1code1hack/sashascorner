"""Recipe resolution and impact preview. Spec 4.3 and 5.5.

PHASE 0 SCOPE NOTE: this module belongs to Agent B (composition engine). Phase 0
implements `resolve_recipe` because it is the contract every other agent depends
on and because `seed --demo` and `stock --as-of` cannot exist without it. Agent B
owns hardening it and adding the impact preview and the cost cascade (spec 5.5).
`preview_impact` (spec 5.5) is Agent B's addition: it answers "what would this edit
do" from two resolutions of the same item, and it is the reason a composition edit
can be reviewed before it commits rather than explained afterwards.

Pure: dataclasses in, dataclasses out. No SQLAlchemy, no I/O. Effective dating is
resolved by the repository BEFORE this function runs -- a MenuItemSpec already
contains only the components and options in force at the resolve date. That split
is deliberate: the date arithmetic is a query concern, and keeping it out of here
is what makes resolution testable without a database.

SEASONS (v2, spec 4.3). `resolve_recipe` **always resolves, in season or not.** An
out-of-season option produces a WARNING, never an error and never a dropped line.
Two reasons, and the second is the load-bearing one:

1. A past sale has to resolve. Expansion resolves each sale at its own `sold_at`,
   and a Pistachio Latte sold in April must still deplete pistachio syrup when the
   ledger is rebuilt in September.
2. Even a sale dated TODAY, out of season, has to resolve. If the till rang one up,
   the syrup left the building. Refusing to resolve would hide real consumption
   behind a calendar, and the stock figures -- the thing the drift metric rests on
   -- would be wrong in the one direction nobody would think to check.

Out-of-season is therefore an **availability** fact, not a composition fact.
`availability_at` answers it for the MENU: the item is listed as unavailable, the
ordering path stops buying for it, and the recipe still works. Those are separate
questions and this module keeps them separate.
"""

from __future__ import annotations

from cafeops.domain.composition.availability import (
    Availability,
    ItemAvailability,
    OptionSeason,
    availability_at,
)
from cafeops.domain.composition.changes import (
    ChangeImpact,
    ItemChange,
    summarise_changes,
)
from cafeops.domain.composition.changeset import (
    ChangesetContext,
    ChangesetOutcome,
    DraftAxis,
    DraftComponent,
    DraftItem,
    DraftOption,
    OpBasePrice,
    OpComponentAdd,
    OpComponentQty,
    OpComponentRemove,
    OpComponentSet,
    OpOptionActive,
    OpOptionAdd,
    OpOptionRemove,
    OpOptionSet,
    OpPrepSet,
    OpTemplateRename,
    Refusal,
    TemplateDraft,
    TemplateOp,
    apply_template_ops,
    base_prices,
    item_prep,
    item_spec_from_draft,
)
from cafeops.domain.composition.display import (
    SIZE_ORDER,
    gbp,
    qty_text,
    unit_gbp,
    unit_label,
)
from cafeops.domain.composition.impact import (
    ItemImpact,
    LabourImpact,
    LabourImpactedItem,
    preview_impact,
    preview_labour_impact,
)
from cafeops.domain.composition.resolve import (
    resolve_recipe,
)

__all__ = [
    "SIZE_ORDER",
    "Availability",
    "ChangeImpact",
    "ChangesetContext",
    "ChangesetOutcome",
    "DraftAxis",
    "DraftComponent",
    "DraftItem",
    "DraftOption",
    "ItemAvailability",
    "ItemChange",
    "ItemImpact",
    "LabourImpact",
    "LabourImpactedItem",
    "OpBasePrice",
    "OpComponentAdd",
    "OpComponentQty",
    "OpComponentRemove",
    "OpComponentSet",
    "OpOptionActive",
    "OpOptionAdd",
    "OpOptionRemove",
    "OpOptionSet",
    "OpPrepSet",
    "OpTemplateRename",
    "OptionSeason",
    "Refusal",
    "TemplateDraft",
    "TemplateOp",
    "apply_template_ops",
    "availability_at",
    "base_prices",
    "gbp",
    "item_prep",
    "item_spec_from_draft",
    "preview_impact",
    "preview_labour_impact",
    "qty_text",
    "resolve_recipe",
    "summarise_changes",
    "unit_gbp",
    "unit_label",
]
