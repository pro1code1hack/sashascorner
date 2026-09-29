/**
 * Small pieces shared by the Members list and one member's card
 * (docs/loyalty/BACKOFFICE-V2.md). Words, pills, the dot meter and the switch row.
 */
import { useId } from 'react'
import { Toggle, cx } from '../../../components/ui'
import type { WalletKind } from '../../../lib/types/loyalty'

/** The `?src=` tag a member joined from, in words. null = the website, untagged. */
export function sourceLabel(src: string | null): string {
  if (src === null || src === '') return 'Website'
  const known: Record<string, string> = {
    'window-sticker': 'Window sticker',
    window: 'Window sticker',
    'counter-qr': 'Counter QR',
    counter: 'Counter QR',
    till: 'Counter QR',
    table: 'Table QR',
    cup: 'Cup QR',
    instagram: 'Instagram',
    ig: 'Instagram',
    'back-office': 'Back office',
    website: 'Website',
    web: 'Website',
  }
  const k = known[src]
  if (k) return k
  const words = src.replace(/[-_]+/g, ' ').trim()
  return words.charAt(0).toUpperCase() + words.slice(1)
}

export function initials(name: string): string {
  const parts = name.trim().split(/\s+/).filter(Boolean)
  const first = parts[0]?.[0] ?? '?'
  const last = parts.length > 1 ? (parts[parts.length - 1]?.[0] ?? '') : ''
  return (first + last).toUpperCase()
}

/**
 * A soft, stable tint per member so the avatar column is scannable. Neutral washes
 * only: a red or amber avatar would read as a crossed threshold (DECISIONS 23).
 */
const AVATAR = ['bg-brand-wash text-brand-ink', 'bg-wash text-ink-2', 'bg-seq-1 text-brand-deep', 'bg-canvas text-ink-2']

export function Avatar({ name, id, size = 32 }: { name: string; id: number; size?: number }) {
  return (
    <span
      aria-hidden="true"
      style={{ width: size, height: size }}
      className={cx('grid flex-none place-items-center rounded-full text-xs font-extrabold', AVATAR[id % AVATAR.length])}
    >
      {initials(name)}
    </span>
  )
}

export const WALLET_NAME: Record<WalletKind, string> = { apple: 'Apple', google: 'Google', web: 'Web' }

export function WalletPill({ wallet }: { wallet: WalletKind | null }) {
  if (wallet === null) return <span className="text-sm text-ink-2">none yet</span>
  const tone = wallet === 'apple' ? 'bg-ink text-white' : wallet === 'google' ? 'bg-wash text-ink-2' : 'bg-brand-wash text-brand-ink'
  return (
    <span className={cx('inline-flex h-6 items-center rounded-full px-2.5 text-xs font-bold', tone)}>{WALLET_NAME[wallet]}</span>
  )
}

/** Up to twelve small circles: filled for stamps, hollow for the rest. Decorative; the count is beside it. */
export function StampDots({ current, required }: { current: number; required: number }) {
  const n = Math.min(Math.max(required, 1), 12)
  return (
    <span className="inline-flex items-center gap-[3px]" aria-hidden="true">
      {Array.from({ length: n }, (_, i) => (
        <span key={i} className={cx('size-2 rounded-full', i < current ? 'bg-brand' : 'border border-line-strong bg-surface')} />
      ))}
    </span>
  )
}

export const MONTHS = ['January', 'February', 'March', 'April', 'May', 'June', 'July', 'August', 'September', 'October', 'November', 'December']

/** "DD-MM" -> "25 March". */
export function birthdayText(b: string | null): string {
  if (!b) return 'not given'
  const m = /^(\d{2})-(\d{2})$/.exec(b)
  if (!m) return b
  return `${Number(m[1])} ${MONTHS[Number(m[2]) - 1] ?? m[2]}`
}

/**
 * The design's relative time: "38 min ago", "2 h ago", "yesterday", "3 days ago",
 * "2 weeks ago", then the date. `null` is said by the caller.
 */
export function since(iso: string, now: number = Date.now()): string {
  const t = Date.parse(iso)
  if (Number.isNaN(t)) return '—'
  const s = Math.max(0, Math.round((now - t) / 1000))
  if (s < 60) return 'just now'
  const m = Math.round(s / 60)
  if (m < 60) return `${m} min ago`
  const h = Math.round(m / 60)
  if (h < 24) return `${h} h ago`
  const today = new Date(now)
  const then = new Date(t)
  const dayDiff = Math.round(
    (Date.UTC(today.getFullYear(), today.getMonth(), today.getDate()) -
      Date.UTC(then.getFullYear(), then.getMonth(), then.getDate())) /
      86_400_000,
  )
  if (dayDiff <= 1) return 'yesterday'
  if (dayDiff < 14) return `${dayDiff} days ago`
  if (dayDiff < 60) return `${Math.round(dayDiff / 7)} weeks ago`
  return shortDate(iso)
}

/** "24 May" this year, "24 May 2025" otherwise. */
export function shortDate(iso: string, withYear: 'auto' | 'always' = 'auto'): string {
  const d = new Date(iso)
  const sameYear = d.getFullYear() === new Date().getFullYear()
  return new Intl.DateTimeFormat('en-GB', {
    day: 'numeric',
    month: 'short',
    ...(withYear === 'always' || !sameYear ? { year: 'numeric' } : {}),
    timeZone: 'Europe/London',
  }).format(d)
}

/** "+447700900123" -> "07700900123": UK numbers as people write them. */
export function phoneText(p: string | null): string | null {
  if (!p) return p
  return p.startsWith('+44') ? `0${p.slice(3)}` : p
}

/**
 * A settings row: a bold label, a muted line under it, and the kit's switch on the
 * right in the programme's brand blue. The switch is named by the label and
 * described by the hint.
 */
export function SwitchRow({
  label,
  hint,
  checked,
  onChange,
  disabled,
}: {
  label: string
  hint: string
  checked: boolean
  onChange: (next: boolean) => void
  disabled?: boolean
}) {
  const id = useId()
  return (
    <div className="flex items-center gap-4 border-t border-line-soft py-3.5">
      <div className="min-w-0 flex-1">
        <div className="text-base font-bold" aria-hidden="true">
          {label}
        </div>
        <div id={`${id}-h`} className="text-sm text-ink-2">
          {hint}
        </div>
      </div>
      <Toggle
        tone="brand"
        checked={checked}
        disabled={disabled}
        describedBy={`${id}-h`}
        onChange={onChange}
        label={<span className="sr-only">{label}</span>}
        className="flex-none"
      />
    </div>
  )
}
