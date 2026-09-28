# Going live: Sasha's Corner Rewards (and the whole stack)

The deal: **fill `.env`, drop the wallet files into `secrets/wallet/`, run the
`docker compose` commands below.** No file in the repo needs editing. Every URL, path and
proxy setting the containers need is either in `.env` or derived from it in
`docker-compose.yml`.

Run everything from the repo root on the server. `docker compose run --rm api <cmd>`
runs a one-off `cafeops` command inside the app image against the live database.

---

## 0. Before you start (once, outside this repo)

- A VM with Docker Engine and the compose plugin (`docker compose version`).
- Ports 80 and 443 open to the internet.
- DNS A/AAAA records pointing at the VM for:
  - the public site, e.g. `sashascorner.co.uk` **and** `www.sashascorner.co.uk`;
  - the back office, e.g. `ops.sashascorner.co.uk`.

  Caddy fetches the HTTPS certificates itself on the first request; it can only do that
  once DNS points here. Apple Wallet refuses to talk to a pass web service that is not
  on valid HTTPS, so passes cannot work before this step.

## 1. Fill `.env`

```bash
cp .env.example .env
python3 -c 'import secrets; print(secrets.token_urlsafe(48))'   # run once per secret
```

Rule that matters: **an optional setting you are not using stays commented out**
(`# KEY=`). A blank number or true/false (`CAFEOPS_TELEGRAM_OWNER_CHAT_ID=`) stops every
cafeops command from starting.

### Required

| Key | What | How to get it |
|---|---|---|
| `SITE_DOMAIN` | the public site's host, no scheme: `sashascorner.co.uk` | your domain |
| `CAFEOPS_DOMAIN` | the back office's host: `ops.sashascorner.co.uk` | your domain |
| `CAFEOPS_API_PASSWORD` | back-office password (the API refuses to serve without one) | choose one |
| `CAFEOPS_LOYALTY_QR_KEY` | signs every card QR. **Set before the first member joins, never change it** -- changing it voids every card in every wallet. Back it up with `.env`. | generator above |
| `SITE_SERVICE_KEY` | joins the back office's Website screens to the site API | generator above |
| `SITE_ADMIN_PASSWORD` | the site's own admin sign-in until one is set there (recommended) | choose one |

Everything URL-shaped follows `SITE_DOMAIN`: `docker-compose.yml` sets
`CAFEOPS_LOYALTY_PUBLIC_URL`, the wallet public URL and `SITE_PUBLIC_URL` to
`https://$SITE_DOMAIN`. Leave those three commented unless you really need a different
origin (then all three must agree; `cafeops doctor` checks).

### Optional -- each one switches a feature on

| Feature | Keys | Without it |
|---|---|---|
| Telegram (owner alerts, 19:30 loyalty summary, fraud alerts, `/member`) | `CAFEOPS_TELEGRAM_BOT_TOKEN` (@BotFather), `CAFEOPS_TELEGRAM_OWNER_CHAT_ID` (@userinfobot) -- both | messages are logged, not sent; the `bot` container exits by design |
| Card recovery by email | `CAFEOPS_SMTP_HOST`, `_PORT` (587), `_USER`, `_PASSWORD`, `_FROM` | recovery says "ask staff" |
| Card recovery by SMS | `CAFEOPS_TWILIO_ACCOUNT_SID`, `_AUTH_TOKEN`, `_FROM_NUMBER` (+44...) | as above |
| Apple Wallet | `CAFEOPS_WALLET_APPLE_PASS_TYPE_ID`, `CAFEOPS_WALLET_APPLE_TEAM_ID` + files (section 2) | no Apple button; web card only |
| Google Wallet | `CAFEOPS_WALLET_GOOGLE_ISSUER_ID` + file (section 2) | no Google button; web card only |
| Lightspeed sales | `CAFEOPS_LIGHTSPEED_CLIENT_ID`, `_CLIENT_SECRET`, `_REFRESH_TOKEN`, `_BUSINESS_ID` -- all four | nightly sync replays fixtures |
| Auto-stamping from the till | `CAFEOPS_LOYALTY_AUTO_STAMP=true` (needs Lightspeed, and the till attaching customers to sales) | staff stamp from the scanner |
| Site analytics | `PUBLIC_SWETRIX_PID` (build-time) | no analytics script at all |
| Narration agent | `CAFEOPS_ANTHROPIC_API_KEY` | template sentences |
| Off-box backups | `CAFEOPS_BACKUP_REMOTE` (+ SSH key, docs/OPERATIONS.md) | local-only backups, logged nightly |

