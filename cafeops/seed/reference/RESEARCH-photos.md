# Ingredient photos: research notes

Fetched 2026-09-28. Output: `ingredient_photos.csv` + `photos/ingredients/*.jpg`.

## Scope change

The brief started as menu-item photos (`photos.csv`). Partway through, the owner decided
menu photos will come from the café's own Deliveroo listing, so this work was redirected
to **ingredient** photos. No menu photos were downloaded, so there is no `photos.csv`
and no `photos_stock_menu.csv`.

## Source and method

- **Every photo is from Wikimedia Commons.** Unsplash's site and API sit behind a bot
  challenge (Anubis) and Pexels returns 403 to scripted requests. The Commons API also
  returns licence and author metadata, so every row's licence comes from the file page's
  own `LicenseShortName` rather than being typed in by hand.
- Search: the Commons API (`generator=search`, File namespace, bitmap only). Only files
  ≥1200 px wide under CC0, Public domain, CC BY or CC BY-SA were kept; NC and ND were
  refused. A person picked each photo from contact sheets and then checked it again at
  a larger size.
- Download: the Commons 1600 px rendition, converted to JPEG with Pillow (RGB, q≈88,
  progressive), 1200–1600 px wide, largest 1.3 MB.
- Blank authors were read from the file-page wikitext (5 files: "Author assumed"
  templates and one "taken from en:" credit).

## Coverage

145 ingredient rows use 126 files. That covers 145 of the 150 names in
`_current_ingredients.csv` + `ingredients_extra.csv`. `ingredients.csv` did not exist
when this was written. If it adds names, they need rows.

