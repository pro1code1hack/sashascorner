# Sasha's Corner Rewards — technical spec (owner, 2026-09-28)

Verbatim from the owner. Where this file and `CONTRACT.md` differ, **CONTRACT.md wins**
— it records how the spec was fitted to the existing repo.

## Summary
We build our own digital stamp card, Sasha's Corner Rewards. It works like the physical card: 8 stamps, and the 9th coffee is free. It lives in Apple Wallet and Google Wallet, so customers don't install an app. It does what Walo does (wallet passes, stamp rules, push messages, analytics), but it's built in-house with our own design. It runs from the same repo and domain as the website and the café ops system.

## Goals
- Replace paper cards: every new loyalty customer joins digitally within 30 days of launch; paper cards are only converted, not reprinted.
- Get a contact we own for each regular: email or phone, with separate marketing consent.
- Bring people back more often: measure visits per member per month and the share of members with a reward redeemed.
- Zero extra work at the till: stamping takes one scan, under 5 seconds.

Success metrics after 90 days: number of members, share of transactions with a stamp, redemptions per week, repeat-visit rate of members vs. non-members, and push-message return rate. Exact targets are an open question.

## Scope
| Phase | What ships |
|---|---|
| MVP | Join page at /rewards; Apple Wallet and Google Wallet pass; web card fallback; staff scanner (PWA) with +1/+2 stamps and redeem; live pass updates; birthday reward; convert a paper card into a digital one; basic admin list; Telegram alerts |
| Phase 2 | Push campaigns to all members ("new autumn menu"); location notification near 23 Commercial Street; segments (lapsed 30+ days); referral stamp; Swetrix events; dashboard charts |
| Phase 3 | Stamps added automatically from Lightspeed sales; points or spend-based rules; several reward types (free cake slice); multi-card types (matcha club) |
| Out of scope | Native app; payments or stored value; gift cards; selling the platform |

## User flows
1. **Join.** QR on tables/till/cups/flyers opens /rewards?src=<place>. Form: first name, email or phone, optional birthday (day+month). Two checkboxes: terms (required), marketing (optional, unticked). After submit: Add to Apple Wallet / Add to Google Wallet by device, plus a "keep it in the browser" web card.
2. **Stamp.** Staff open /staff on till tablet or phone, scan the pass QR. Screen shows name and count. Default +1; +2/+3 for a group order paid by one person. Pass updates within seconds.
3. **Redeem.** At 8 stamps the pass shows "Free drink ready" and a lock-screen message. Staff scan, tap Redeem, count resets to 0. Any drink qualifies.
4. **Birthday.** From 7 days before to 7 days after the birthday the pass carries a free-drink voucher. Separate reward, doesn't touch stamps.
5. **Paper card conversion.** Staff choose "Convert paper card", enter stickers (1–7), scan the new pass. Stamps added with reason paper_migration.
6. **Lost/new phone.** Customer re-opens /rewards, enters same email/phone, gets a one-time code, re-downloads the same card. Serial stays.

## Wallet passes
One customer, one card, one serial. Server renders it as Apple pass, Google pass, or web card. Server is the single source of truth.

**Apple (PassKit):** storeCard; strip image holds the stamp grid (9 strip PNGs 0–8 at @1x/@2x/@3x from one SVG template). Fields: header "Stamps 5/8"; primary "Free drink after 3 more"; secondary member name + "member since"; back: rules, hours, phone, website, privacy link. Colours: background #E9DCD6, label #9B6038, foreground #474531. Logo = line-art mark. Barcode QR payload `SC1:<card_uuid>:<hmac8>`. webServiceURL = https://[DOMAIN]/wallet/apple + per-pass authenticationToken; implement the 5 endpoints (register device, list serials, get latest pass, unregister, log). On every stamp bump updated_at and send an empty APNs push to each registered device token. changeMessage on header → "You've got 6 stamps" / "Your free drink is ready". Signing: Pass Type ID certificate ($99/yr org account). pass.json + manifest.json SHA-1 + PKCS#7 detached signature, zipped as .pkpass.

**Google Wallet:** one LoyaltyClass; one LoyaltyObject per card: loyaltyPoints.balance = stamps, text module row for the reward, barcode with the same QR payload, heroImage = matching stamp-strip PNG. "Add to Google Wallet" = signed JWT save link per card (issuer account + service account). Updates: PATCH the object on every stamp; lock-screen messages via addMessage with notification.

