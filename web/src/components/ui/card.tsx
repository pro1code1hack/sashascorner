/**
 * Card, GridCard, Tile, InfoPanel, EstNote, WarnBox, DashedPanel, Empty,
 * Loading, ErrorBox: design-system.md §5.7.
 *
 * Elevation is carried by borders. No stat cards, no sparklines, no delta
 * pills (dropped in v2).
 */
import type { ElementType, ReactNode } from 'react'
import { ApiError } from '../../lib/api'
import { cx } from './cx'

export function Card({
  children,
  soft = false,
  as: As = 'div',
  className,
}: {
  children: ReactNode
  /** Recipe component / flavour cards: softer border. */
  soft?: boolean
  as?: ElementType
  className?: string
}) {
  return (
    <As
      className={cx(
        'rounded-card border bg-surface px-3.5 py-3',
        soft ? 'border-line-soft' : 'border-line',
        className,
      )}
    >
      {children}
    </As>
  )
}

/** Menu-grid card. Clickable, so it is a button. */
export function GridCard({
  children,
  selected,
  inactive,
  onClick,
  label,
  className,
}: {
  children: ReactNode
  selected?: boolean
  /** Off the menu: the photo fades and text goes ink-2; text never drops below AA (was opacity-55). */
  inactive?: boolean
  onClick?: () => void
  label?: string
  className?: string
}) {
  return (
    <button
      type="button"
      onClick={onClick}
      aria-label={label}
      aria-pressed={selected}
      className={cx(
        'flex flex-col overflow-hidden rounded-card-lg bg-surface text-left transition-shadow',
        selected ? 'shadow-selected' : 'shadow-raised',
        inactive && 'text-ink-2 [&_img]:opacity-50',
        className,
      )}
    >
      {children}
    </button>
  )
}

/** Grey figure tile: Sell price / Costs us / Margin. `tone` takes a threshold wash. */
export function Tile({
  label,
  children,
  tone = 'plain',
  est,
  className,
}: {
  label: ReactNode
  children: ReactNode
  tone?: 'plain' | 'ok' | 'bad'
  /** The figure is an estimate: italic. */
  est?: boolean
  className?: string
}) {
  return (
    <div
      className={cx(
        'min-w-0 rounded-button px-3 py-2.5',
        tone === 'plain' && 'bg-canvas',
        tone === 'ok' && 'bg-ok-wash text-ok-ink',
        tone === 'bad' && 'bg-bad-wash text-bad-ink',
        className,
      )}
    >
      <div className={cx('text-xs font-bold', tone === 'plain' ? 'text-ink-2' : 'opacity-90')}>{label}</div>
      <div className={cx('fig truncate text-xl font-extrabold tracking-[-.01em]', est && 'italic')}>{children}</div>
    </div>
  )
}

/** "Made from the recipe X" panel, with an optional on-wash action. */
export function InfoPanel({ children, action }: { children: ReactNode; action?: ReactNode }) {
  return (
    <div className="flex flex-col gap-2 rounded-button bg-brand-wash px-3.5 py-3 text-base text-brand-deep">
      <div>{children}</div>
      {action && <div>{action}</div>}
    </div>
  )
}

/** "*Italic* costs are estimates" note. */
export function EstNote({ children }: { children?: ReactNode }) {
  return (
    <div className="rounded-control bg-est-wash px-3 py-2 text-sm text-ink-2">
      {children ?? (
        <>
          <em>Italic</em> costs are estimates.
        </>
      )}
    </div>
  )
}

/** Dashed alert box: "Terms are guesses", "Needs a look". */
export function WarnBox({ children, className }: { children: ReactNode; className?: string }) {
  return (
    <div className={cx('rounded-button border-[1.5px] border-dashed border-alert px-3 py-2 text-base', className)}>
      {children}
    </div>
  )
}

/** Dashed neutral panel: "Shop run · can't wait for a delivery". */
export function DashedPanel({ children, className }: { children: ReactNode; className?: string }) {
  return (
    <div className={cx('rounded-card border-[1.5px] border-dashed border-line-strong px-3.5 py-3', className)}>
      {children}
    </div>
  )
}

/** "Nothing in this view." Centred, ink-2 for contrast (design: ink-3). */
export function Empty({
  children,
  action,
  roomy = false,
}: {
  children: ReactNode
  action?: ReactNode
  /** 60px padding instead of 40. */
  roomy?: boolean
}) {
  return (
    <div className={cx('flex flex-col items-center gap-3 text-center text-md text-ink-2', roomy ? 'p-15' : 'p-10')}>
      <div className="max-w-[48ch]">{children}</div>
      {action}
    </div>
  )
}

export function Loading({ what = 'Loading' }: { what?: string }) {
  return (
    <div className="p-10 text-center text-md text-ink-2" role="status" aria-live="polite">
      {what}…
    </div>
  )
}

/** A failed read, in words. Shows the server's message when it sent one. */
export function ErrorBox({ error, what }: { error: unknown; what?: string }) {
  let message = error instanceof Error ? error.message : String(error)
  if (error instanceof ApiError) {
    const p = error.payload as { detail?: unknown } | null
    if (p && typeof p.detail === 'string') message = p.detail
    else if (error.status === 404) message = 'This part of the back office is not on the server yet.'
  }
  return (
    <div role="alert" className="m-5 rounded-card border border-line bg-alert-wash px-3.5 py-3 text-base">
      <div className="font-bold">{what ? `Couldn't load ${what}.` : "Couldn't load this."}</div>
      <div className="text-sm text-bad-ink">{message}</div>
    </div>
  )
}
