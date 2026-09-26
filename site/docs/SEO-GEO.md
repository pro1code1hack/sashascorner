# Sasha's Corner: search and AI visibility (SEO + GEO)

Written 26 September 2026. What the website now does, what the research found, and a
prioritised list of things only the owner can do. The website is the smaller half of
this: for a café, **the Google Business Profile and consistent listings decide most
of what Google and AI assistants say.**

The rule that governs all of it (site/BRIEF.md): nothing in structured data, the FAQ
or the AI-facing text claims anything that isn't verified. When a fact is confirmed,
add it to the site data first, then to listings, in that order.

---

## 1. What people search for, and who shows up (research, Sept 2026)

| Intent | What ranks today | What it means for us |
|---|---|---|
| "best coffee Dundee" / "cafés Dundee" | Tripadvisor "10 best cafés", Wanderlog "50 best", Visit Dundee's café list, The Courier's "seven cosy city-centre cafés", and single cafés with strong sites (Coffee & Co, EH9, Höfn, Empire State, Henry's). [1][2][3][4][5] | List pages dominate. Getting **onto** those lists (Tripadvisor, Visit Dundee, Wanderlog pulls from Google) matters more than our own page ranking. Never say "best" on our own site. |
| "matcha Dundee" | The Courier's "5 best places for matcha in Dundee" (July 2025: Little Things, EH9, Höfn, Flame Tree, CUPP; we opened after it) and matchanearme.com lists. [6][7] | Our matcha range (25 green, 8 blue) is unusually wide. A pitch to The Courier for an update is the single best matcha move. |
| "bubble tea Dundee" | Just Eat and Uber Eats category pages, CUPP, Boba Time (Union St), AdoraBubble. [8][9] | We're on Just Eat already; make sure the listing's category includes bubble tea. |
| "brunch Dundee" | Tripadvisor brunch list, Uber Eats category pages. [10] | Tripadvisor listing + photos of the waffles. |
| "cafe near Dundee station" | Yelp "cafés near Dundee railway station", Wanderlog, the station's own Dundee Coffee House, Bridgeview Station. [11][12] | Yelp and Apple Maps listings; the FAQ and visit page now say "about 700 m, a 10-minute walk from Dundee station". |
| "Kyiv cake Dundee" | **Nothing relevant**: search engines read it as "Dundee cake" and return fruit cake shops. Wikipedia and recipe sites own "Kyiv cake". [13][14] | An empty niche. Our pages now pair "Kyiv cake" with "Dundee" and "order a whole one"; Instagram posts and GBP products should use the same words. |
| "Sasha's Corner Dundee" | Companies House, two Courier articles (the planning story and the opening), FSA hygiene rating, Instagram, Just Eat, a Facebook page. **Not our website yet.** [15][16][17][18] | The entity already exists across the web. The site needs to become its hub: GBP website link, Search Console, and the same name/address/phone everywhere. |

Specific, uncontested queries are where a new café can win quickly: **blue matcha
Dundee, Raff coffee Dundee, Kyiv cake Dundee, rose latte Dundee, hojicha Dundee**.
(No search volumes were measured; these are judgements from what ranks.)

### What the search engines and AI companies say

- **Google AI Overviews / AI Mode**: no special optimisation, no special files and no
  special markup are needed; a page must simply be indexed and snippet-eligible.
  Structured data must match what's visible. Google explicitly lists keeping the
  **Business Profile** up to date as part of doing well. [19]
- **llms.txt**: Google says it gives no special treatment to llms.txt and won't use
  it; server-log studies show AI crawlers rarely fetch it. [19][20] We ship it
  anyway because it costs nothing, is generated from the same data, and some agents
  and tools do read it. Don't expect it to move anything.
- **FAQ rich results**: Google stopped showing FAQ rich results on 7 May 2026. [21]
  FAQPage markup is still valid schema.org and harmless; its value now is that the
  questions and answers are clean, quotable passages for AI answers.
- **LocalBusiness structured data**: required `name` and `address`; recommended
  `telephone`, `url`, `geo`, `openingHoursSpecification`, `priceRange`, and for
  restaurants `servesCuisine` and `menu`. `review` and `aggregateRating` are only
  for sites that review *other* businesses: **self-serving reviews on your own
  LocalBusiness are not eligible for review stars and go against the guidelines**,
  so the site has none, even though the 4.9 rating is real. [22]
- **Crawlers**: OpenAI runs OAI-SearchBot (ChatGPT search), GPTBot (training) and
  ChatGPT-User (user-triggered fetches). Anthropic runs Claude-SearchBot, ClaudeBot
  and Claude-User, all honouring robots.txt. [23][24] Perplexity runs PerplexityBot and
  Perplexity-User. We allow all of them: a café wants to be quoted.

---

## 2. What the website now does

- **One JSON-LD graph per page** (`web/src/layouts/Base.astro`):
  - `WebSite` (`/#website`), publisher and subject the café.
  - `CafeOrCoffeeShop` (`/#cafe`): name, address (with `addressRegion: Scotland`),
    geo, phone, grouped opening hours (Mon–Sat 09:00–19:00, Sun 09:00–17:00),
    `foundingDate` 2025-11, `servesCuisine`, `priceRange`, `menu`/`hasMenu` → /menu,
    `acceptsReservations` → /book, `hasMap` → Google Maps, `areaServed` Dundee,
    `sameAs` (Instagram, Deliveroo, Just Eat, Maps), `potentialAction` ReserveAction
    → /book and OrderAction → Deliveroo and Just Eat. No `paymentAccepted` (not
    verified), no rating or reviews (see above).
  - `BreadcrumbList` derived from the URL on every indexable page except the home.
  - Page-specific blocks stay as they were: `Menu` (menu), `ItemList` of pre-order
    products (order), `AboutPage` (about), plus the new `FAQPage` (faq).
- **/faq**: 13 questions in the page and the JSON-LD (plus one "ask us" answer for
  wifi, dogs, access and gluten-free, shown on the page but not marked up). Answers
  are generated from `menu.json`/`info.json` in `web/src/pages/_facts.ts`, so a price
  change on the board changes the FAQ, its JSON-LD and llms-full.txt together.
  Unknowns carry `<!-- OWNER TO CONFIRM -->` comments.
- **/llms.txt** and **/llms-full.txt** (llmstxt.org format), built from the same data.
- **robots.txt** names the search and AI crawlers explicitly, keeps `/book/manage`
  and `/admin` disallowed, and links the sitemap.
- **Sitemap** excludes `/admin`, `/book/manage` and 404, and each URL has a
  `lastmod` from its source file's last commit (or mtime when edited/uncommitted),
  not the build time.
- **Meta**: every title ≤ 60 characters and description ≤ 155, `og:image:alt`,
  `twitter:title/description/image/image:alt`. Canonical and noindex unchanged.
- **Fonts**: the two Latin font files are preloaded, so text doesn't swap after
  first paint.

---

## 3. Owner playbook, in priority order

### P1. Google Business Profile (this week; the biggest lever by far)

1. **Add the website URL.** The profile currently says "Add website". Use the bare
   domain once it's live. Until then Google can't connect the site to the listing.
2. **Links**: Menu link → `https://<domain>/menu`. Reservations link → `/book`.
   Order-ahead link → `/order`. Check the Deliveroo and Just Eat order buttons
   Google adds automatically point at the right listings.
3. **Categories**: primary *Café*; secondary *Coffee shop*, *Bubble tea store*,
   *Tea house*, *Breakfast restaurant*, *Brunch restaurant*, *Cake shop*. Pick the
   primary with care; it weighs the most. Only add what's genuinely served.
4. **Attributes**: already women-owned and LGBTQ+ friendly. Tick every attribute
   that is true (dine-in, takeaway, no-contact delivery, table service or counter,
   accepts reservations, and so on). Accessibility, wifi, dogs, toilets and payment
   types: **decide and set them**, because Google and AI assistants answer these
   questions from the profile. Then tell the web team so the FAQ can say the same.
5. **Products / menu items**: add Kyiv cake (slice £4.00, whole £25.00, order
   ahead), blue matcha, Raff coffee, iced matcha, bubble tea, brunch waffles, each
   with a real photo and the same name and price as the menu page.
6. **Description** (750 characters max; no links, no "best"). Suggested, all verified:
   > Independent café at 23 Commercial Street in Dundee city centre, open since
   > November 2025. More than 100 drinks on the board: matcha whisked fresh in front of
   > you, blue matcha, Raff coffee, rose and lavender lattes, bubble tea, milkshakes
   > and iced teas. Breakfast, lunch and brunch waffles, and cakes including Kyiv cake
   > by the slice or whole to order. Oat, almond, coconut and soya milk. Chess and
   > board games in the room. Dine in, take away, or order on Deliveroo and Just Eat.
7. **Photos**: aim for 5–10 new photos a month: drinks as served, the room at
   different times, the front from the street (helps people find the door), the
   menu boards (Google reads menu photos), and the team if you're happy with that.
   Real photos only; no stock.
8. **Posts**: one a week. Seasonal drinks (the autumn hojichas now), a Kyiv cake
   reminder before holidays, opening-hours changes. Posts expire from view; short
   and regular beats long and rare.
9. **Q&A**: Google has been winding down profile Q&A; if the section still shows,
   seed it with 3–5 real questions from the FAQ page and answer them yourself.
10. **Special hours**: set bank holidays and Christmas in advance. Wrong hours are
    the complaint AI assistants repeat most.
11. **Reply to every review** within a few days, briefly and by name of the drink
    or cake they mention ("Glad you liked the rose latte"). Reply to bad ones
    calmly and once. Never offer anything in exchange for a review.

**Maps link**: in the profile, *Share* → copy the `maps.app.goo.gl` link and send it
to the web team. It replaces the search URL now used for `hasMap` and `sameAs`
(`MAPS_URL` in `Base.astro`) and is a stronger identity signal.

### P2. Name, address, phone everywhere (NAP), within a month

Use exactly: **Sasha's Corner**, **23 Commercial Street, Dundee DD1 3DD**,
**07398 433317**, the same hours, and the website URL. One spelling, everywhere.

- **Apple Maps** via Apple Business Connect (Siri and iPhone Maps; Apple
  Intelligence answers from it).
- **Bing Places for Business** (import from Google in a few clicks; ChatGPT search
  and Copilot lean on Bing).
- **Tripadvisor**: claim the listing; it dominates "best cafés/brunch in Dundee".
- **Yelp**: claim it; it ranks for "cafés near Dundee railway station".
- **Facebook**: a page exists (facebook.com/p/Sashas-Corner-61582666970753). If it's
  yours, confirm it and put the URL into `info.socials.facebook`; the site then adds
  it to `sameAs` automatically. If it's not, report it.
- **Visit Dundee** café listing (visitdundee.com), **Dundee Directory**
  (dundeedirectory.co.uk), **Scotland Food & Drink**-type local lists, and the city
  centre BID / Dundee Commercial Street business groups if any exist.
- **Deliveroo and Just Eat**: same name, address, a description that matches the
  profile, categories including coffee, bubble tea and desserts.
- Check the **Food Standards Agency** listing name matches (it does today:
  "Sasha's Corner").

Also check what third-party sites already say: one directory describes the café as
"pet friendly". If that's true, add it to the profile and the site; if not, ask
them to correct it. **Inconsistent facts are what make AI answers wrong.**

### P3. Reviews, ongoing

- Ask at the moment someone says they enjoyed it. A small card or sticker at the
  till with a QR code to the Google review link (*Ask for reviews* in the profile).
- Mention the review link on receipts or bags if you print them.
- A steady trickle (a few a week) beats bursts. Never pay, discount or gate for
  reviews; Google removes them and can suspend the profile.
- Reviews that mention specific things ("blue matcha", "Kyiv cake", "near the
  station") help both Google and AI answers. You can't script them, but you can ask
  "what did you have?" and people tend to write about it.

### P4. AI visibility (GEO), ongoing

AI assistants answer from the web's consensus about an entity. The job is to make
the same few facts appear, consistently, in places they trust.

- **Same facts everywhere**: name, address, hours, what you're known for (matcha
  range, blue matcha, Raff, Kyiv cake, 100+ drinks, board games). The website,
  GBP, Apple, Bing, Tripadvisor, Instagram bio and delivery listings should all say
  the same things in similar words.
- **Local press**: The Courier has covered the café twice and runs "best matcha /
  cosy cafés in Dundee" roundups. Pitch them when there's news: a new seasonal menu,
  blue matcha, Kyiv cake for a holiday. Press pages are heavily cited by AI answers.
  Also Dundee student media (the udsbstudents Substack writes food guides) and
  Visit Dundee.
- **Reddit and forums**: r/dundee threads asking "where for matcha / bubble tea /
  brunch" are cited by Perplexity and ChatGPT. Don't astroturf (it gets removed and
  resented); answer genuinely, as the owner, when someone asks, or let customers do it.
