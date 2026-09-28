/**
 * Words, figures and small pieces every Members page shares.
 */
import { useState } from 'react'
import type { ReactNode } from 'react'
import { Input, cx } from '../../components/ui'
import type { WriteResult } from '../../lib/api'
import { href } from '../../lib/router'
import type {
  CampaignSegment,
  MemberRow,
  MemberSegment,
  MemberWallet,
  RewardKind,
  StaffRole,
  StampReason,
} from '../../lib/types/members'

/* ---------------------------------------------------------------- words --- */

export const SEGMENT_LABEL: Record<MemberSegment, string> = {
  all: 'All',
  reward_ready: 'Reward ready',
  lapsed_30: 'Lapsed 30+ days',
  opted_in: 'Opted in',
  new_30: 'New, last 30 days',
}

export const CAMPAIGN_SEGMENT: Record<CampaignSegment, { label: string; hint: string }> = {
  ALL_OPTED_IN: { label: 'Everyone opted in', hint: 'Every member who ticked the marketing box.' },
  LAPSED_30: { label: 'Lapsed 30+ days', hint: 'Opted in, and no stamp or reward for 30 days or more.' },
  REWARD_READY: { label: 'Reward ready', hint: 'Opted in, with a free drink waiting on the card.' },
  NEW_30: { label: 'New, last 30 days', hint: 'Opted in, and joined in the last 30 days.' },
}

export const REASON_LABEL: Record<StampReason, string> = {
  PURCHASE: 'Bought a drink',
  PAPER_MIGRATION: 'Paper card moved over',
  MANUAL_FIX: 'Manual correction',
  REDEEM: 'Redeemed',
  REFERRAL: 'Referral',
  UNDO: 'Undone',
}

export const REWARD_LABEL: Record<RewardKind, string> = {
  STAMP_CARD: 'Free drink (full card)',
  BIRTHDAY: 'Birthday drink',
  REFERRAL: 'Referral reward',
}

export const WALLET_LABEL: Record<MemberWallet, string> = {
  apple: 'Apple Wallet',
  google: 'Google Wallet',
  web: 'Web card',
}

export const ROLE_LABEL: Record<StaffRole, string> = { STAFF: 'Staff', MANAGER: 'Manager', OWNER: 'Owner' }

/** The ?src= a member joined from, in words. `null`: came in without one. */
export function sourceLabel(s: string | null): string {
  if (s === null || s === '') return 'No source tag'
  const known: Record<string, string> = {
    table: 'Table QR',
    till: 'Till QR',
    cup: 'Cup QR',
    'flyer-uni': 'Flyer, university',
    'flyer-centre': 'Flyer, city centre',
    ig: 'Instagram',
    delivery: 'Delivery bag',
  }
  return known[s] ?? s
}

export function contactOf(m: Pick<MemberRow, 'email' | 'phone'>): string {
  return m.email ?? m.phone ?? '—'
}

/** A 0–1 share as a percentage, 1 dp. */
export function share(x: number | null): string {
  if (x === null || !Number.isFinite(x)) return '—'
  return `${(x * 100).toFixed(1)}%`
}

/** A plain rate, 1 dp: "1.8". */
export function rate(x: number | null): string {
  if (x === null || !Number.isFinite(x)) return '—'
  return x.toFixed(1)
}

/** "DD-MM" -> "14 March". */
export function birthdayText(b: string | null): string {
  if (!b) return '—'
  const m = /^(\d{2})-(\d{2})$/.exec(b)
  if (!m) return b
  const months = ['January', 'February', 'March', 'April', 'May', 'June', 'July', 'August', 'September', 'October', 'November', 'December']
  return `${Number(m[1])} ${months[Number(m[2]) - 1] ?? m[2]}`
}

export function signed(n: number): string {
  return n > 0 ? `+${n}` : n < 0 ? `−${Math.abs(n)}` : '0'
}

/* ------------------------------------------------------------ the tabs --- */

const TABS = [
  { id: 'list', label: 'Members', path: '/members' },
  { id: 'insights', label: 'Insights', path: '/members/insights' },
  { id: 'campaigns', label: 'Campaigns', path: '/members/campaigns' },
  { id: 'staff', label: 'Staff & devices', path: '/members/staff' },
  { id: 'programme', label: 'Programme', path: '/members/programme' },
] as const
export type MembersTab = (typeof TABS)[number]['id']

export function MembersTabs({ current }: { current: MembersTab }) {
  return (
    <span className="inline-flex flex-wrap items-center gap-1" role="navigation" aria-label="Members sections">
      {TABS.map((t) => (
        <a
          key={t.id}
          href={href(t.path)}
          aria-current={t.id === current ? 'page' : undefined}
          className={cx(
            'inline-flex h-8 items-center whitespace-nowrap rounded-full px-3.5 text-base font-bold no-underline transition-colors',
            t.id === current ? 'bg-brand-wash text-brand-ink' : 'text-ink-2 hover:bg-canvas hover:text-ink',
          )}
        >
          {t.label}
        </a>
      ))}
    </span>
  )
}

