# Where things stand

**The app is running.** <http://localhost/> — password `sashascorner-local-dev`.

It reads your live database, not fixtures. Eight screens: Today, Stock, Orders,
Composition, Menu margin, Channels, Money & P&L, Import review.

---

## Two findings worth acting on

**£140 of whipping cream was bought and thrown away, having never sold once** — 74% of
all expiry waste in the window. Not a recipe problem; an ordering one. Stock → drift
attribution shows it.

**Babyccino M loses money.** 59.6% margin, **−4.8% after labour** at £14.50/hr. Either
the £0.50 price or the 80-second prep time is wrong. Menu margin flags it.

---

## Four things only you can answer

The system refuses to invent these, because a guessed value silences a warning with
fiction — which is worse than the warning.

| What | Why it matters | How to record it |
|---|---|---|
| **6 of 8 suppliers' terms are invented** — Booker, Brakes, Cakesmiths, Cups Direct, Monolith, Nataly | Every cover window and every order quantity is computed from them | `cafeops supplier list`, then `cafeops supplier confirm <name> …`, or the Money screen |
| **100 shelf lives are ESTIMATE defaults** | Shelf life *caps order size* — a wrong one either wastes stock or causes a stockout | `cafeops shelf-life list` ranks by what actually moves (coffee beans first), then `shelf-life set`, or the Stock screen |
| **42 prices are ESTIMATE** | This is what makes the 46% COGS figure untrustworthy | `cafeops set-price --ingredient <name> --pack-cost <pence> --commit` |
| **What does "Nataly (custom)" supply, and through what channel?** (spec §15.5) | It currently has no delivery weekdays and no minimum | tell me and I'll wire it |

`cafeops doctor` reports all of these, and every warning names a command you can run.

## One thing worth doing in the app

**Import review** has 27 detected templates waiting. 318 menu rows are really ~20
patterns; confirming one collapses its rows into a single template that every cost then
recalculates from.

It also fixes something subtle: seasonality resolves through *variant options*, which
only exist once a template is confirmed. `Pumpkin season` is open right now, drives 8
menu items, and is wired to nothing — so when it closes on 30 November those sales will
inflate the December forecast. Confirming the templates is what closes that
(ARCHITECTURE §8S).

11 of the 27 have quantity conflicts where the legacy rows disagree — e.g. *27 items
differ on whole milk at size M (0.18 vs 0.24 L)*. Those are refused until a human
decides, and the screen shows exactly who uses which.

---

## One change is written but not deployed

Docker's daemon became unresponsive at the end of the session — `docker ps` itself
hangs. The containers keep serving normally; only *deploys* are blocked.

The pending change: `/api/health` was returning row counts ("113 ingredients, 16,734
movements") to **unauthenticated** callers. The endpoint being open is correct and
deliberate — health has to answer before anyone has the password, or "is it up?" and
"is my password right?" become the same question — but a liveness probe does not need
the size of the inventory, and this is built to run on a real domain.

Counts are now null unless the caller presents the password. Verified directly:
anonymous gets `status: ok` with nulls, authenticated gets the figures. The frontend
renders "not disclosed" rather than a number. ruff, mypy strict and `tsc` all clean,
and `web/dist` is already rebuilt.

**To land it:** restart Docker Desktop, then

```bash
docker compose build api caddy && docker compose up -d
```

Nothing else is pending.

## Useful commands

```bash
docker compose ps                     # what is running
docker compose exec api cafeops doctor        # is this install operable
docker compose exec api cafeops bot-preview all   # every Telegram flow, dry run
docker compose logs -f api            # follow the API
```

The Telegram bot is **not configured** and exits `78` (`EX_CONFIG`) by design — set
`CAFEOPS_TELEGRAM_BOT_TOKEN` and `CAFEOPS_TELEGRAM_OWNER_CHAT_ID` in `.env`, then
`docker compose up -d bot`. Until then `bot-preview` renders every flow locally.

## Where the reasoning lives

- `docs/SPEC-CONFORMANCE.md` — §1–§13 with the command that proves each
- `ARCHITECTURE.md` — every decision that differs from the brief, and why
- `docs/phase4/DESIGN-LAW.md` — the design system, measured from finsepa.com
- `docs/OPERATIONS.md` — backup/restore, drilled end to end inside the container

## Your paper sketches — found and audited

The three attached images never reached me as chat images, but I found the same pages
at `~/Downloads/Untitled-2/` and audited the build against them. Everything on them is
implemented except two things:

1. **The load balancer** in the architecture sketch is deliberately not built —
   one box, ~40 transactions a day, one SQLite writer with a single writer lock. A
   second instance would contend on that file and make things worse (`CLAUDE.md` §3).
2. **"Payment reports"** from the Lightspeed box was **genuinely missing** — and not
   in the written brief either. It is now built: `cafeops payments import --commit`,
   `GET /api/takings`, and a takings section on Money & P&L.

   Built CSV-first, reading a back-office export, because the Lightspeed payments
   endpoint has never been probed and guessing its shape would make a mapper that
   mis-reads real money silently.

   Both implementations §4.6 calls for are present: a CSV reader, and a browser
   agent that downloads the back office's own export (`cafeops payments
   browser-plan` prints the read-only script it would follow; it contacts nothing
   until a driver is wired). A downloaded file goes through the same reader and the
   same refusals as a hand export — only who fetched it differs.

   **What it still needs from you:** a real export to map against — the shipped
   samples are *shaped like* one, not taken from one. Drop a CSV anywhere and point
   `CAFEOPS_PAYMENTS_CSV_DIR` at it; the reader will either map it by column name or
   refuse it and tell you which column it could not find. It never guesses.

   One export that breaks out refunds, fees **and** discounts is all it takes for the
   net figure to compute on its own.

   **Check yours before importing:** `cafeops payments schemas` prints every column
   header the reader accepts and every payment-method word it recognises. Columns are
   matched by name after normalising, never by position, so order and capitalisation
   do not matter and an unexpected extra column is ignored rather than mis-read.

Full audit in `docs/SPEC-CONFORMANCE.md`.
