/**
 * The figure primitives. Every number in the application is rendered through
 * one of these, which is how the rules in DESIGN.md §1 stay true in one place
 * instead of being remembered in forty.
 */
import type { CSSProperties, ReactNode } from 'react'
import { costView, pence, penceSigned, trimQty } from '../lib/format'
import type { Cost, Unit } from '../lib/types'

type FigSize = 'micro' | 'sub' | 'base' | 'lg'

const FIG_SIZE: Record<FigSize, string> = {
  micro: 'text-[length:var(--text-micro)]',
  sub: 'text-[length:var(--text-fig-sub)]',
  base: 'text-[length:var(--text-fig)]',
  lg: 'text-[length:var(--text-fig-lg)]',
}

export function Fig({
  children,
  size = 'base',
  sizeClass,
  weight = 400,
  className = '',
}: {
  children: ReactNode
  size?: FigSize
  /** Escape hatch for a figure whose size is responsive (the theoretical
   *  on-hand figure is larger on a phone, where it is the whole row). */
  sizeClass?: string
  weight?: 300 | 400 | 500
  className?: string
}) {
  return (
    <span
      className={`fig ${sizeClass ?? FIG_SIZE[size]} ${className}`}
      style={{ fontWeight: weight }}
    >
      {children}
    </span>
  )
}

/**
 * A figure and its exact remainder. The head is the value TRUNCATED at two
 * decimal places, the tail is every digit after that, so head+tail is the
 * value — no rounding is hidden. The tail is subordinate by weight and colour
 * and sits on the baseline in the same figure column; it is never a tooltip,
 * because a tooltip is a thing you have to suspect exists. DESIGN.md §1 R3.
 */
export function Exact({
  head,
  tail,
  suffix,
  size = 'base',
  weight = 400,
}: {
  head: string
  tail: string
  suffix?: string
  size?: FigSize
  weight?: 300 | 400 | 500
}) {
  return (
    <Fig size={size} weight={weight}>
      <span className="tabular-nums">{head}</span>
      {tail !== '' && (
        <span className="text-muted text-[0.85em]" aria-label={`exact remainder ${tail}`}>
          {tail}
        </span>
      )}
      {suffix !== undefined && <span className="text-muted">{suffix}</span>}
    </Fig>
  )
}

/** Money in the exact-tail contexts: always pence, never switching unit. */
export function PenceExact({
  value,
  signed = false,
  size = 'base',
  weight = 400,
}: {
  value: string | number
  signed?: boolean
  size?: FigSize
  weight?: 300 | 400 | 500
}) {
  const { head, tail } = signed ? penceSigned(value) : pence(value)
  return <Exact head={head} tail={tail} suffix="p" size={size} weight={weight} />
}

/**
 * Invariant 8 in a component. A missing cost renders as an em-rule and the
 * words "no price" — never as 0, and never silently. An estimate is marked
 * typographically (dotted underline plus its source in brackets) and not
 * chromatically, because 42 of 113 prices are estimates and the ambient
 * condition of the books is not an alarm.
 */
export function CostFig({
  cost,
  suffix = '',
  size = 'base',
  weight = 400,
  showSource = true,
}: {
  cost: Cost | null | undefined
  suffix?: string
  size?: FigSize
  weight?: 300 | 400 | 500
  showSource?: boolean
}) {
  const v = costView(cost)
  if (v.kind === 'missing') {
    return (
      <span className="whitespace-nowrap">
        <Fig size={size} weight={weight} className="text-flag">
          —
        </Fig>{' '}
        <span className="text-flag text-[length:var(--text-micro)]">no price</span>
      </span>
    )
  }
  return (
    <span className="whitespace-nowrap">
      <span className={v.isEstimate ? 'est' : undefined}>
        <Exact head={v.head} tail={v.tail} suffix={`p${suffix}`} size={size} weight={weight} />
      </span>
      {showSource && v.isEstimate && (
        <span className="text-muted text-[length:var(--text-micro)]"> ⟨estimate⟩</span>
      )}
    </span>
  )
}

/** A quantity as sent, trailing zeros trimmed, unit always attached. The
 *  string from the API is authoritative; it is never re-derived. */
export function Qty({
  value,
  unit,
  size = 'base',
  sizeClass,
  weight = 400,
  className = '',
}: {
  value: string
  unit?: Unit | string | null
  size?: FigSize
  sizeClass?: string
  weight?: 300 | 400 | 500
  className?: string
}) {
  return (
    <span className={`whitespace-nowrap ${className}`}>
      <Fig size={size} sizeClass={sizeClass} weight={weight}>
        {trimQty(value)}
      </Fig>
      {unit ? (
        <Fig size="micro" weight={400} className="text-muted">
          {' '}
          {unit}
        </Fig>
      ) : null}
    </span>
  )
}

/**
 * A hanging marginal note. DESIGN.md §8.3: the left gutter is reserved for
 * these and nothing else may occupy it. Below 720px the gutter collapses and
 * the note becomes the line above — the same reading order either way.
 */
export function Marg({ children, flag = false }: { children: ReactNode; flag?: boolean }) {
  return <div className={`marg ${flag ? 'text-flag' : ''}`}>{children}</div>
}

export function Sheet({
  children,
  className = '',
  style,
}: {
  children: ReactNode
  className?: string
  style?: CSSProperties
}) {
  return (
    <div className={`sheet ${className}`} style={style}>
      {children}
    </div>
  )
}

export function Body({
  children,
  className = '',
  style,
}: {
  children: ReactNode
  className?: string
  style?: CSSProperties
}) {
  return (
    <div className={`body-col ${className}`} style={style}>
      {children}
    </div>
  )
}

/** A label. Mono, lower case, muted — never tracked-out caps. */
export function Label({ children, className = '' }: { children: ReactNode; className?: string }) {
  return (
    <span className={`fig text-[length:var(--text-micro)] text-muted ${className}`}>
      {children}
    </span>
  )
}