/* ----------------------------------------------------------------- PINs --- */

export const PIN_RE = /^\d{4,6}$/

/** A 4–6 digit PIN box: masked, digits only, never autofilled. */
export function PinInput({
  value,
  onChange,
  label,
  className,
}: {
  value: string
  onChange: (v: string) => void
  label?: string
  className?: string
}) {
  return (
    <Input
      type="password"
      inputMode="numeric"
      autoComplete="off"
      maxLength={6}
      aria-label={label}
      placeholder="4–6 digits"
      value={value}
      onChange={(e) => {
        const v = e.target.value.replace(/\D/g, '')
        onChange(v.slice(0, 6))
      }}
      className={cx('fig', className)}
    />
  )
}

/* -------------------------------------------------------------- writes --- */

export type Outcome = { tone: 'ok' | 'bad'; text: string } | null

/** A write's state: busy flag, the last outcome line, and a runner. */
export function useWriteState() {
  const [busy, setBusy] = useState(false)
  const [outcome, setOutcome] = useState<Outcome>(null)
  /** Resolves to `{ data }` when the write landed (data is `null` for a 204), else `null`. */
  async function run<T>(fn: () => Promise<WriteResult<T>>, ok: (data: T) => string | void): Promise<{ data: T } | null> {
    setBusy(true)
    setOutcome(null)
    const r = await fn()
    setBusy(false)
    if (r.kind === 'ok') {
      const text = ok(r.data)
      if (text) setOutcome({ tone: 'ok', text })
      return { data: r.data }
    }
    setOutcome({ tone: 'bad', text: r.message })
    return null
  }
  return { busy, outcome, setOutcome, run }
}

export function OutcomeLine({ outcome, className }: { outcome: Outcome; className?: string }) {
  if (!outcome) return null
  return (
    <p role={outcome.tone === 'bad' ? 'alert' : 'status'} className={cx('text-sm', outcome.tone === 'bad' ? 'text-bad-ink' : 'text-ink-2', className)}>
      {outcome.text}
    </p>
  )
}

/** A white panel with an h2, as on the item and order pages. */
export function Panel({
  title,
  right,
  children,
  className,
  id,
}: {
  title: ReactNode
  right?: ReactNode
  children: ReactNode
  className?: string
  id?: string
}) {
  return (
    <section aria-labelledby={id} className={cx('min-w-0 rounded-card-lg border border-line bg-surface px-4 py-4 sm:px-5', className)}>
      <div className="mb-3 flex flex-wrap items-baseline justify-between gap-x-3 gap-y-1">
        <h2 id={id} className="text-lg font-extrabold tracking-[-.01em]">
          {title}
        </h2>
        {right}
      </div>
      {children}
    </section>
  )
}

/**
 * The card as the customer sees it: a sticker per earned slot (a different one per
 * slot, fixed order), a faint numbered ring for the rest, and the reward sticker when
 * a free drink is waiting. public/stickers/ is a COPY of assets/pass/stickers/ (the
 * canonical art, which also draws the wallet strips); `cafeops wallet assets`
 * refreshes it. Decorative: the count is always in text beside it.
 */
export function StampStickers({ current, required, reward }: { current: number; required: number; reward: boolean }) {
  const n = Math.min(Math.max(required, 1), 12)
  return (
    <span className="flex flex-wrap items-center gap-1" aria-hidden="true">
      {Array.from({ length: n }, (_, i) =>
        i < current ? (
          <img key={i} src={`/stickers/slot-${(i % 8) + 1}.svg`} alt="" width={36} height={36} className="size-8 select-none sm:size-10" draggable={false} />
        ) : (
          <span key={i} className="fig grid size-8 place-items-center sm:size-10">
            <span className="grid size-8 place-items-center rounded-full border border-dashed border-line-strong text-sm text-ink-3 max-sm:size-7">{i + 1}</span>
          </span>
        ),
      )}
      {reward && <img src="/stickers/reward.svg" alt="" width={48} height={48} className="ml-1 size-10 select-none sm:size-12" draggable={false} />}
    </span>
  )
}

/** Eight small circles for a card: filled for stamps, hollow for the rest. */
export function StampDots({ current, required }: { current: number; required: number }) {
  const n = Math.min(Math.max(required, 1), 12)
  return (
    <span className="inline-flex items-center gap-[3px]" aria-hidden="true">
      {Array.from({ length: n }, (_, i) => (
        <span
          key={i}
          className={cx('size-2 rounded-full', i < current ? 'bg-brand' : 'border border-line-strong bg-surface')}
        />
      ))}
    </span>
  )
}
