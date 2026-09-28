// The card, as HTML. One renderer for the build-time sample on /rewards and the live
// web card at /c/<id>, so the two can never drift apart. Pure string building: it runs
// in Astro frontmatter and in the browser alike. Styles: ./card.css.
import { qrSvg } from './qr';
import { REWARD_STICKER, stickerImg, stickerSrc } from './stickers';

// ---- API shapes (docs/loyalty/CONTRACT.md §4) --------------------------------------
export interface RewardOption {
  name: string;
  description: string | null;
  max_price_pence: number | null;
}
export interface Program {
  name: string;
  stamps_required: number;
  reward_text: string;
  birthday_reward: boolean;
  referral_stamps: number;
  max_stamps_per_scan: number;
  wallets: { apple: boolean; google: boolean };
  // Additive (absent from an older server).
  slug?: string;
  kind?: 'STAMPS' | 'POINTS' | string;
  description?: string | null;
  reward_ready_label?: string;
  /** null: the free drink can be any drink. */
  reward_max_price_pence?: number | null;
  points_per_pound?: number | null;
  catalogue?: RewardOption[];
}
export interface Reward {
  id: number;
  kind: 'STAMP_CARD' | 'BIRTHDAY' | 'REFERRAL' | string;
  label: string;
  expires_at: string | null;
}
/** A programme as customers see it (phase 3, GET /api/loyalty/programs). */
export interface PublicProgram {
  slug: string;
  name: string;
  kind: 'STAMPS' | 'POINTS' | string;
  description: string | null;
  stamps_required: number;
  points_per_pound: number | null;
  reward_text: string;
  reward_ready_label: string;
  is_default: boolean;
  catalogue?: RewardOption[];
}
/** Another card of the same member (phase 3). */
export interface SiblingCard {
  card_id: string;
  token: string;
  program_slug: string;
  program_name: string;
  program_kind: string;
  stamps_current: number;
  stamps_required: number;
  reward_ready: boolean;
  web_card_url: string;
}
export interface CardState {
  card_id: string;
  first_name: string;
  member_since: string; // YYYY-MM-DD
  program_name: string;
  stamps_required: number;
  stamps_current: number;
  reward_text: string;
  rewards: Reward[];
  qr_payload: string;
  marketing_opt_in: boolean;
  updated_at: string;
  wallets: { apple_pass_url: string | null; google_save_url: string | null };
  // Phase 3 (optional: absent from an older server).
  program_slug?: string;
  program_kind?: 'STAMPS' | 'POINTS' | string;
  points_per_pound?: number | null;
  reward_ready_label?: string;
  program_description?: string | null;
  other_cards?: SiblingCard[];
  joinable?: PublicProgram[];
  // The member's own details (additive).
  referral_stamps?: number;
  birthday_reward?: boolean;
  birthday_day?: number | null;
  birthday_month?: number | null;
  /** YYYY-MM-DD: the first birthday that brings a drink, after the 30-day rule. */
  birthday_counts_from?: string | null;
  contact_masked?: string | null;
}
export interface JoinResult {
  card_id: string;
  token: string;
  web_card_url: string;
  apple_pass_url: string | null;
  google_save_url: string | null;
  extra_cards?: { program_slug: string; card_id: string; token: string; web_card_url: string }[];
}

/** What the page says when the API can't be reached. Matches CONTRACT §0 defaults. */
export const PROGRAM_FALLBACK: Program = {
  name: "Sasha's Corner Rewards",
  stamps_required: 8,
  reward_text: 'Any drink, on us',
  birthday_reward: true,
  referral_stamps: 1,
  max_stamps_per_scan: 3,
  wallets: { apple: false, google: false },
};

// ---- artwork ---------------------------------------------------------------------
// Stamps are stickers, one per slot in a fixed order (./stickers.ts has where the art
// lives). They are decorative: the count is in the stamp list's aria-label.

// The cup line mark, in the logo's single line weight: the voucher icon.
export const CUP_PATHS =
  '<path d="M8.5 12.5h14v5a6.5 6.5 0 0 1-6.5 6.5h-1a6.5 6.5 0 0 1-6.5-6.5Z"/>' +
  '<path d="M22.5 14.5h1.2a2.8 2.8 0 0 1 0 5.6h-1.9"/>' +
  '<path d="M6.5 27h19"/>' +
  '<path d="M13 9.5c0-1.6 1.6-1.6 1.6-3.2M17.4 9.5c0-1.6 1.6-1.6 1.6-3.2"/>';
export const cupSvg = (cls = '') =>
  `<svg class="${cls}" viewBox="0 0 32 32" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">${CUP_PATHS}</svg>`;

// ---- helpers -----------------------------------------------------------------------
export const esc = (s: string) =>
  s.replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' })[c]!);

const fmtMonthYear = new Intl.DateTimeFormat('en-GB', { month: 'long', year: 'numeric', timeZone: 'UTC' });
const fmtDayMonth = new Intl.DateTimeFormat('en-GB', { day: 'numeric', month: 'long', timeZone: 'Europe/London' });
export const memberSince = (ymd: string) => {
  const d = new Date(`${ymd}T12:00:00Z`);
  return Number.isNaN(d.getTime()) ? '' : fmtMonthYear.format(d);
};
export const untilDate = (iso: string | null) => {
  if (!iso) return '';
  const d = new Date(iso);
  return Number.isNaN(d.getTime()) ? '' : fmtDayMonth.format(d);
};

