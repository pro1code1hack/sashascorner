/**
 * The Insights charts: one-series bar charts (weekly counts, stamps by hour) and a
 * horizontal share list (where members joined). Hand-rolled divs like the kit's
 * `Bars` (CLAUDE.md §3, no chart library), plus what the kit's version lacks: a
 * per-bar hover/focus readout in the card's corner, and a hidden table for screen
 * readers. One series each, so no legend: the card's title names it.
 */
import { useState, type ReactNode } from 'react'
import { cx } from '../../../components/ui'

export interface ColumnDatum {
  key: string
  value: number
  /** Readout when this bar is hovered or focused: "Week of 29 Jun: 3". */
  readout: string
  /** Axis label under the bar; most are blank so labels never collide. */
  axis?: string
  /** Drawn in the strong tone (the peak hour). */
  strong?: boolean
}

type Tone = 'brand' | 'ok' | 'seq'

const FILL: Record<Tone, string> = {
  brand: 'bg-brand',
  ok: 'bg-ok',
  // Sequential single hue: light for ordinary, full brand for the one that matters.
  seq: 'bg-seq-2',
}

/** A white chart card: title, a figure (or the hovered bar's readout) in the corner. */
export function ChartCard({
  title,
  sub,
  corner,
  children,
  className,
}: {
  title: string
  sub?: ReactNode
  corner?: ReactNode
  children: ReactNode
  className?: string
}) {
  return (
    <section className={cx('flex min-w-0 flex-col rounded-card border border-line bg-surface px-4 pb-3 pt-3.5', className)}>
      <div className="flex items-baseline justify-between gap-3">
        <h3 className="text-md font-extrabold">{title}</h3>
        {corner !== undefined && <span className="fig truncate text-xs text-ink-2">{corner}</span>}
      </div>
      {sub && <p className="text-xs text-ink-2">{sub}</p>}
      <div className="mt-3 min-w-0 flex-1">{children}</div>
    </section>
  )
}

/**
 * Vertical bars from a baseline. `onHover` reports the hovered bar's readout so the
 * card can show it in its corner; zero is a hairline stub, not an empty gap.
 */
export function Columns({
  data,
  tone,
  height = 96,
  label,
  onHover,
  firstLabel,
  lastLabel,
}: {
  data: readonly ColumnDatum[]
  tone: Tone
  height?: number
  label: string
  onHover?: (readout: string | null) => void
  /** Labels pinned under the first and last bar ("29 Jun" … "this week"). */
  firstLabel?: string
  lastLabel?: string
}) {
  const max = Math.max(1, ...data.map((d) => d.value))
  const axis = data.some((d) => d.axis !== undefined)
  return (
    <figure aria-label={label} className="min-w-0">
      <div className="flex items-end gap-[3px] border-b border-line" style={{ height }} onMouseLeave={() => onHover?.(null)}>
        {data.map((d) => {
          const h = d.value === 0 ? 1 : Math.max(3, (d.value / max) * (height - 4))
          return (
            <div
              key={d.key}
              tabIndex={0}
              aria-label={d.readout}
              onMouseEnter={() => onHover?.(d.readout)}
              onFocus={() => onHover?.(d.readout)}
              onBlur={() => onHover?.(null)}
              className="group flex h-full min-w-0 flex-1 cursor-default items-end justify-center outline-none"
            >
              <div
                style={{ height: h }}
                className={cx(
                  'w-full max-w-7 rounded-t-[4px] transition-opacity group-hover:opacity-80 group-focus-visible:ring-2 group-focus-visible:ring-brand',
                  d.value === 0 ? 'bg-line-strong' : d.strong ? 'bg-brand' : FILL[tone],
                )}
              />
            </div>
          )
        })}
      </div>
      {axis ? (
        <div className="mt-1 flex gap-[3px]" aria-hidden="true">
          {data.map((d) => (
            <div key={d.key} className="fig min-w-0 flex-1 text-center text-label text-ink-2">
              {d.axis ?? ''}
            </div>
          ))}
        </div>
      ) : (
        (firstLabel || lastLabel) && (
          <div className="mt-1 flex justify-between gap-2 text-label text-ink-2" aria-hidden="true">
            <span>{firstLabel}</span>
            <span>{lastLabel}</span>
          </div>
        )
      )}
      <table className="sr-only">
        <caption>{label}</caption>
        <tbody>
          {data.map((d) => (
            <tr key={d.key}>
              <td>{d.readout}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </figure>
  )
}

/** Label · bar · count · share, one row each (where members joined). */
export function ShareRows({
  rows,
  label,
}: {
  rows: readonly { key: string; label: string; count: number; share: string; fraction: number }[]
  label: string
}) {
  const max = Math.max(0.0001, ...rows.map((r) => r.fraction))
  return (
    <table className="w-full border-collapse text-base" aria-label={label}>
      <tbody>
        {rows.map((r) => (
          <tr key={r.key}>
            <th scope="row" className="max-w-[9rem] truncate py-1 pr-3 text-left font-normal text-ink">
              {r.label}
            </th>
            <td className="w-full py-1 pr-3" aria-hidden="true">
              <div className="h-2 rounded-full bg-wash">
                <div className="h-2 rounded-full bg-brand" style={{ width: `${Math.max(2, (r.fraction / max) * 100)}%` }} />
              </div>
            </td>
            <td className="fig py-1 pr-3 text-right font-bold">{r.count}</td>
            <td className="fig w-10 py-1 text-right text-sm text-ink-2">{r.share}</td>
          </tr>
        ))}
      </tbody>
    </table>
  )
}

/** Hover state for a chart card's corner readout. */
export function useReadout(): [string | null, (r: string | null) => void] {
  const [r, set] = useState<string | null>(null)
  return [r, set]
}