**Not covered (nothing suitable, so the rows were left out rather than filled with a wrong picture):**
Capri-Sun pouch, Fruit Shoot (every pouch or kids'-bottle photo was branded or off-topic),
Sweetened condensed milk (every tin was branded), Wild rocket (the only clean leaf shots are
titled "rot in packaging"), Caesar dressing (only finished salads).

## Placeholder compromises: the photo shows the flavour, not the product

- **Monin syrups** are shown as their flavour (fruit, nut, flower, spice), not a bottle.
  Several share one file: caramel ×3 (caramel, salted caramel, toffee); cinnamon (chai
  concentrate, cinnamon-bun syrup, ground cinnamon); butterfly pea (butterfly syrup +
  flowers); raspberry; strawberry.
- **Branded drinks** (Coca-Cola, Diet Coke, Black Cola, Fanta, Irn-Bru, Sprite,
  Tropicana, Pringles, Pom-Bear) are shown as generic unbranded equivalents. Irn-Bru uses
  the orange-soda photo. Sprite and Tonic water use a plain sparkling-water glass.
- **Packaging:** one photo (a white takeaway cup with lid and sleeve) covers all cup sizes
  and lids. The sleeve has no logo, but it does have a green stop-plug.
- **Cakes** are generic lookalikes, not the Cakesmiths products. The "Morello cake" is a
  cherry cake, "Tiramisu cake" is a layered cream dessert, "Blood orange cake" is a plain
  sponge, "Banana pecan muffin" is banana bread, and "Lemon muffin" is a poppy-seed muffin.
  Alt text describes what is actually in the picture, not the product name.
- Bacon photo shows a butcher's counter with handwritten price tags; Cheese is a
  processed-style yellow slice, not cheddar.

## Licence mix (by file, 126)

| Licence | Files | Attribution required |
|---|---|---|
| CC0 | 29 | no |
| Public domain | 12 | no |
| CC BY 2.0 / 2.5 / 3.0 / 4.0 | 21 | **yes** |
| CC BY-SA 2.0 / 2.5 / 3.0 / 4.0 | 64 | **yes**, plus share-alike on modified versions |

**85 files need a visible credit** wherever they are shown publicly (website, printed
menu). The admin back office is not public, but crediting there too costs nothing. The
credit line format is *"Author, Licence, via Wikimedia Commons"* linking to
`source_page_url`. CC BY-SA also means a cropped or edited version must be released under
the same licence. Replacing these with the café's own photos removes the obligation.

### Files that must be credited

| File | Credit line | Used for |
|---|---|---|
| `almond-milk.jpg` | Kjokkenutstyr, CC BY-SA 4.0, via Wikimedia Commons | Almond milk (barista) |
| `apple.jpg` | Abhijit Tembhekar from Mumbai, India, CC BY 2.0, via Wikimedia Commons | Apple syrup (Monin) |
| `avocado.jpg` | Ivar Leidus, CC BY-SA 4.0, via Wikimedia Commons | Avocado |
| `banana.jpg` | Filo gèn', CC BY-SA 4.0, via Wikimedia Commons | Banana syrup (Monin); Bananas (fresh) |
| `beans-1.jpg` | Alorin, CC BY 4.0, via Wikimedia Commons | Coffee beans (decaf) |
| `black-tea.jpg` | Oraola, CC BY-SA 4.0, via Wikimedia Commons | Loose-leaf tea (black) |
| `blueberries.jpg` | Petar Milošević, CC BY-SA 4.0, via Wikimedia Commons | Blueberries (fresh) |
| `bread-slices.jpg` | Evan Swigart from Chicago, USA, CC BY 2.0, via Wikimedia Commons | Bread (toastie) |
| `brownie.jpg` | Gdr (modified by Lyzzy), CC BY-SA 3.0, via Wikimedia Commons | Brownie bar |
| `butterfly-pea-9.jpg` | Adityamadhav83, CC BY-SA 3.0, via Wikimedia Commons | Butterfly pea flowers |
| `caramel-0.jpg` | Where next Columbus? (English Wikipedia user), CC BY-SA 3.0, via Wikimedia Commons | Toffee syrup (Monin) |
| `caramel-1.jpg` | Rebecca Siegel, CC BY 2.0, via Wikimedia Commons | Salted caramel syrup (Monin) |
| `caramel-2.jpg` | Rainer Zenz, CC BY-SA 3.0, via Wikimedia Commons | Caramel syrup (Monin) |
| `caramel-brownie.jpg` | Stephanie Clifford from Arlington, VA, USA, CC BY 2.0, via Wikimedia Commons | Salted caramel brownie |
| `cheese.jpg` | CNEcija12345, CC BY-SA 4.0, via Wikimedia Commons | Cheese (toastie) |
| `cheesecake-rasp.jpg` | zingyyellow, CC BY 2.0, via Wikimedia Commons | Raspberry brownie cheesecake |
| `cherry-cake.jpg` | RGloucester, CC BY-SA 4.0, via Wikimedia Commons | Morello cake (slice) |
| `chocolate-sauce.jpg` | Aine, CC BY-SA 2.0, via Wikimedia Commons | Dark chocolate sauce |
| `ciabatta-3.jpg` | Key West Wedding Photography, CC BY 2.0, via Wikimedia Commons | Panini bread |
| `ciabatta-5.jpg` | Arnold Gatilao from Oakland, CA, USA, CC BY 2.0, via Wikimedia Commons | Ciabatta roll |
| `cinnamon-0.jpg` | Friedrich Haag, CC BY-SA 4.0, via Wikimedia Commons | Chai concentrate |
| `cinnamon-1.jpg` | Luc Viatour, CC BY-SA 3.0, via Wikimedia Commons | Cinnamon bun syrup (Monin) |
| `cinnamon-4.jpg` | formulatehealth, CC BY 2.0, via Wikimedia Commons | Ground cinnamon |
| `coconut-0.jpg` | Clockery, CC BY 3.0, via Wikimedia Commons | Coconut syrup (Monin) |
| `coconut-milk.jpg` | S Sepp, CC BY-SA 3.0, via Wikimedia Commons | Coconut milk (barista) |
| `coffee-capsule.jpg` | Reddalo, CC BY-SA 4.0, via Wikimedia Commons | Decaf coffee pod |
| `cream.jpg` | Jo, CC BY 2.0, via Wikimedia Commons | Whipping cream |
| `croissant.jpg` | SKopp, CC BY-SA 3.0, via Wikimedia Commons | Plain croissant |
| `croutons.jpg` | Bi-frie (talk), CC BY 3.0, via Wikimedia Commons | Croutons |
| `cucumber.jpg` | Nikodem Nijaki, CC BY-SA 3.0, via Wikimedia Commons | Cucumber |
| `eggs.jpg` | Krzysztof Golik, CC BY-SA 4.0, via Wikimedia Commons | Eggs |
| `golden-syrup.jpg` | Londonsista, CC BY-SA 3.0, via Wikimedia Commons | Golden syrup; Brown sugar syrup |
| `granola.jpg` | David Corby (Miskatonic), CC BY 2.5, via Wikimedia Commons | Granola |
| `ham.jpg` | Anna.Massini, CC BY 4.0, via Wikimedia Commons | Ham (sliced) |
| `hazelnut.jpg` | Ivar Leidus, CC BY-SA 4.0, via Wikimedia Commons | Toffee nut syrup (Monin) |
| `hojicha-powder.jpg` | Francois Mathieu, CC BY-SA 4.0, via Wikimedia Commons | Hojicha powder |
| `honey.jpg` | Fæ, CC BY-SA 3.0, via Wikimedia Commons | Honey |
| `ice-cream.jpg` | a.pasquier from bellingham, washington, CC BY-SA 2.0, via Wikimedia Commons | Vanilla ice cream |
| `jam.jpg` | Chris Bohn from Gainesville, FL, USA, CC BY-SA 2.0, via Wikimedia Commons | Jam sachet |
| `kiwi.jpg` | Luc Viatour, CC BY-SA 2.5, via Wikimedia Commons | Kiwi syrup (Monin) |
| `lavender.jpg` | Norbert Nagel, CC BY-SA 3.0, via Wikimedia Commons | Lavender syrup (Monin) |
| `lemon-cake-0.jpg` | Whoisjohngalt, CC BY-SA 4.0, via Wikimedia Commons | Lemon & pistachio cake |
| `lemon.jpg` | André Karwath aka Aka, CC BY-SA 2.5, via Wikimedia Commons | Lemon syrup (Monin) |
| `mango.jpg` | Ivar Leidus, CC BY-SA 4.0, via Wikimedia Commons | Mango syrup/puree (Monin) |
| `maple.jpg` | Dvortygirl, CC BY-SA 3.0, via Wikimedia Commons | Maple syrup |
| `marshmallow.jpg` | John Morgan, CC BY 2.0, via Wikimedia Commons | Marshmallow |
| `matcha-powder.jpg` | Evanhoever, CC BY-SA 4.0, via Wikimedia Commons | Matcha powder |
| `milk-pour.jpg` | Shixart1985, CC BY 2.0, via Wikimedia Commons | Semi-skimmed milk |
| `muffin-banana.jpg` | Shisma, CC BY 4.0, via Wikimedia Commons | Banana pecan muffin |
| `muffin-lemon.jpg` | Katrin Gilger, CC BY-SA 2.0, via Wikimedia Commons | Lemon muffin |
| `muffin-raspberry.jpg` | Famartin, CC BY-SA 4.0, via Wikimedia Commons | Raspberry almond muffin |
| `oats.jpg` | Yonygg, CC BY-SA 4.0, via Wikimedia Commons | Porridge oats |
| `orange-soda.jpg` | Billjones94, CC BY-SA 4.0, via Wikimedia Commons | Fanta; Irn-Bru; Sugar-free Irn-Bru |
| `orange.jpg` | Ivar Leidus, CC BY-SA 4.0, via Wikimedia Commons | Orange syrup (Monin) |
| `pancakes.jpg` | Shisma, CC BY-SA 4.0, via Wikimedia Commons | Pancake mix |
| `panini-4.jpg` | Jari Asselman, CC BY-SA 4.0, via Wikimedia Commons | Ham & cheese toastie (premade); Cajun chicken panini (premade) |
| `panini-9.jpg` | cyclonebill, CC BY-SA 2.0, via Wikimedia Commons | Margarita panini (premade) |
| `paper-bag.jpg` | Jeffrey Beall, CC BY-SA 2.0, via Wikimedia Commons | Takeaway bag |
| `paper-cup.jpg` | Supuhstar, CC BY-SA 4.0, via Wikimedia Commons | 8oz paper cup; 12oz paper cup; 16oz paper cup; 4oz / small cup; 8oz cup lid; 12oz cup lid; 16oz cup lid |
| `pistachio.jpg` | Kobi Schutz, CC BY-SA 3.0, via Wikimedia Commons | Pistachio syrup (Monin); Pistachio spread |
| `raspberry-1.jpg` | Ivar Leidus, CC BY-SA 4.0, via Wikimedia Commons | Raspberries (fresh) |
| `raspberry-8.jpg` | Ivar Leidus, CC BY-SA 4.0, via Wikimedia Commons | Raspberry syrup (Monin) |
| `romaine.jpg` | Rainer Zenz, CC BY-SA 3.0, via Wikimedia Commons | Romaine lettuce |
| `rose.jpg` | Maor X, CC BY-SA 3.0, via Wikimedia Commons | Rose syrup (Monin) |
| `salami.jpg` | Mattia Luigi Nappi, CC BY-SA 4.0, via Wikimedia Commons | Salami (sliced) |
| `seeded-bread.jpg` | Jim Lukach, CC BY 2.0, via Wikimedia Commons | Seeded brown bread |
| `shortbread-6.jpg` | Finbar.concaig, CC BY-SA 4.0, via Wikimedia Commons | Salted caramel bar |
| `smoked-salmon.jpg` | Touzrimounir, CC BY-SA 4.0, via Wikimedia Commons | Smoked salmon |
| `soup.jpg` | Whoisjohngalt, CC BY-SA 4.0, via Wikimedia Commons | Soup of the day (batch) |
| `soy-milk.jpg` | Kjokkenutstyr, CC BY-SA 2.0, via Wikimedia Commons | Soy milk (barista) |
| `sticky-toffee.jpg` | Chalk and Cheese, CC BY 2.0, via Wikimedia Commons | Sticky toffee Biscoff |
| `stirrers.jpg` | James Petts from London, England, CC BY-SA 2.0, via Wikimedia Commons | Wooden stirrer |
| `strawberries-0.jpg` | Ivar Leidus, CC BY-SA 4.0, via Wikimedia Commons | Strawberries (fresh) |
| `strawberries-1.jpg` | Ivar Leidus, CC BY-SA 4.0, via Wikimedia Commons | Strawberry syrup (Monin) |
| `sugar-sachet.jpg` | Ka23 13, CC BY-SA 4.0, via Wikimedia Commons | Sugar sachet |
| `tapioca.jpg` | Lars Plougmann from United States, CC BY-SA 2.0, via Wikimedia Commons | Tapioca pearls |
| `tomatoes.jpg` | Softeis, CC BY-SA 3.0, via Wikimedia Commons | Tomatoes (fresh) |
| `tuna.jpg` | Tamorlan, CC BY 3.0, via Wikimedia Commons | Tinned tuna |
| `ube-powder.jpg` | Pradeep717, CC BY-SA 4.0, via Wikimedia Commons | Ube powder |
| `vanilla-yogurt-0.jpg` | Rainer Zenz, CC BY-SA 3.0, via Wikimedia Commons | Greek yogurt |
| `vanilla-yogurt-8.jpg` | Urci dream, CC BY-SA 4.0, via Wikimedia Commons | Vanilla yogurt |
| `vanilla.jpg` | Vanilla_6beans.JPG: B.navez; derivative work: Andrzej 22 (talk), CC BY-SA 3.0, via Wikimedia Commons | Vanilla syrup (Monin) |
| `wafer.jpg` | Pilettes, CC BY-SA 3.0, via Wikimedia Commons | Chocolate wafer; Blue chocolate wafer |
| `waffle-mix.jpg` | Doug, CC BY-SA 2.0, via Wikimedia Commons | Belgian waffle mix |
| `white-chocolate.jpg` | Elizabeth Moorehead, CC BY 2.0, via Wikimedia Commons | White chocolate sauce |
