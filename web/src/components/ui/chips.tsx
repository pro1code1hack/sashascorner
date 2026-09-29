/**
 * FilterChip, Segmented, LinkChip: design-system.md §5.4.
 *
 * One filter-pill language app-wide: the soft FilterChip. The design's legacy
 * solid pill (Finance months, Agents run-log filters, Orders view switch) is
 * replaced by it, per the spec's recommendation.
 */
import { useRef } from 'react'
import type { KeyboardEvent, ReactNode } from 'react'
import { cx } from './cx'

export function FilterChip({
  active,
  onClick,
  count,
  tone = 'default',
  size = 34,
  children,
}: {
  active: boolean
  onClick: () => void
  /** Trailing count, 12px at full opacity (opacity-60 measured 2.6–4.1:1). Omit rather than pass 0 when unknown. */
  count?: ReactNode
  /** `alert` is Finance's "Needs a look": red border, solid red when active. */
  tone?: 'default' | 'alert'
  /** 36 is the compact category rail. */
  size?: 34 | 36
  children: ReactNode
}) {
  return (
    <button
      type="button"
      aria-pressed={active}
      onClick={onClick}
      className={cx(
        'flex flex-none items-center gap-1.5 whitespace-nowrap rounded-full px-3.5 text-base transition-[background-color,border-color]',
        size === 34 ? 'h-[34px]' : 'h-9',
        tone === 'alert'
          ? active
            ? 'border-2 border-alert bg-alert font-bold text-white'
            : 'border-2 border-alert font-medium text-ink hover:bg-alert-wash'
          : active
            ? 'border border-brand-line bg-brand-wash font-bold text-brand-ink'
            : 'border border-line-control bg-surface font-medium text-ink hover:bg-canvas',
      )}
    >
      {children}
      {count !== undefined && (
        <span className={cx('fig text-xs font-semibold', active ? 'text-brand-ink' : 'text-ink-2')}>{count}</span>
      )}
    </button>
  )
}

/**
 * A row of chips. `scroll` keeps them on one line and scrolls inside itself
 * (the compact category rail); otherwise they wrap.
 */
export function FilterChipRow({
  children,
  scroll = false,
  label,
  className,
}: {
  children: ReactNode
  scroll?: boolean
  /** Accessible name for the group: "Filter stock". */
  label: string
  className?: string
}) {
  return (
    <div
      role="group"
      aria-label={label}
      className={cx('flex items-center gap-2', scroll ? 'scroll-x -mx-1 px-1 pb-1' : 'flex-wrap', className)}
    >
      {children}
    </div>
  )
}

export interface SegmentedOption<T extends string> {
  value: T
  label: ReactNode
  /** Accessible name when `label` is a glyph. */
  ariaLabel?: string
}

/** Radio-style segmented control (Stock's Tier All/A/B/C). Arrow keys move. */
export function Segmented<T extends string>({
  options,
  value,
  onChange,
  label,
  showLabel = false,
  className,
}: {
  options: readonly SegmentedOption<T>[]
  value: T
  onChange: (v: T) => void
  /** Accessible name; shown as a leading `text-xs text-ink-2` label if `showLabel`. */
  label: string
  showLabel?: boolean
  className?: string
}) {
  const refs = useRef<Array<HTMLButtonElement | null>>([])
  const onKey = (e: KeyboardEvent, i: number) => {
    const d = e.key === 'ArrowRight' || e.key === 'ArrowDown' ? 1 : e.key === 'ArrowLeft' || e.key === 'ArrowUp' ? -1 : 0
    if (d === 0) return
    e.preventDefault()
    const n = (i + d + options.length) % options.length
    const next = options[n]
    if (next === undefined) return
    onChange(next.value)
    refs.current[n]?.focus()
  }
  return (
    <div
      role="radiogroup"
      aria-label={label}
      className={cx('inline-flex h-10 items-center gap-0.5 rounded-button bg-wash p-[3px]', className)}
    >
      {showLabel && (
        <span className="px-2 text-xs text-ink-2" aria-hidden="true">
          {label}
        </span>
      )}
      {options.map((o, i) => {
        const on = o.value === value
        return (
          <button
            key={o.value}
            ref={(el) => {
              refs.current[i] = el
            }}
            type="button"
            role="radio"
            aria-checked={on}
            aria-label={o.ariaLabel}
            tabIndex={on ? 0 : -1}
            onClick={() => onChange(o.value)}
            onKeyDown={(e) => onKey(e, i)}
            className={cx(
              'h-[34px] min-w-[34px] rounded-[calc(var(--radius-button)-3px)] px-2.5 text-base transition-[background-color]',
              on ? 'bg-surface font-bold text-brand-ink shadow-seg' : 'font-medium text-ink hover:text-brand-ink',
            )}
          >
            {o.label}
          </button>
        )
      })}
    </div>
  )
}

/** A clickable chip that goes somewhere ("Used in: Latte"). `quiet` is "Up next". */
export function LinkChip({
  children,
  onClick,
  href,
  quiet = false,
}: {
  children: ReactNode
  onClick?: () => void
  href?: string
  quiet?: boolean
}) {
  const cls = cx(
    'inline-flex items-center border border-line-strong no-underline transition-colors hover:bg-canvas hover:text-ink',
    quiet ? 'rounded-card px-2 text-sm' : 'rounded-button px-3.5 py-1.5 text-base',
  )
  if (href !== undefined) {
    return (
      <a href={href} className={cls}>
        {children}
      </a>
    )
  }
  return (
    <button type="button" onClick={onClick} className={cls}>
      {children}
    </button>
  )
}