**Web card** at /c/<card_uuid>: same design, live QR, "Add to Home Screen".

## Architecture
One repo, one domain, one Python backend; loyalty is a module of the ops system. Routing on [DOMAIN]: static site; /rewards, /c/* customer pages; /staff scanner; /admin dashboard; /api/* and /wallet/apple/v1/* backend.

**Plugs into ops:** a redemption is logged as a £0 sale of the chosen drink (stock + COGS stay honest). Telegram: daily 19:30 summary (new members, stamps, rewards redeemed); /member <phone>; alerts for suspicious stamping. Dashboard: Members tab with search, card stamp history, manual adjust (manager only), simple charts. One user table: roles staff / manager / owner.

## Data model (spec sketch)
members(id, first_name, email?, phone?, birthday_day?, birthday_month?, marketing_opt_in, opt_in_at, source, created_at, deleted_at) · programs(id, name, stamps_required=8, reward_text, birthday_reward, active) · cards(id uuid=serial, member_id, program_id, stamps_current, cycles_completed, reward_available, auth_token, qr_secret, created_at, updated_at) · stamp_events(id, card_id, delta, reason purchase|paper_migration|manual_fix|redeem, staff_user_id, device_id, created_at) · rewards(id, card_id, kind stamp_card|birthday, issued_at, expires_at?, redeemed_at?, redeemed_item_id?, staff_user_id) · apple_registrations · google_objects · campaigns (phase 2) · users(id, name, role, pin_hash, telegram_id?)

## APIs (spec sketch)
Public: POST /api/loyalty/join · GET /api/loyalty/card/{id} · GET …/apple.pkpass · GET …/google · POST /api/loyalty/recover → /verify · /wallet/apple/v1/…
Staff: POST /api/staff/login · scan · stamp · redeem · migrate · undo (within 2 min). After each stamp/redeem a background job updates Apple (APNs) and Google (PATCH), retrying with backoff; failure never blocks the till.

## Rules, fraud, privacy
- 1 stamp per hot or cold drink; up to 3 per scan. At 8 the next drink is free; reward doesn't expire while card active; stamps above 8 carry over.
- Birthday drink: ±7 days, once a year; a birthday entered/changed <30 days before the date doesn't qualify that year.
- One card per phone number or email.
- QR payload HMAC-signed; scanner rejects unknown/tampered codes.
- Cooldown: >3 stamps on one card within 10 minutes needs a manager PIN.
- Every event records staff user and device. Telegram alert on unusual patterns (e.g. one staff member >20 stamps an hour).
- Undo only within 2 minutes; later corrections manager-only with a reason.
- Scanner only on registered devices, staff PIN, 12-hour session.
- Privacy (UK GDPR, PECR): data = first name, email or phone, optional birthday (no year), stamp history. Contract basis for the card, consent for marketing (separate, unticked, record when/where). Unsubscribe on every marketing message. Lock-screen messages limited to card updates and ≤2 promos/month. /privacy page: controller, what's stored, retention (deleted 24 months after last activity), how to request deletion. "Delete my card" in the web card deletes the member and voids passes.

## Website additions
/rewards (explain, join form, wallet buttons, FAQ) · /events (upcoming events with RSVP) · /matcha-dundee · /bubble-tea-dundee · /kyiv-cake (links to whole-cake orders).
SEO for every page: unique title + meta description; one H1 with the search phrase; address and hours in text; JSON-LD: CafeOrCoffeeShop site-wide (address, geo, hours, menu URL, acceptsReservations), Menu/MenuItem with prices on menu and SEO pages, Event on each event, FAQPage on Rewards and Visit. Real photos with alt; LCP < 2.5s on 4G; link each SEO page from its menu section; sitemap.xml and robots.txt.

QR attribution: /rewards?src=table|till|cup|flyer-uni|flyer-centre; /menu?src=delivery; /?src=ig — src stored on the member at signup.

Swetrix (later): events rewards_view, join_submit, wallet_add_apple, wallet_add_google, event_rsvp, cake_enquiry, booking_confirm, delivery_click; src as a property; nothing personal.

## Open questions
Premium drinks earn one stamp? · Free 9th drink any drink or price cap? · Birthday reward at launch? · Which company owns the Apple account? · 90-day targets.
