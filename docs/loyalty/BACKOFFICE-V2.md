# Loyalty card — back office v2 (owner's design, 2026-09-28)

Binding contract for the redesign of the Rewards back office into one **Customers ›
Loyalty card** section with four tabs: **Members · Insights · Messages · Programme**.
The owner's screenshots are the visual spec. Where this file and CONTRACT.md disagree
about the back office, this file wins; the public card, scanner and wallet rules in
CONTRACT.md are unchanged except where §4 below says so.

TypeScript shapes: `web/src/lib/types/loyalty.ts` (the source of truth for field names).
Hooks and writes: `web/src/lib/loyalty-api.ts`. All endpoints are under `/api/members`
(behind the shared password, ops domain — never `/api/loyalty`).

## 1. Routes (web)

| Hash path | Screen |
|---|---|
| `#/loyalty` | Members list (default tab) |
| `#/loyalty/members/<member_id>` | One member's card |
| `#/loyalty/insights` | Insights |
| `#/loyalty/messages` | Messages |
| `#/loyalty/programme` | Programme (incl. Staff & devices, and a link to the reward catalogue) |
| `#/loyalty/programme/catalogue` | The existing Catalogue screen (phase-3 reward options / other cards), kept reachable |

Nav: a **Customers** group between Menu and Money with one item, **Loyalty card**.
Old `#/rewards…` and `#/members…` links redirect to the matching `#/loyalty…` path.
Tab header (all tabs): title (or breadcrumb `‹ Members / <Name> #<id>` on a member page)
and a grey segmented tab strip; the Members tab carries a count badge = members with a
reward ready (`counts.reward_ready`).

## 2. Stickers

Eight stickers, fixed catalogue (`assets/pass/stickers/slot-N.svg`, copied to
`web/public/stickers/`):

| key | file | name |
|---|---|---|
| cat | slot-1.svg | Cat |
| seal | slot-2.svg | Seal |
| matcha | slot-3.svg | Matcha |
| boba | slot-4.svg | Boba |
| cake | slot-5.svg | Cake |
| knight | slot-6.svg | Knight |
| latte | slot-7.svg | Rose latte |
| star | slot-8.svg | Star |

- The programme holds the **sticker set**: an ordered list of enabled keys
  (`loyalty_program.stickers`, JSON; NULL = all eight in the order above). At least one.
- A card holds the sticker in each filled slot (`loyalty_card.stickers`, JSON list whose
  length always equals `stamps_current` on a STAMPS card). A new stamp in slot *i*
  (0-based) gets `set[i % len(set)]` unless the caller names a sticker. When a card
  fills and resets, the carry-over slots get fresh stickers. Negative deltas drop from
  the end. If the list is ever shorter than the count (legacy cards), it is padded by the
  same rule on read and on the next write.
- Each stamp event records the sticker(s) it put on the card (`loyalty_stamp_event.sticker`,
  the first one if delta > 1) — history shows "Counter iPad · Cat".
- Changing a slot's sticker (`PUT /api/members/{id}/stickers`) edits the card's list
  only (cosmetic; the ledger is untouched) and refreshes the wallet pass.
- The wallet strip draws the card's own stickers (see §4).

## 3. Endpoints