/** Integer pence to "£3.50" / "£3". Money is never a float until this edge. */
export const pounds = (pence: number) => {
  const p = Math.round(pence);
  const whole = Math.floor(p / 100);
  const rest = p % 100;
  return rest ? `£${whole}.${String(rest).padStart(2, '0')}` : `£${whole}`;
};

export const ordinal = (n: number) => {
  const s = ['th', 'st', 'nd', 'rd'];
  const v = n % 100;
  return `${n}${s[(v - 20) % 10] ?? s[v] ?? s[0]}`;
};

export const isPoints = (s: Pick<CardState, 'program_kind'>) => s.program_kind === 'POINTS';
const isMain = (s: Pick<CardState, 'program_slug'>) => (s.program_slug ?? 'stamp') === 'stamp';

/** "Free matcha ready" -> "Free matcha after 3 more": the pass says the same (wallet/content.py). */
export function headline(
  state: Pick<CardState, 'stamps_current' | 'stamps_required' | 'rewards'> & Partial<Pick<CardState, 'reward_ready_label' | 'program_kind'>>,
): string {
  const label = state.reward_ready_label || 'Free drink ready';
  if (state.rewards.some((r) => r.kind === 'STAMP_CARD')) return label;
  const left = Math.max(0, state.stamps_required - state.stamps_current);
  const thing = /\sready$/i.test(label) ? label.replace(/\sready$/i, '') : 'Reward';
  if (isPoints(state)) return `${thing} after ${left} more point${left === 1 ? '' : 's'}`;
  return `${thing} after ${left} more`;
}

/** The sticker strip. A points card shows its progress on 8 slots, as its wallet pass does. */
export function stampsHtml(current: number, required: number, fresh = 0, points = false): string {
  const slotsN = points || required > 20 ? 8 : required;
  const filled = points || required > 20 ? Math.min(8, Math.floor((current * 8) / Math.max(1, required))) : current;
  const slots = Array.from({ length: slotsN }, (_, i) => {
    const on = i < filled;
    const isNew = !points && on && i >= filled - fresh;
    const empty = points ? '' : `<span class="rwcard__n num">${i + 1}</span>`;
    return `<li class="rwcard__slot${on ? ' is-on' : ''}${isNew ? ' is-new' : ''}">${on ? stickerImg(stickerSrc(i), 'rwcard__sticker') : empty}</li>`;
  }).join('');
  const cols = slotsN <= 5 ? slotsN : Math.ceil(slotsN / 2);
  const what = points ? 'points' : 'stamps';
  return `<ol class="rwcard__stamps${points ? ' is-points' : ''}" style="--cols:${cols}" role="img" aria-label="${current} of ${required} ${what}">${slots}</ol>`;
}

export interface CardRenderOptions {
  /** QR contents. On the sample card it is a link to /rewards, never a real payload. */
  qr: string;
  qrLabel: string;
  sample?: boolean;
  /** How many of the filled stamps arrived since the last render (animated in). */
  fresh?: number;
}

export function cardHtml(state: CardState, opts: CardRenderOptions): string {
  const vouchers = state.rewards
    .filter((r) => r.kind !== 'STAMP_CARD')
    .map((r) => {
      const until = untilDate(r.expires_at);
      return `<li class="rwcard__voucher">${cupSvg('rwcard__vcup')}<span><strong>${esc(r.label)}</strong>${until ? `<small>Until ${esc(until)}</small>` : ''}</span></li>`;
    })
    .join('');
  const ready = state.rewards.some((r) => r.kind === 'STAMP_CARD');
  const since = memberSince(state.member_since);
  const points = isPoints(state);
  return `
<article class="rwcard${ready ? ' is-ready' : ''}${opts.sample ? ' is-sample' : ''}" aria-label="${esc(state.program_name)} card">
  <header class="rwcard__top">
    <span class="rwcard__logo" role="img" aria-label="Sasha's Corner"></span>
    <p class="rwcard__count"><span class="rwcard__lbl">${points ? 'Points' : 'Stamps'}</span> <span class="num"><span data-count>${state.stamps_current}</span>/${state.stamps_required}</span></p>
  </header>
  ${isMain(state) ? '' : `<p class="rwcard__prog">${esc(state.program_name)}</p>`}
  ${
    ready
      ? `<div class="rwcard__ready"><div><p class="rwcard__head" data-headline>${esc(headline(state))}</p><p class="rwcard__reward">${esc(state.reward_text)}. Show this at the till.</p></div>${stickerImg(REWARD_STICKER, 'rwcard__prize')}</div>`
      : `<p class="rwcard__head" data-headline>${esc(headline(state))}</p>`
  }
  ${stampsHtml(state.stamps_current, state.stamps_required, opts.fresh ?? 0, points)}
  ${vouchers ? `<ul class="rwcard__vouchers">${vouchers}</ul>` : ''}
  <dl class="rwcard__meta">
    <div><dt class="rwcard__lbl">Member</dt><dd>${esc(state.first_name)}</dd></div>
    ${since ? `<div><dt class="rwcard__lbl">Member since</dt><dd>${esc(since)}</dd></div>` : ''}
  </dl>
  <div class="rwcard__qr">
    ${qrSvg(opts.qr, { label: opts.qrLabel, dark: '#474531' })}
    <p>${opts.sample ? 'Your own code appears here' : 'Show this at the till'}</p>
  </div>
</article>`;
}
