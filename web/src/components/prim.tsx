/**
 * Figure primitives for Stock and Composition, layered on the shared kit's
 * `<Fig>` so the rules in the brief hold in one place instead of forty.
 *
 * Everything here delegates its size and tone to `ui.tsx`. What it adds is the
 * three things the two data-dense screens need and the kit does not carry:
 *
 *   - `Exact` — a figure plus its exact remainder, head truncated at 2dp and
 *     the tail printed subordinate on the same baseline, so no rounding hides.
 *   - `CostFig` — invariant 8 as a component: a missing cost is "no price",
 *     never 0, and an estimate is marked typographically rather than in colour
 *     (42 of 113 prices are estimates; the ambient condition is not an alarm).
 *   - `Qty` — the API's own quantity string, trailing zeros trimmed, unit
 *     attached. Never re-derived, never parsed to a float.
 */
import type { CSSProperties, ReactNode } from 'react'
import { Fig as KitFig, type Tone } from './ui'
import { costView, pence, penceSigned, trimQty } from '../lib/format'
import type { Cost, Unit } from '../lib/types'

/**
 * `micro` and `sub` are the two sizes below the body figure. They map onto the
 * kit's `sm` and then step the type down again, because the kit deliberately
 * stops at `sm` -- anything smaller is a margin annotation, not a figure.
 */
export type FigSize = 'micro' | 'sub' | 'sm' | 'base' | 'lg' | 'xl'
export type FigNamedWeight = 'light' | 'normal' | 'medium' | 'semibold'
/**
 * Numeric weights are the older spelling and stay accepted so the screens can
 * migrate one at a time rather than in one 46-call-site sweep.
 */
export type FigWeight = FigNamedWeight | 300 | 400 | 500 | 600

const WEIGHT: Record<FigNamedWeight, string> = {
  light: 'font-light',
  normal: 'font-normal',
  medium: 'font-medium',
  semibold: 'font-semibold',
}

const NUMERIC_WEIGHT: Record<number, FigNamedWeight> = {
  300: 'light',
  400: 'normal',
  500: 'medium',
  600: 'semibold',
}

/** The sizes the kit's Fig actually understands. */
type KitFigSize = 'sm' | 'base' | 'lg' | 'xl'

const SIZE: Record<FigSize, { kit: KitFigSize; step: string }> = {
  micro: { kit: 'sm', step: 'text-[0.6875rem] leading-tight' },
  sub: { kit: 'sm', step: 'text-xs' },
  sm: { kit: 'sm', step: '' },
  base: { kit: 'base', step: '' },
  lg: { kit: 'lg', step: '' },
  xl: { kit: 'xl', step: '' },
}

function weightClass(w: FigWeight | undefined): string {
  if (w === undefined) return ''
  return WEIGHT[typeof w === 'number' ? (NUMERIC_WEIGHT[w] ?? 'normal') : w]
}

/** An estimated figure is marked by a dotted rule under it, not by colour. */
export const EST = 'underline decoration-dotted decoration-ink-4 underline-offset-[3px]'

export function Fig({
  children,
  size = 'base',
  weight,
  tone = 'plain',
  missing,
  className = '',
}: {
  children?: ReactNode
  size?: FigSize
  weight?: FigWeight
  tone?: Tone
  /** Why there is no number. Renders in place of it — never beside it. */
  missing?: string
  className?: string
}) {
  if (missing !== undefined) return <KitFig missing={missing} className={className} />
  const s = SIZE[size]
  return (
    <KitFig size={s.kit} tone={tone} className={`${s.step} ${weightClass(weight)} ${className}`}>
      {children}
    </KitFig>
  )
}

/**
 * A figure and its exact remainder. `head` is the value TRUNCATED at two
 * decimals, `tail` every digit after, so head+tail is the value exactly. The
 * tail is subordinate by weight and colour and sits in the same figure column;
 * it is never a tooltip, because a tooltip is a thing you have to suspect
 * exists.
 */
export function Exact({
  head,
  tail,
  suffix,
  size = 'base',
  weight,
  tone = 'plain',
}: {
  head: string
  tail: string
  suffix?: string
  size?: FigSize
  weight?: FigWeight
  tone?: Tone
}) {
  return (
    <Fig size={size} weight={weight} tone={tone} className="whitespace-nowrap">
      {head}
      {tail !== '' && (
        <span className="text-ink-3 text-[0.85em]" aria-label={`exact remainder ${tail}`}>
          {tail}
        </span>
      )}
      {suffix !== undefined && <span className="text-ink-3">{suffix}</span>}
    </Fig>
  )
}

