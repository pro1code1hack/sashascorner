# Deliveroo listing → reference seed

Source: the café's own Deliveroo page,
https://deliveroo.co.uk/menu/fife-and-perthshire/dundee/sashas-corner, captured
2026-09-28 into `deliveroo/menu.json` (278 item records, 16 categories, 16 modifier groups).

## What the listing is

- 145 items sit in the 16 visible categories. The other 133 records have no category and a
  price of 0. They are modifier options (milks, syrups, sizes, sugar) and the drink/snack
  choices inside the Lunch deal and Afternoon Bundle. Five of them are orphan products
  that appear in no group: Ham and cheese toastie, Mozzarella and pesto toastie, and the
  Tomato&Cheese and Tuna&Cheese paninis. They are probably retired.
- The `Afternoon Bundle` is listed but marked unavailable.
- 118 unique images. Five images are shared between two items: Single/Double Espresso,
  Single/Double Macchiato, Single/Double Con Pana, Banana/Vanilla Milkshake, and
  Salted Caramel/Salted Caramel Coffee Milkshake.

## Photos (`photos/menu/`, index in `deliveroo/images.csv`)

- 45 originals are larger than 1600px. I fetched them with
  `?width=1600&height=1600&fit=crop&format=jpg`, which gives a 1600×1600 square. I checked
  every one by eye and none is badly cropped.
- 73 originals are only 1200×675 (a few are 1376×768). For these, `fit=crop` gives a
  675px square, because the CDN does not upscale, and it cuts off packs and cake trays.
  `fit=max` returned 675×675 as well. I kept the full-frame original instead. It is still
  ≥1200px wide, as the README requires.
- All 118 files are real JPEGs of 2 MB or less, 16 MB in total.
- **Not all of these are the café's photos.** Nine files used in `photos.csv` are brand or
  supplier packshots: Coca-Cola, Fanta, Harrogate water, Cawston apple juice, the Balocco
  wafer, the Savour It toastie, the Handmade Cake Co brownie, the Billionaire's shortbread
  tray shot, and a stock-looking soup image. They carry the licence
  `third-party packshot (Deliveroo listing) - check before web use`, not `own`.
- Deliveroo reuses the hot-drink photo for Iced Lavender Matcha and Iced Rose Matcha, so
  those two show a hot glass. Their alt text says so.

## Matching (`deliveroo/mapping.csv`)

Of the 145 listed items: 62 exact, 35 normalised (case, "Tea" suffix, "Hot" prefix,
`&`/`and`, can size, the "Cappucino" and "Pod" typos), 9 manual, and 39 with no match.
110 ops names are matched in total, counting modifier options. The manual matches below
need a human to confirm them:

| Deliveroo | ops | conf. |
|---|---|---|
| Catado | Cortado | 0.95 |
| Strawberry Milkshake | Strawberry | 0.95 |
| Fresh Granola Pod / Healthy Chia Pudding / Homemade Bacon Roll | Granola pot / Chia pudding / Bacon roll | 0.9 |
| Afternoon Bundle | Afternoon Deal | 0.8 |
| Chocolate Babyccino | Babyccino | 0.8 |
| Handmade Cake Co GF Chocolate Brownie 60g | Brownie Bar | 0.7 |
| Fresh Cinnamon Bun | Cinnamon classic | **0.5, photo not assigned** |

`photos.csv` includes only matches with confidence ≥ 0.7. It has 90 rows.

## Prices (`deliveroo/channel_prices.csv`)

- The Size groups are expanded. Hot drinks: Small 177ml (+0p) → S, Medium 236ml (+5p) → M,
  Large 354ml (+25p) → XL.
- Iced drinks, milkshakes and bubble tea offer only Medium (+0p) and Large (+25p). I
  mapped these by position to ops S and M.
- Every Deliveroo price is at or above the ops price: 177 comparisons, median +80p (about
  +24%), range +25p to +140p.
- These are **channel prices**. None of them should be written to `menu_item.price_pence`.

## Other files

- `unmatched_items.csv`: 42 Deliveroo products with no ops item. These are candidates for
  new menu items.
- `modifiers.csv`: every option in the 16 groups, with an `implies_ingredient` hint.
  `(NEW?)` marks an option that probably has no ingredient in ops yet.
- `descriptions.csv`: 18 Deliveroo descriptions that are real sentences. They are verbatim,
  so several are longer than 140 characters (see the `chars` column). The Cakesmiths cake
  text and the toastie text are supplier copy.