`.env.example` has a one-line explanation for every key, including the defaults you can
leave alone.

## 2. Wallet files (only for the wallets you set up)

How to obtain them: [`WALLET-SETUP.md`](WALLET-SETUP.md) (allow 2-3 weeks for Apple's
organisation check and Google's class review). Then:

```bash
mkdir -p secrets/wallet
cp pass.pem pass.key wwdr.pem google-sa.json secrets/wallet/   # whichever you have
sudo chown -R 1000:1000 secrets/wallet     # the app runs as uid 1000 in the container
chmod 700 secrets/wallet && chmod 600 secrets/wallet/*
```

`secrets/` is git-ignored and excluded from every image (`.dockerignore`).
`docker-compose.yml` mounts `./secrets/wallet` **read-only** at `/secrets/wallet` into
`api` (builds passes, pushes right after a stamp) and `scheduler` (retries pushes every
minute) -- nowhere else. With exactly these file names no path needs setting:

| File | Default path in the container | Override key |
|---|---|---|
| `pass.pem` Pass Type ID certificate | `/secrets/wallet/pass.pem` | `CAFEOPS_WALLET_APPLE_CERT_PATH` |
| `pass.key` its private key | `/secrets/wallet/pass.key` | `CAFEOPS_WALLET_APPLE_KEY_PATH` (+ `_KEY_PASSWORD` if encrypted) |
| `wwdr.pem` Apple WWDR G4 | `/secrets/wallet/wwdr.pem` | `CAFEOPS_WALLET_APPLE_WWDR_PATH` |
| `google-sa.json` service-account key | `/secrets/wallet/google-sa.json` | `CAFEOPS_WALLET_GOOGLE_SERVICE_ACCOUNT_PATH` |

## 3. Build, migrate, start

```bash
docker compose build            # reads .env for the build args (site URL, Swetrix id)
docker compose up -d            # migrate + site-migrate run first, then everything else
docker compose ps               # migrate/site-migrate/site-static "Exited (0)", the rest Up
```

`migrate` brings the ops database to head (including the loyalty and wallet tables and
the default "stamp" programme); `site-migrate` does the site's own history (bookings,
events). Both are one-shot and run on every `up`, so a later deploy is the same two
commands. `./start.sh docker` does exactly this.

First install only -- load the café's data (see README.md "Shortest path"):

```bash
docker compose run --rm api cafeops import-legacy --commit
docker compose run --rm api cafeops drift --backfill
```

**After changing `.env`:** `docker compose up -d` (recreates containers with the new
values; `restart` does NOT re-read `.env`). If you changed `SITE_DOMAIN`,
`SITE_PUBLIC_URL` or `PUBLIC_SWETRIX_*`, rebuild the site first, since those are baked
into the pages: `docker compose build site-static && docker compose up -d`.

## 4. Check the install

```bash
docker compose run --rm api cafeops doctor
docker compose run --rm api cafeops wallet doctor      # add --online to test Google
docker compose run --rm api cafeops jobs               # wallet_outbox, loyalty_* listed
docker compose logs --tail=50 scheduler                # "scheduler starting"
curl -fsS https://sashascorner.co.uk/api/loyalty/program
curl -fsS https://ops.sashascorner.co.uk/api/health
```

`doctor` prints one line per integration -- QR key, public URL, SMTP, Twilio, Apple
Wallet, Google Wallet, Telegram, Lightspeed, auto-stamp -- as **configured**, **not
configured** (a supported state) or **MISCONFIGURED** (half set, a file missing or
unreadable, settings that disagree). Fix every MISCONFIGURED before inviting customers.
It exits non-zero only on FAIL.

The `/api/loyalty/program` answer includes `"wallets": {"apple": ..., "google": ...}`:
the join page offers exactly the wallets marked `true`.

## 5. Staff: owner, managers, the till