- **Instagram**: bio with address, hours and the website; captions that name the
  drink and "Dundee" in plain words (AI crawlers read captions, not pictures).
- **No Wikipedia needed.** A single café won't meet notability and shouldn't try.
  The entity signals that matter here are: Companies House (Sasha's Corner Ltd,
  SC855371), the FSA hygiene listing, the press articles, the GBP and the listings
  above, all pointing at one website.
- **Check it monthly**: ask ChatGPT, Perplexity, Gemini and Claude "Where can I get
  blue matcha in Dundee?", "cafés near Dundee station", "Kyiv cake Dundee" and "What
  are Sasha's Corner's opening hours?". Note wrong facts and fix them at their source
  (usually a listing).

### P5. Search Console, Bing Webmaster Tools, IndexNow (when the domain is live)

1. **Google Search Console**: add a *Domain* property (DNS TXT record). Submit
   `https://<domain>/sitemap-index.xml`. Use *URL inspection → Request indexing* for
   `/`, `/menu`, `/faq`, `/visit`. Watch *Pages* (indexing) and *Performance*
   (queries) monthly.
2. **Bing Webmaster Tools**: *Import from Google Search Console* (one click). Submit
   the same sitemap. Bing powers ChatGPT search and Copilot, so this is not optional.
3. **IndexNow** (Bing, Yandex, Seznam, Naver): generate a key in Bing Webmaster Tools,
   put the key file at `web/public/<key>.txt`, and after each deploy POST the changed
   URLs to `https://api.indexnow.org/indexnow`. A one-line `curl` in the deploy
   script is enough; ask the web team. Google doesn't use IndexNow; it reads the
   sitemap `lastmod`.
