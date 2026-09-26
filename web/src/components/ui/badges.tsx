/**
 * CountBadge, Pill, TrustPill, MarginChip, StatusTag, TierBadge, Dot:
 * design-system.md §5.5.
 *
 * Semantic colour is always a wash + ink pair. Green never appears as text on
 * white: a fine margin is `ink`, only the pill background says "fine".
 */
import type { ReactNode } from 'react'
import { cx } from './cx'

/** Nav/hamburger count. Renders nothing for 0, null or undefined. */
export function CountBadge({ count, label }: { count: number | null | undefined; label?: string }) {
  if (count === null || count === undefined || count <= 0) return null
  return (
    <span
      className="fig flex-none rounded-full bg-brand px-[7px] py-0.5 text-label font-bold leading-[14px] text-white"
      aria-label={label ? `${count} ${label}` : undefined}
    >
      {count}
    </span>
  )
}

export type PillTone = 'ok' | 'warn' | 'bad' | 'neutral' | 'muted' | 'brand'

const PILL: Record<PillTone, string> = {
  ok: 'bg-ok-wash text-ok-ink',
  warn: 'bg-warn-wash text-warn-ink',
  bad: 'bg-bad-wash text-bad-ink',
  neutral: 'bg-wash text-ink-2',
  // Design: wash/ink-3. ink-3 at 12px fails contrast; ink-2 keeps it legible.
  muted: 'bg-wash text-ink-2 font-semibold',
  brand: 'bg-brand-wash text-brand-ink',
}

export function Pill({ tone = 'neutral', children, className }: { tone?: PillTone; children: ReactNode; className?: string }) {
  return (
    <span
      className={cx(
        'inline-flex h-6 items-center whitespace-nowrap rounded-full px-2.5 text-xs font-bold',
        PILL[tone],
        className,
      )}
    >
      {children}
    </span>
  )
}

export type Trust = 'trusted' | 'drifting' | 'excluded' | 'never_counted' | 'checklist' | 'low'

const TRUST: Record<Trust, { tone: PillTone; text: string }> = {
  trusted: { tone: 'ok', text: 'Trusted' },
  drifting: { tone: 'warn', text: 'Drifting' },
  excluded: { tone: 'bad', text: 'Excluded' },
  never_counted: { tone: 'muted', text: 'Never counted' },
  checklist: { tone: 'neutral', text: 'Checklist' },
  low: { tone: 'bad', text: 'Low' },
}

/** The stock trust pill with the design's words and tones. */
export function TrustPill({ trust, children }: { trust: Trust; children?: ReactNode }) {
  const t = TRUST[trust]
  return <Pill tone={t.tone}>{children ?? t.text}</Pill>
}

/**
 * Margin chip on a menu card. `marginPct` null means no price: neutral, and
 * the text says so. Threshold 60% (design `m < 0.6`).
 */
export function MarginChip({ marginPct, children }: { marginPct: number | null; children?: ReactNode }) {
  const tone = marginPct === null ? 'bg-wash text-ink-2' : marginPct < 60 ? PILL.bad : PILL.ok
  return (
    <span className={cx('fig inline-flex rounded-full px-[9px] py-[3px] text-xs font-bold', tone)}>
      {children ?? (marginPct === null ? 'no price' : `${Math.round(marginPct)}%`)}
    </span>
  )
}

export type StatusTone = 'ink' | 'ink-3' | 'alert'

/**
 * Outline tag. Order status: Draft ink-3; Waiting alert; Confirmed/Sent ink;
 * Received/Bought/Cancelled ink-3.
 */
export function StatusTag({ tone = 'ink', children }: { tone?: StatusTone; children: ReactNode }) {
  return (
    <span
      className={cx(
        'inline-flex items-center whitespace-nowrap rounded-button border px-2 text-xs',
        tone === 'alert' && 'border-alert font-bold text-bad-ink',
        tone === 'ink' && 'border-ink text-ink',
        // Border stays ink-3 as designed; text raised to ink-2 for contrast.
        tone === 'ink-3' && 'border-ink-3 text-ink-2',
      )}
    >
      {children}
    </span>
  )
}

export function TierBadge({ tier }: { tier: 'A' | 'B' | 'C' | string }) {
  return (
    <span
      className="grid size-[22px] flex-none place-items-center rounded-[7px] bg-wash text-label font-bold text-ink-2"
      aria-label={`Tier ${tier}`}
    >
      {tier}
    </span>
  )
}

export function Dot({ tone = 'muted' }: { tone?: 'muted' | 'alert' | 'brand' }) {
  return (
    <span
      className={cx(
        'inline-block size-2 flex-none rounded-full',
        tone === 'muted' && 'bg-ink-3',
        tone === 'alert' && 'bg-alert',
        tone === 'brand' && 'bg-brand',
      )}
      aria-hidden="true"
    />
  )
}