### Members list — `GET /api/members`
Query: `q`, `segment` (`all|reward_ready|lapsed_30|new_30|opted_in|no_wallet`),
`sort` (`recent` = last visit, `joined`, `stamps`, `name`), `limit`, `offset`.
Response `MembersResponse`: adds `counts` (every segment's size, ignoring `q`) and on
each row `last_visit_at` (last PURCHASE/PAPER stamp or redemption; null if none) —
`last_activity_at` stays.

### Add member — `POST /api/members`
`MemberCreateIn` → `MemberDetail` (201). Name + email or phone required; birthday
optional (`"DD-MM"`); `marketing_opt_in` only if the customer said yes (recorded with
source `back office`); `source` defaults to `back-office`. Terms: recorded as accepted
now (staff confirm the customer agreed — the form says so). Welcome stamp applies.
409 `already_member` on a duplicate contact.

### One member — `GET /api/members/{id}` → `MemberDetail`
Existing fields stay. New: `member.notes`, `member.terms_accepted_at`,
`member.visits_per_month` (distinct local days with a purchase stamp or redemption in
the last 90 days ÷ 3, one decimal; members younger than 90 days use their age in days,
min 30), `member.last_visit_at`, `card` (`CardFace`: stickers per slot, required,
reward id if ready, `can_undo` + `undo_label`), and `history` (`HistoryEntry[]`, newest
first, every stamp/undo/correction/redemption/birthday/welcome as one readable row).

### Edit — `PATCH /api/members/{id}` (`MemberPatchIn`) → `MemberDetail`
Any subset of first_name (full name allowed, ≤40), email, phone, birthday (`"DD-MM"` or
null), marketing_opt_in, notes (≤1000). Email/phone uniqueness → 409 `contact_taken`.
Cannot remove the last contact. Consent changes audited with source `back office`.

### Card actions (one tap, no PIN — the back office is behind the password)
- `POST /api/members/{id}/stamp` `{sticker?}` → `MemberDetail`. One PURCHASE stamp,
  device "Back office" (no staff user), no cooldown check. Fills → reward issued as usual.
- `POST /api/members/{id}/give-reward` `{reward_id?}` → `MemberDetail`. Redeems the
  oldest available reward (or the named one), no drink recorded. 409 when none ready.
- `POST /api/members/{id}/undo-last` → `MemberDetail`. Reverses the newest thing that
  can be reversed on the main card: a redemption (un-redeems it) or a stamp
  (PURCHASE/PAPER_MIGRATION/MANUAL_FIX/welcome → UNDO row, voiding an unredeemed reward
  it completed). No time limit here (back office = manager). 409 with a sentence when
  nothing can be undone (e.g. the stamp completed a reward already given — undo that first).
- `PUT /api/members/{id}/stickers` `{slot, sticker}` → `MemberDetail`.
- `POST /api/members/{id}/send-link` → `SendLinkOut`: emails (or SMS when configured) the
  member their web-card link, which offers Apple/Google Wallet. `delivery: "none"` with
  a sentence when no channel is configured — the link itself is returned so staff can
  copy it (`url`).
- `DELETE /api/members/{id}` unchanged (erase).

### Insights — `GET /api/members/insights?days=30|90|180` → `Insights`
Current window vs the same-length window before. KPIs: members (total now vs at the
start; `joined` in window), active members (distinct members with a purchase stamp in
window) + visits per active member per month, stamps given (net purchase stamps),
free drinks given (redemptions), came back (share of members who joined before the
window and stamped in it), opted in share + count. Series: `weekly` buckets (Monday
local weeks; last one is "this week") of new members, stamps, free drinks. `by_source`,
`hours` (stamps by local hour 7–20), `regulars` (top 5 by stamps in window). `alerts`:
the fraud alerts from the last 30 days (shown only when non-empty).

### Messages — campaigns
- `GET /api/members/campaigns` adds `promos_this_month` (promotional campaigns sent or
  scheduled this calendar month), `audiences` (current opted-in counts per segment incl.
  `BIRTHDAY` = birthday in this calendar month), and on each campaign `status`
  (`draft|scheduled|sent|cancelled`), `returned`, `return_rate` (0..1 or null).
- New segment `BIRTHDAY`. Notices (`is_promo=false`) go to **everyone with a live card**
  in the segment, not only the opted-in (existing rule: "Notices go to everyone with a
  card") — promos only to the opted-in. A notice lands on the wallet pass only; email is
  sent only to opted-in members, notices included, so nobody who said no gets mail.
- `POST /api/members/campaigns` accepts `send_now: true` to create and send in one step.
  Title is optional; blank → the first words of the message (≤40 chars).
- `POST /api/members/campaigns/{id}/cancel` → `Campaign` (only unsent; sets `cancelled_at`).

### Programme — `GET /api/members/program` / `PUT /api/members/program`
`ProgramSettings`: stamps_required (6/8/10/12 in the UI; any 1–20 accepted), reward_text,
birthday_reward, welcome_stamp, stamps_expire (bool; 12 months untouched → reset),
stickers (ordered enabled keys), sticker_catalogue, join_url (the public join page,
`<loyalty_public_url>/rewards` — `site/web/src/pages/rewards.astro` reads `?src=`),
join_sources (known `?src=` tags with member counts), pin_required. PUT takes any subset
+ `manager_pin` when `pin_required`. Changing stamps_required applies to new stamps only
(existing counts stay; a card already at/over the new target fills on its next stamp).

## 4. Backend changes (summary)

Migration on top of the current head: `loyalty_program.stickers JSON`,
`loyalty_program.welcome_stamp BOOL NOT NULL DEFAULT 0`,
`loyalty_program.stamps_expire_months INT NULL`, `loyalty_member.notes VARCHAR(1000)`,
`loyalty_card.stickers JSON`, `loyalty_stamp_event.sticker VARCHAR(20)`,
`loyalty_campaign.cancelled_at DATETIME`. Enums are stored without CHECKs, so
`CampaignSegment.BIRTHDAY` and `StampReason.WELCOME` need no constraint change.

- `stamping._apply` is still the only writer of stamp events; it maintains
  `card.stickers` and `event.sticker`.
- Welcome stamp: `join()` (and back-office add) gives +1 `WELCOME` when the programme
  says so. It is not a visit: stats, the cooldown and the referral rule ignore it.
- Stamps expire: `stamping.expire_stamps`, run daily inside the 06:00
  `loyalty_birthdays` job, resets cards whose member has had no activity for
  `stamps_expire_months` (12) — a MANUAL_FIX `-n` with note "stamps expired…".
- Undoing the stamp that filled a card brings the stamps back but not their stickers
  (the list was reset when the card filled); the restored slots get the default rotation.
- Wallet: `strip_png(..., stickers=)` draws the card's stickers; the public strip URL
  gains `s=<keys joined by '.'>`; Apple and Google builders pass the card's list;
  `CardView` gains `stickers`. The public web card (`/api/loyalty/card/{id}`) exposes
  `stickers` too so the site can draw them later.
- Migration: `d5e1a7c30b42_loyalty_card_v2` (revises `c4d2e8a91f07`).