4. Set `SITE_PUBLIC_URL` to the real domain before the production build (canonical,
   sitemap, robots, JSON-LD and llms.txt all follow it).

### P6. Monthly checklist (30 minutes)

- [ ] Hours correct everywhere, including upcoming bank holidays (GBP special hours).
- [ ] 5–10 new photos on GBP; 4 posts in the month.
- [ ] Every new review replied to.
- [ ] Menu boards changed? Run `sashasite menu-export`, rebuild, deploy: the menu
      page, FAQ prices and llms files update together. Update GBP products to match.
- [ ] Search Console: any pages dropped out of the index? Any new queries worth a
      FAQ answer?
- [ ] Bing Webmaster: crawl errors?
- [ ] Ask four AI assistants the four test questions above; fix wrong facts at source.
- [ ] Any new press, blog or Reddit mention? Check its facts; thank the writer.
- [ ] Anything newly confirmed (wifi, dogs, access, gluten-free, whole-cake notice)?
      Tell the web team so the FAQ gets a real answer and the `OWNER TO CONFIRM`
      comment goes.

---

## 4. Open facts that would improve answers

Each of these is asked of AI assistants and has no verified answer yet. The site says
"ask the café" until the owner confirms.

- Wifi; dog policy; wheelchair / step-free access; toilets; parking.
- Gluten-free and vegan options (the milks are verified; "vegan" is not).
- How much notice a whole Kyiv cake needs (the order page says "a few days" and is
  marked OWNER TO CONFIRM).