/** Money in the exact-tail contexts: always pence, never switching unit. */
export function PenceExact({
  value,
  signed = false,
  size = 'base',
  weight,
  tone = 'plain',
}: {
  value: string | number
  signed?: boolean
  size?: FigSize
  weight?: FigWeight
  tone?: Tone
}) {
  const { head, tail } = signed ? penceSigned(value) : pence(value)
  return <Exact head={head} tail={tail} suffix="p" size={size} weight={weight} tone={tone} />
}

/**
 * Invariant 8. A missing cost renders as "no price" in the kit's own missing
 * treatment — never as 0, and never silently.
 */
export function CostFig({
  cost,
  suffix = '',
  size = 'base',
  weight,
  showSource = true,
}: {
  cost: Cost | null | undefined
  suffix?: string
  size?: FigSize
  weight?: FigWeight
  showSource?: boolean
}) {
  const v = costView(cost)
  if (v.kind === 'missing') return <KitFig missing="no price" />
  return (
    <span className="whitespace-nowrap">
      <span className={v.isEstimate ? EST : undefined}>
        <Exact head={v.head} tail={v.tail} suffix={`p${suffix}`} size={size} weight={weight} />
      </span>
      {showSource && v.isEstimate && <Label className="ml-1">estimate</Label>}
    </span>
  )
}

/** A quantity exactly as sent, trailing zeros trimmed, unit always attached. */
export function Qty({
  value,
  unit,
  size = 'base',
  weight,
  tone = 'plain',
  className = '',
}: {
  value: string
  unit?: Unit | string | null
  size?: FigSize
  weight?: FigWeight
  tone?: Tone
  className?: string
}) {
  return (
    <span className={`whitespace-nowrap ${className}`}>
      <Fig size={size} weight={weight} tone={tone}>
        {trimQty(value)}
      </Fig>
      {unit ? <span className="text-ink-3 text-[0.75rem]"> {unit}</span> : null}
    </span>
  )
}

/** A small caption beside or under a figure. Sentence case, never tracked-out. */
/**
 * The margin-annotated row: a small label in the left gutter and the content
 * beside it. Below `sm` the gutter collapses and the label sits above its body,
 * because a 7rem gutter on a 375px phone leaves nothing for the figures.
 */
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
    <div
      className={`grid grid-cols-1 gap-x-5 sm:grid-cols-[6.5rem_minmax(0,1fr)] ${className}`}
      style={style}
    >
      {children}
    </div>
  )
}

/** A gutter annotation. `flag` is the only colour it ever takes. */
export function Marg({ children, flag = false }: { children: ReactNode; flag?: boolean }) {
  return (
    <div
      className={`pt-1 pb-1 font-mono text-[0.6875rem] leading-[16px] sm:col-start-1 sm:pb-0 sm:text-right ${
        flag ? 'text-bad' : 'text-ink-3'
      }`}
    >
      {children}
    </div>
  )
}

/**
 * The body column. `min-w-0` is load-bearing: without it a wide figure row
 * blows the grid track out and the whole page scrolls sideways instead of the
 * one table that is too wide.
 */
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
    <div className={`min-w-0 sm:col-start-2 ${className}`} style={style}>
      {children}
    </div>
  )
}

/** A label. Mono, lower case, muted -- never tracked-out caps. */
export function Label({
  children,
  tone = 'muted',
  className = '',
}: {
  children: ReactNode
  tone?: Tone
  className?: string
}) {
  const c: Record<Tone, string> = {
    plain: 'text-ink-2',
    ok: 'text-ok',
    warn: 'text-warn',
    bad: 'text-bad',
    info: 'text-info',
    muted: 'text-ink-3',
  }
  return <span className={`text-[0.6875rem] ${c[tone]} ${className}`}>{children}</span>
}

/**
 * A column heading inside a responsive row grid, where `<Th>` cannot be used
 * because the grid must collapse to a stacked list on a narrow viewport and a
 * real `<table>` cannot do that.
 *
 * Sentence case, not tracked-out capitals: DESIGN-LAW.md bans those, and at
 * 11px the tracking costs more legibility than the emphasis buys.
 */
export function ColLabel({
  children,
  className = '',
}: {
  children: ReactNode
  className?: string
}) {
  return (
    <span className={`text-[0.6875rem] font-medium text-ink-4 ${className}`}>{children}</span>
  )
}
