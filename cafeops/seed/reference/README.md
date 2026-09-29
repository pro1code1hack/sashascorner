# Reference seed — contract

Researched starter values for ingredients, supplier prices, menu descriptions,
recipes and photos. Plain files a human can read and edit, loaded with

```bash
uv run cafeops seed-reference --dry-run      # prints every insert/update, writes nothing
uv run cafeops seed-reference --commit       # applies through the services layer
uv run cafeops seed-reference --commit --only ingredients,prices   # subset
```

Loading is an **upsert by natural key** and is safe to re-run. Edit a file, run it again,
and only the difference is applied.

## Rules every file obeys

1. **Nothing here outranks what the owner entered.** The loader never overwrites a value
   whose source is `INVOICE`, `SUPPLIER_FEED`, a confirmed shelf life (`supplier` /
   `packaging`), a menu price, or a recipe that already exists. Researched values land as
   `ESTIMATE` and stay flagged through every rollup (CLAUDE.md invariant 8).
2. **Every researched number carries its evidence:** `source_url` + `fetched_on`
   (YYYY-MM-DD). A number without a source is not written; leave the cell blank.
3. **Money in integer pence, quantities as decimal strings.** No floats.
4. **Names match the database exactly** (`_current_ingredients.csv`, `_current_menu.csv`
   are snapshots taken 2026-09-28). A new ingredient is a new row with `is_new = true`.
5. **Menu prices are the café's own.** Internet prices appear only as `benchmark_*`
   columns for comparison. They are never written to `menu_item.price_pence`.
6. **Photos must be free to reuse commercially** (Unsplash, Pexels, Wikimedia Commons
   CC0/CC-BY/CC-BY-SA). Record licence, author and page URL. They are placeholders until
   the café photographs its own; they go into the media library and are linked to menu
   items, never assigned to website slots automatically.

## Files

### `ingredients.csv`
`name, is_new, unit (L|KG|ML|G|EACH), category, storage (AMBIENT|CHILLED|FROZEN),
shelf_life_days, open_life_days, allergens, source_url, fetched_on, note`

`allergens`: `|`-separated from the UK 14: celery, cereals_gluten, crustaceans, eggs,
fish, lupin, milk, molluscs, mustard, tree_nuts, peanuts, sesame, soya, sulphites.
Leave blank when unknown and write `none` when you have confirmed there are none.

`ingredients_extra.csv` has the same columns. It holds new ingredients that a researched recipe
needs (ube powder, hojicha, tapioca pearls…), written by the recipe researcher. The loader
reads both files; a name in both is an error.

### `supplier_products.csv`
`ingredient, supplier (Amazon|Booker|Brakes|Cakesmiths|Cups Direct|Monolith|Tesco),
product_name, sku, pack_size (decimal string), pack_unit, price_pence, vat_included (true|false),
product_url, fetched_on, note`

Upsert key: (supplier, ingredient, sku). When there is no SKU, use a slug of `product_name`.
Price is ex-VAT when it can be found; otherwise record it as shown and set `vat_included`.

### `menu.csv`
`name, category, description (≤140 chars, plain, no claims about sourcing or origin),
tags (| separated: vegan-possible, contains-caffeine, iced, seasonal…),
benchmark_price_pence, benchmark_source_url, fetched_on`

### `recipes.csv`
Proposed recipes, **only for menu items that have none today**:
`menu_item, size (S|M|XL|One), ingredient, qty (decimal string, in the ingredient's unit),
role, source_url, note`

These are loaded as manual recipe lines flagged `data_quality_flag = "researched recipe —
confirm"`, and only for sizes with no lines yet.

### `photos.csv` + `photos/`
`menu_item (or category:<name>), file (photos/<slug>.jpg|webp, ≤2 MB, ≥1200px wide),
licence, author, source_page_url, fetched_on, alt`

## Loader