- Whether the chess set and board games are free for customers to use.
- Payment methods (for `paymentAccepted` and the GBP attribute).
- Bank holiday and Christmas hours.
- The domain (canonical URLs currently assume `sashascorner.co.uk`).

---

## Sources

1. Tripadvisor, cafés in Dundee: https://www.tripadvisor.co.uk/Restaurants-g186518-c8-Dundee_Scotland.html
2. Wanderlog, best coffee shops in Dundee: https://wanderlog.com/list/geoCategory/17348/best-coffee-shops-and-best-cafes-in-dundee
3. Visit Dundee, cafés: https://visitdundee.com/eat-and-drink/cafes-in-dundee/
4. The Courier, seven cosy city-centre cafés: https://www.thecourier.co.uk/fp/lifestyle/food-drink/5411188/cosy-dundee-cafe-review/
5. Wander Scotland, best coffee shops Dundee: https://www.wandersomewhere.com/food-drink/best-coffee-shops-dundee
6. The Courier, 5 best places for matcha in Dundee (25 July 2025): https://www.thecourier.co.uk/fp/lifestyle/food-drink/5294247/where-find-best-matcha-dundee/
7. Matcha Near Me, Dundee: https://matchanearme.com/matcha-in-dundee-uk
8. Just Eat, bubble tea in Dundee: https://www.just-eat.co.uk/takeaway/dundee/bubble-tea
9. Dundee Directory, Boba Time: https://dundeedirectory.co.uk/business/boba-time-dundee/
10. Tripadvisor, brunch in Dundee: https://www.tripadvisor.co.uk/Restaurants-g186518-zfp10606-Dundee_Scotland.html
11. Yelp, cafés near Dundee railway station: https://yelp.com/search?cflt=cafes&find_near=dundee-railway-station-dundee
12. AccessAble, The Dundee Coffee House: https://www.accessable.co.uk/dundee-city-council/access-guides/the-dundee-coffee-house
13. Search for "Kyiv cake Dundee" returned only Dundee cake retailers (e.g. https://clarksbakery.co.uk/pages/buy-traditional-dundee-cake-online-handcrafted-in-dundee)
14. Wikipedia, Kyiv cake: https://en.wikipedia.org/wiki/Kyiv_cake
15. Companies House, SASHA'S CORNER LTD: https://find-and-update.company-information.service.gov.uk/company/SC855371
16. The Courier, new café plan for Commercial Street: https://www.thecourier.co.uk/fp/news/5321707/sashas-corner-dundee-commercial-street/
17. The Courier, opening story: https://www.thecourier.co.uk/fp/news/5371997/ukrainian-couple-dundee-cafe-best-bakes-in-world/
18. FSA food hygiene rating: https://ratings.food.gov.uk/business/1859739/sashas-corner
19. Google Search Central, AI features and your website (updated 10 Dec 2025): https://developers.google.com/search/docs/appearance/ai-features
20. llms.txt adoption and Google's position: https://www.getpassionfruit.com/blog/should-i-create-an-llms.txt-file-google-s-2026-guidance-explained ; spec: https://llmstxt.org/
21. Search Engine Journal, Google drops FAQ rich results: https://www.searchenginejournal.com/google-drops-faq-rich-results-from-search/574429/
22. Google Search Central, LocalBusiness structured data: https://developers.google.com/search/docs/appearance/structured-data/local-business
23. OpenAI crawlers: https://developers.openai.com/api/docs/bots
24. Anthropic crawlers: https://support.claude.com/en/articles/8896518-does-anthropic-crawl-data-from-the-web-and-how-can-site-owners-block-the-crawler