PINs are 4-6 digits and identify the person, so they must differ. Put a space before
the command so the PIN stays out of shell history.

```bash
 docker compose run --rm api cafeops loyalty staff-add "Sasha" --role owner --pin 482913
 docker compose run --rm api cafeops loyalty staff-add "Name" --role manager --pin 250617
 docker compose run --rm api cafeops loyalty staff-add "Name" --role staff --pin 7302
docker compose run --rm api cafeops loyalty device-pair "Till tablet"
```

`device-pair` prints a 6-digit code valid for 15 minutes. On the till tablet open
`https://sashascorner.co.uk/staff`, enter the code, then a PIN. Add it to the home screen
(it installs as "SC Staff"). Sessions last 12 hours. More staff and devices, and
revoking a lost tablet, are in the back office under Members.

## 6. Print the QR posters

```bash
mkdir -p posters
docker compose run --rm --user "$(id -u):$(id -g)" -v "$PWD/posters:/out" \
    api cafeops loyalty qr-posters --out /out
```

(or on a laptop: `uv run cafeops loyalty qr-posters --out posters --base-url https://sashascorner.co.uk`).
One A6 poster per placement -- `table`, `till`, `cup`, `flyer-uni`, `flyer-centre`
(`/rewards?src=...`), `delivery` (`/menu?src=delivery`), `ig` (`/?src=ig`) -- as SVG
(send to the printer; scales cleanly), 300 dpi PNG, and `qr-posters.pdf` with all
seven. **Scan every one with a phone before printing**: it must open the live site on
the address printed under it. The `src` ends up on each member (Members > Insights, "by
source"), so do not reuse one poster for another placement.

## 7. A test join, end to end

1. On your phone open `https://sashascorner.co.uk/rewards?src=till`, join with your own
   email, tick terms. You land on your web card `/c/<id>`.
2. On the till scanner: scan that QR, +1. The web card shows 1 stamp on refresh; the
   back office shows you under Members with source `till`.
3. Undo it from the scanner (within 2 minutes), or leave it.
4. Recovery: on `/rewards`, use the recovery option with the same email. With SMTP set
   a code arrives; without, the page says to ask staff (scanner > Find member).
5. When done: "Delete my card" on the web card erases you.

## 8. Wallet passes on real phones

**iPhone (Apple Wallet)** -- once `cafeops doctor` says apple wallet *configured*:

1. Join from Safari; tap "Add to Apple Wallet"; the pass shows the stamp strip, your name
   and "member since" on the front; rules, hours, phone and privacy link on the back.
2. Stamp it at the till. Within seconds the pass updates and the lock screen says
   "You've got 1 stamps". Nothing? `cafeops wallet doctor` shows the latest push
   failure; `docker compose logs api | grep "wallet device log"` is Wallet explaining
   itself. Most common causes: HTTPS not live on the site domain, certificate for the
   wrong Pass Type ID, `CAFEOPS_WALLET_APPLE_APNS_USE_SANDBOX=true` on a normal iPhone.
3. Delete the pass and add it again from the web card: same serial, same stamps.

**Android (Google Wallet)** -- once `cafeops doctor` says google wallet *configured*:

1. `docker compose run --rm api cafeops wallet google-class-sync` creates the class.
   Until Google approves it (Pay & Wallet Console > request publishing access), only
   Google accounts added as test users there can save the pass -- add yours.
2. Join from Chrome on Android; "Add to Google Wallet"; stamp at the till. The count and
   strip update within about a minute, with a notification (Google limits how many a
   pass may show per day; beyond that it updates silently).

## 9. What still needs a human

- DNS, the VM, and ports 80/443.
- The Apple Developer organisation account (D-U-N-S, 99 USD/yr), the Pass Type ID
  certificate and its **yearly renewal** (`doctor` warns 30 days ahead).
- The Google issuer account, service account, and Google's class review.
- A Telegram bot, an SMTP provider and/or Twilio, if wanted.
- Real PINs for real people, pairing the till, printing and placing the posters.
- Lightspeed credentials, and the till attaching customers to sales, before
  `CAFEOPS_LOYALTY_AUTO_STAMP=true` does anything.
- Off-box backups: `CAFEOPS_BACKUP_REMOTE` plus an SSH key (docs/OPERATIONS.md).
