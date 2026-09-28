# Research notes: menu.csv and recipes.csv

Fetched 2026-09-28. These files follow the rules in `README.md`. Every benchmark is some other café's price and is never written to `menu_item.price_pence`. Every researched recipe loads flagged `researched recipe — confirm`.

## Benchmark sources

All listings are on Deliveroo, read in a browser because WebFetch gets a 403 there. Deliveroo prices often carry a delivery mark-up over counter prices, so treat each benchmark as an upper-leaning comparison.

| Key | Café | Where | Used for |
|---|---|---|---|
| LT | Little Things Cafe | Dundee | Core coffee, iced coffee, iced matcha (£4.60), iced teas, juices, Coke, filled pastry, ciabatta, cake slices |
| FT | The Flame Tree Cafe | Dundee | Chai, iced chai, iced matcha, milkshakes (£4.95), teas, toast, croissant with jam |
| ES | Empire State Coffee | Dundee | Add-ons (syrup £0.80, alt milk £0.50, shot £0.80), 12oz iced americano, flavoured iced lattes (£4.99), muffins, brownie, shortbread, cheesecake, blondie, soup, porridge, bacon roll, white hot chocolate |
| CH | Dundee Cycle Hub Cafe | Dundee | Espresso, macchiato, green tea, water, smoothie (£3.50), avocado toast, traybakes |
| POBA | Poba Bubble Tea | Dundee | All creamy bubble teas (£3.99, one size), ham & cheese panini |
| CROL | Crollas | Dundee | Sweet waffles (£9.49), ham & cheese toastie |
| LOOM | Loom Deli | Dundee | Lemonade (£7.50, a premium outlier), Fanta |
| MS | Matcha Sando | **Edinburgh** | Flavoured hot matcha and hojicha (£4.95, 16oz), pistachio latte (£4.25). No Dundee café sells these. |
| COURIER | The Courier, 17 Aug 2026 | Dundee | West House granola £7.95 |

Some benchmarks are derived. **Flavoured hot latte = £5.05**, which is ES latte £4.25 plus ES syrup £0.80. **Flavoured hot chocolate = £5.15**, which is ES hot chocolate £4.35 plus £0.80. Both figures are what that café charges for the same build. The `note` behind each benchmark is kept in the build script and is summarised here rather than in the CSV, because the contract has no column for it.

**Compare against the M price.** Most benchmarks are 12oz regulars, so M is the right comparison, not S or XL. The MS flavoured matcha prices are for 16oz drinks.

**Left blank (67 items):** these had no comparable independent listing:

- all ube drinks (the only UK listing found was Costa's Sweet Ube Iced Whipped Latte at £4.95/£5.20, a chain)
- blue matcha
- Raff, white mocha, espresso tonic, coconut water latte
- the mochas with rose or raspberry
- the special leaf teas
- the refreshers other than Berry Breeze
- coffee milkshakes, kiwi and mango milkshakes
- omelette, chia pudding, Caesar and tuna salads, the savoury waffles, sausage roll
- the deals, snacks, Capri-Sun, Pringles, Syrup Gift Set and the whole Kyiv cake

## Recipe conventions mirrored from the database

- **Hot drinks:** S uses an 8oz cup and 0.16 L milk, M uses 12oz and 0.24 L, XL uses 16oz and 0.32 L. Syrup is 15, 20 or 25 ml. Matcha is 1.5, 2 or 2.5 g.
- **Iced drinks:** S uses a 12oz cup and 0.12 L milk, M uses 16oz and 0.18 L.
- **Bubble tea:** adds a wide straw, 4 g loose-leaf black tea, and 30 or 50 g tapioca.
- **Milkshakes:** 60 or 100 ml vanilla ice cream.
- Food lines have no packaging, which matches Ham Croissant and the toasties.
- Food with no size uses size `One`. In the database these are menu_item rows with `size_code` NULL.

## Assumptions to confirm with the owner

1. **Ube:** modelled as ube powder, 2–3 g per 250 ml. If the café uses a sweetened ube syrup, swap the ingredient.
2. **"Cloud"** is read as whipped cream foam.
3. **Iced ube in XL:** the POS "Large" has no cold cup bigger than 16oz in the ingredient list.
4. **Waffles:** one Belgian waffle is about 90 g of dry mix. The board says "waffles", so double this if two are served.
5. **Cheesy potato waffles** are read as two frozen potato waffles with cheese.
6. **Sausage roll** is read as sausages in a bread roll, because it sits beside Bacon roll on the breakfast board, not as a pastry roll.
7. **Croissant (lunch)** is modelled as ham & cheese. **Fresh ciabatta** is also modelled as ham & cheese.
8. **Soup of the day** is a new composite ingredient `Soup of the day (batch)` in litres, at 0.3 L a portion.
9. **Tiramisu blondie** uses the existing, unused ingredient `Tiramisu cake (slice)`. Its invoiced 135p matches Cakesmiths' Tiramisu Blondie at £1.35 a portion.
10. **Kiwi:** added `Kiwi syrup (Monin)`. The existing Kiwi Bubble Tea recipe uses strawberry syrup as a stand-in, which is worth fixing separately.
11. **Syrup Gift Set** has no recipe. It is a retail bundle and its contents are unknown.

## The café's own Deliveroo listing (`deliveroo/menu.json`)

**Descriptions follow this order of preference:**

1. The board note.
2. The café's own Deliveroo text, where it describes the item. There are 37 of these, including espresso volumes, tea 414 ml, Tiramisu blondie, Billionaire's, granola, chia, avocado toast and the toastie.
3. Plain researched text.

Filler text on Deliveroo such as "Choose your size" or "Perfect taste!" is ignored. Claims of "homemade" and "Wiltshire" were dropped because of the `site/BRIEF.md` facts rule. Deliveroo prices are channel prices and are not used as benchmarks.

**Recipes changed to match the café's text:**

- **Granola pot:** vanilla yoghurt, honey, granola, and three berries.
- **Chia pudding:** chia, Greek yoghurt, cottage cheese, golden syrup, oats and fruit. It uses no milk.
- **Avocado toast:** seeded brown toast, tomatoes and wild rocket.
- **Banana bread hojicha:** has no added syrup.

Recipes use the default milk (whole) and no optional syrup.

**Deliveroo sizes disagree with the cups in the database.** Deliveroo sells Small 177 ml, Medium 236 ml and Large 354 ml. The database maps S, M and XL hot drinks to 8, 12 and 16oz cups, and these recipes mirror the database. Worth checking with the owner.

## Existing-data oddities noticed (not changed)

- Kiwi Bubble Tea and Passion Fruit Bubble Tea use strawberry syrup.
- Black Honey is a tea bag with strawberry syrup.
- Iced Coconut Water Latte has no coconut water.
- Hot banana matcha uses the same M quantities and 12oz cup for all three sizes.
