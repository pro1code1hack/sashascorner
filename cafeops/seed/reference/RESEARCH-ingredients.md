# Ingredient and price research, 2026-09-28

Covers `ingredients.csv`, `supplier_products.csv`, and the shelf-life fill of
`ingredients_extra.csv`. The loader dry-run (`--only ingredients,prices`) passes with no
errors and nothing refused: 52 ingredient inserts, 108 updates, and 414 price inserts. It
skips 69 prices because an INVOICE price already exists.

## Method
- **Shelf life, open life, allergens:** taken from manufacturer pages (Oatly, Alpro, Vita Coco, Fever-Tree,
  Monin FAQ, Kraft Heinz), Brakes product pages, Cakesmiths product pages (frozen life
  and days after defrost), and Morrisons listings ("typical product life plus delivery
  day" for the fresh extras). Allergens list only what a product **contains**.
  "May contain" is in `note`.
- **Prices:**
  - Brakes: public product search API (`/occ/v2/brakes/products/search`), ex-VAT.
  - Tesco: tesco.com returns 403, so prices come from trolley.co.uk product pages. `sku` is the real Tesco product id.
  - Amazon: search snippets, curl and a read-only browser session.
  - Cakesmiths and cupsdirect.co.uk: public prices, ex-VAT.
- **Choosing the product:** the product each ingredient most likely is. Where that is a guess, the `note` says so.

## Coverage
| | rows | shelf life | allergens | ≥1 price | ≥2 suppliers |
|---|---|---|---|---|---|
| existing (snapshot) | 113 | 42 | 105 | 107 | 63 |
| new in ingredients.csv | 15 | 3 | 13 | 14 | 12 |
| ingredients_extra.csv | 37 | 18 (all chilled/frozen rows) | 12 | 33 | 25 |

For the existing rows, "shelf life" excludes the packaging and sundries, which correctly have
none. A blank shelf life on an existing row means "keep the current value". Blanks
are common for fresh dairy, food and bottled drinks: these carry a use-by date and no
published typical life, and a guess was not written.

## Not found or not written
- **No price anywhere:**
  - Butterfly pea flowers, and "Butterfly syrup (Monin)" (Monin Butterfly Pea is not sold by any of the 7 suppliers).
  - "Toffee syrup (Monin)" (no such Monin UK flavour).
  - Kyiv cake whole and slice (Monolith is trade-only).
  - Raspberry brownie cheesecake (the Cakesmiths product has been withdrawn).
  - Blue matcha powder (Amazon price not visible).
  - Maple syrup, cucumber and romaine lettuce: sold by g or each, and the ingredient unit is ML or KG with no sourced conversion. These rows were dropped.
  - Soup (made in-house).
- **Rows dropped because the loader requires a price or pack size:** the Kyiv/Monolith rows, the Brakes
  lemon drizzle traycake (portions per tray not stated), and the Amazon blue matcha row.
- **Ice cubes (new):** dropped. It is FROZEN, and no bag or producer page states a life.
  A frozen row without one is refused.
- **Storage left unchanged** where a supplier sells the item frozen but no frozen life was
  found: panini bread, bread roll, croissant, the 3 premade paninis, Kyiv cakes, mini cookie
  and raspberry brownie cheesecake. Otherwise these rows would become FROZEN with a 4-day life.
- **Cream cheese:** the 10-day open life was omitted because it is longer than the current 4-day shelf life.
- **Unit conversions by density (in notes):** honey at 1.42 kg/L and golden syrup at
  1.43 kg/L, both from Wikipedia.

## Sites
- **Blocked:** tesco.com (403), amazon.co.uk (503), booker.co.uk (403, no Booker prices at all), help.monin.com (403), lovefoodhatewaste (403).
- **Partly working:** brake.co.uk HTML loaded intermittently; the API was reliable.
- **Not a coffee roaster:** Monolith is Monolith UK Ltd, an Eastern European food wholesaler, and has no public prices.
- **Search budget:** WebSearch ran out (200 calls) partway through.