```bash
uv run cafeops seed-reference                 # dry run (default): validate, then report
uv run cafeops seed-reference --commit        # write, one transaction per section
uv run cafeops seed-reference --only ingredients,prices --commit
uv run cafeops seed-reference --only stock-photos --commit   # opt-in fallback, see below
uv run cafeops seed-reference --dir PATH --show 50           # another folder; more detail lines
```

Code: `cafeops/seed/reference_csv.py` (parse + validate), `cafeops/services/reference_seed.py`
(one function per section), command in `cafeops/cli/seed.py`. Schema: migration `c4d2e8a91f07`.

**Validation first.** Every file is parsed before anything is written; any error stops the
run and lists all of them as `file:row: problem` (bad unit/storage, `3.50` in a pence
column, non-decimal quantities, bad dates, duplicate keys, unknown ingredient / menu item /
supplier / category / size, a number without `source_url` + `fetched_on`, a photo that is
missing, outside this folder, not an image by its bytes, or over 2 MB). Photos under
1200px wide, and rows without author or alt text, are warnings.

**Each section prints** insert / update / skip counts, the skip reasons with counts, and
the first `--show N` changes. A second `--commit` reports only skips.

| Section (`--only`) | File(s) | Writes | Never touches |
|---|---|---|---|
| `ingredients` | `ingredients.csv`, `ingredients_extra.csv` | new ingredients (tier C, untracked, shelf life ESTIMATE); on existing ones storage / shelf / opened life while `shelf_life_source` is empty or ESTIMATE (then ESTIMATE, `source_note` = url); `allergens` + `allergens_source` where NULL | a confirmed shelf life; allergens already recorded; unit. A new CHILLED/FROZEN row without a shelf life is refused (skip) |
| `prices` | `supplier_products.csv` | `supplier_product` upserted by (supplier, ingredient, sku), ★ only if the ingredient has no ★; an ESTIMATE `ingredient_price` from the ★ researched link (else the cheapest per unit), then the cost rollup for that ingredient | a price whose current source is INVOICE / SUPPLIER_FEED; an archived link; a ★ link priced from an invoice |
| `menu` | `menu.csv` | `menu_item_reference` (description, tags, benchmark, source); the website blurb (`site_menu_item_meta.description`) where the site has none | `price_pence` (benchmarks are comparison only); a non-empty site description; items whose text the TV menu board supplies |
| `recipes` | `recipes.csv` | `manual_recipe_line` via `menu_catalog.stage_manual_lines`, dated from now, signed in `recipe_change`; `manual_recipe = true`, `data_quality_flag` "researched recipe — confirm"; cost rollup | any size that already has lines or a template. `role` is not stored (manual lines have no role). A line needs `source_url` **or** a `note` saying where the quantity comes from ("mirrors Hot Rose Matcha") |
| `photos` | `photos.csv` | `media_asset` (sha256, deduped; licence / author / source_url), the website photo library via the site's own `sashasite media-add`, and `menu_item.photo_asset_id` on every size where NULL. `category:<name>` fills the category's items that still have none, after the item rows | website slots; a menu item that already has a photo |
| `ingredient-photos` | `ingredient_photos.csv` | `media_asset` + `ingredient.photo_asset_id` where NULL. Not added to the website library | an ingredient that already has a photo |
| `stock-photos` | `photos_stock_menu.csv` | like `photos`, only for items with no photo on any size; a stock photo no item needs is not stored. Runs only when named in `--only` | anything with a photo |

Photos land where the running apps read them: ops files in `CAFEOPS_MEDIA_DIR`
(`<sha256>.<ext>`), site variants in `SITE_MEDIA_DIR` (`<id>-<w>.webp`), and the site
library is written into the same database as cafeops (`SITE_DATABASE_URL` is set from
`CAFEOPS_DATABASE_URL`). To rehearse on a copy:

```bash
sqlite3 cafeops.db ".backup /tmp/copy.db"
CAFEOPS_DATABASE_URL=sqlite+pysqlite:////tmp/copy.db CAFEOPS_MEDIA_DIR=/tmp/m \
  SITE_MEDIA_DIR=/tmp/s uv run cafeops seed-reference --commit
```
