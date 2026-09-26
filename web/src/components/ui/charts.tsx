/**
 * Meter, Bars, DivergingBars: design-system.md §5.7. Hand-rolled divs, no
 * library (CLAUDE.md §3). Brand bars; `alert` only for a bar over its
 * threshold; a missing value is a dashed outline with no fill, never a zero.
 *
 * Values are plain numbers used ONLY for geometry (integer pence, counts,
 * percentages). Labels are pre-formatted strings from lib/format, so no float
 * ever reaches a figure the reader sees.
 */
import type { ReactNode } from 'react'
import { cx } from './cx'

/** Split bar: e.g. drift attribution (measurement vs expiry). */
export function Meter({
  segments,
  label,
  className,
}: {
  /** Shares in any unit; drawn proportionally. `tone` 'brand' (default) or 'muted'. */
  segments: ReadonlyArray<{ key: string; value: number; tone?: 'brand' | 'muted' | 'alert'; label?: string }>
  /** Accessible description of the whole meter. */
  label: string
  className?: string
}) {
  const total = segments.reduce((a, s) => a + Math.max(0, s.value), 0)
  return (
    <div role="img" aria-label={label} className={cx('flex h-3 w-full border border-line-strong', className)}>
      {total > 0 &&
        segments.map((s) => (
          <div
            key={s.key}
            title={s.label}
            style={{ width: `${(Math.max(0, s.value) / total) * 100}%` }}
            className={cx(
              'h-full',
              (s.tone ?? 'brand') === 'brand' && 'bg-brand',
              s.tone === 'muted' && 'bg-line-strong',
              s.tone === 'alert' && 'bg-alert',
            )}
          />
        ))}
    </div>
  )
}

export interface Bar {
  key: string
  /** Axis label under the bar ("Mon"). */
  label: ReactNode
  /** null = missing (dashed outline), never drawn as zero. */
  value: number | null
  /** Pre-formatted value label above the bar. */
  valueLabel?: ReactNode
  /** Over the threshold: alert fill. */
  alert?: boolean
}

/** Vertical bars from a baseline (shop runs, takings by day). */
export function Bars({
  bars,
  height = 120,
  label,
  className,
}: {
  bars: readonly Bar[]
  height?: number
  label: string
  className?: string
}) {
  const max = Math.max(1, ...bars.map((b) => (b.value === null ? 0 : Math.abs(b.value))))
  return (
    <figure aria-label={label} className={cx('min-w-0', className)}>
      <div className="flex items-end gap-1 border-b border-line" style={{ height }}>
        {bars.map((b) => {
          const h = b.value === null ? height * 0.35 : (Math.abs(b.value) / max) * (height - 18)
          return (
            <div key={b.key} className="flex min-w-0 flex-1 flex-col items-center justify-end gap-0.5">
              {b.valueLabel !== undefined && (
                <span className="fig max-w-full truncate text-xs text-ink-2">{b.valueLabel}</span>
              )}
              <div
                style={{ height: Math.max(b.value === null ? h : 1, h) }}
                className={cx(
                  'w-full max-w-8',
                  b.value === null
                    ? 'border-[1.5px] border-b-0 border-dashed border-line-strong'
                    : b.alert
                      ? 'bg-alert'
                      : 'bg-brand',
                )}
              />
            </div>
          )
        })}
      </div>
      <div className="mt-1 flex gap-1">
        {bars.map((b) => (
          <div key={b.key} className="min-w-0 flex-1 truncate text-center text-xs text-ink-2">
            {b.label}
          </div>
        ))}
      </div>
    </figure>
  )
}

/** Bars above and below a zero line (cash over/under). */
export function DivergingBars({
  bars,
  height = 120,
  label,
  className,
}: {
  bars: readonly Bar[]
  height?: number
  label: string
  className?: string
}) {
  const max = Math.max(1, ...bars.map((b) => (b.value === null ? 0 : Math.abs(b.value))))
  const half = height / 2
  return (
    <figure aria-label={label} className={cx('min-w-0', className)}>
      <div className="relative flex gap-1" style={{ height }}>
        <div className="absolute inset-x-0 border-t border-line" style={{ top: half }} aria-hidden="true" />
        {bars.map((b) => {
          const v = b.value
          const h = v === null ? 0 : (Math.abs(v) / max) * (half - 4)
          return (
            <div key={b.key} className="relative min-w-0 flex-1" title={typeof b.valueLabel === 'string' ? b.valueLabel : undefined}>
              {v === null ? (
                <div
                  className="absolute inset-x-1/4 border-[1.5px] border-dashed border-line-strong"
                  style={{ top: half - 12, height: 24 }}
                />
              ) : (
                <div
                  className={cx('absolute inset-x-0 mx-auto max-w-8', b.alert ? 'bg-alert' : 'bg-brand')}
                  style={v >= 0 ? { bottom: half, height: Math.max(1, h) } : { top: half, height: Math.max(1, h) }}
                />
              )}
            </div>
          )
        })}
      </div>
      <div className="mt-1 flex gap-1">
        {bars.map((b) => (
          <div key={b.key} className="min-w-0 flex-1 truncate text-center text-xs text-ink-2">
            {b.label}
          </div>
        ))}
      </div>
    </figure>
  )
}
