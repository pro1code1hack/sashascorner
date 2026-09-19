# Agent B — Composition engine

**Owns:** `cafeops/domain/composition.py` (extend), `cafeops/services/edit_composition.py`,
`cafeops/jobs/cost_rollup.py`

The most important agent. This is the half of the product that justifies the build.

## Already done in Phase 0 — extend, do not rewrite

`domain/composition.py::resolve_recipe` implements spec §4.3 rules 1–6:
per-size components, variant options filling role-matched empty slots, modifiers in
`SUBSTITUTE → SCALE → ADD` order, `SubstitutionError` on a non-substitutable slot,
manual-recipe bypass, and separate `lines` (recipe) vs `depletion_lines` (waste
applied) so invariant 5 cannot be violated by accident.

Verified working against the seeded demo: a Pistachio Latte resolves to the right
cup per size, oat-milk substitution moves 0.18 L from whole milk to oat milk, a
`SUBSTITUTE` aimed at the non-substitutable COFFEE slot raises, and resolving a
30-day-old date is unaffected by an edit committed today.

Effective dating is resolved in `db/repositories/composition.py` *before*
`resolve_recipe` runs. Keep that split — the date arithmetic is a query concern and
keeping it out of `domain/` is what makes resolution reviewable.

## Build

### `ImpactPreview` (spec §5.5) — a domain function, not a UI concern

`domain/types.py` already defines `ImpactPreview` and `ImpactedItem`. Produce one
*before* a composition edit commits:

```
Milk 0.18 L -> 0.20 L (size M)
  Affects 21 menu items
  Cost per item        +£0.014
  COGS, last 30 days   +£11.60
  Lowest margin after  Pistachio Latte M, 70.9% -> 70.4%
```

`monthly_cogs_delta_pence` uses the last 30 days of actual sales volume
(`MenuCostRepository.units_sold`). An item with a missing cost must **not** be
silently treated as zero — put it in `warnings` and leave it out of the totals.

### `services/edit_composition.py`

One transaction. Uses `CompositionRepository.close_and_open_component` — already
written, and it **refuses** to touch an already-closed row. "Apply from today" is
the only mode: there is no retroactive edit, because effective dating is what keeps
history honest (invariant 3).

### `jobs/cost_rollup.py` (spec §5.5 cascade)

```
ingredient_price change -> template_component using it -> menu_item resolving
through those templates -> menu_item_cost -> margin + P&L COGS
```

`menu_item_cost` is the materialised cache; refresh it on every composition edit
and on a schedule. 318 resolutions must never happen inside a request handler.

`MenuItemCost.cost_source` is the **weakest** source among the item's ingredients —
`ResolvedRecipe.cost_source` already computes that, including returning `None` when
anything is unpriced. Invariant 6: an estimate stays an estimate all the way up,
and a missing cost stays missing rather than becoming zero.

### Turning proposals into real templates

`cafeops/seed/patterns.py` proposes 27 templates from the legacy data, with
conflicts. Phase 0 deliberately writes **none** of them — every menu item is
`manual_recipe=True` until a human confirms. Build the service that accepts a
confirmed proposal and materialises it (`DrinkTemplate`, `SizeProfile`,
`TemplateComponent`, `VariantAxis`, `VariantOption`, then re-point the `MenuItem`
rows and clear `manual_recipe`). `seed/demo.py::_build_latte_template` is a worked
example of the shape.

## Verify by running

No tests. Show real output for: an impact preview on the latte template's milk
quantity; a cost rollup before/after an ingredient price change; and a proposal
materialised into a template with its menu items re-pointed.
