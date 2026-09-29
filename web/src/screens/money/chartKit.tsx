/**
 * The pieces every Money chart shares: a clean £ axis, a width observer, the
 * legend (keys can toggle a series), the hover tooltip and the rounded bar end.
 * Marks follow the dataviz spec: bars <= 44px with a 4px rounded data end,
 * square at the baseline; hairline grid; a legend for two or more series.
 */
import { useEffect, useRef, useState } from 'react'
import type { ReactNode } from 'react'
import { cx } from '../../components/ui'

/** £ axis labels: whole pounds, "£1.2k" past a thousand. */
export function axisMoney(pence: number): string {
  const pounds = pence / 100
  if (pounds >= 1000) return `£${(pounds / 1000).toFixed(pounds >= 10000 ? 0 : 1)}k`
  return `£${Math.round(pounds)}`
}

export function niceMax(v: number): { max: number; step: number } {
  if (v <= 0) return { max: 1000, step: 250 }
  const raw = v / 4
  const mag = 10 ** Math.floor(Math.log10(raw))
  const step = [1, 2, 2.5, 5, 10].map((m) => m * mag).find((s) => s >= raw) ?? raw
  return { max: Math.ceil(v / step) * step, step }
}

export function useWidth<T extends HTMLElement>(): [React.RefObject<T>, number] {
  const ref = useRef<T>(null)
  const [w, setW] = useState(0)
  useEffect(() => {
    const el = ref.current
    if (!el) return
    const ro = new ResizeObserver(([e]) => e && setW(Math.floor(e.contentRect.width)))
    ro.observe(el)
    return () => ro.disconnect()
  }, [])
  return [ref, w]
}

/* --------------------------------------------------------------- legend --- */

export function Legend({
  items,
  onToggle,
}: {
  items: { key: string; label: string; color: string; value: ReactNode; off?: boolean; shape?: 'bar' | 'line' }[]
  onToggle?: (key: string) => void
}) {
  return (
    <ul className="mb-3 flex flex-wrap gap-x-4 gap-y-1" aria-label="Legend">
      {items.map((it) => {
        const body = (
          <>
            {it.shape === 'line' ? (
              <span aria-hidden="true" className="flex h-3 w-4 flex-none items-center">
                <span className="h-[3px] w-full rounded-full" style={{ background: it.off ? 'transparent' : it.color, boxShadow: `inset 0 0 0 1.5px ${it.color}` }} />
              </span>
            ) : (
              <span aria-hidden="true" className="size-3 flex-none rounded-[3px]" style={{ background: it.off ? 'transparent' : it.color, boxShadow: `inset 0 0 0 2px ${it.color}` }} />
            )}
            <span className={cx('text-sm', it.off ? 'text-ink-3 line-through' : 'text-ink-2')}>{it.label}</span>
            <span className={cx('fig text-sm font-bold', it.off ? 'text-ink-3' : 'text-ink')}>{it.value}</span>
          </>
        )
        return (
          <li key={it.key}>
            {onToggle ? (
              <button type="button" aria-pressed={!it.off} title={it.off ? 'Show' : 'Hide'} onClick={() => onToggle(it.key)} className="flex min-h-8 items-center gap-1.5 rounded-control px-1.5 hover:bg-canvas-2">
                {body}
              </button>
            ) : (
              <span className="flex min-h-8 items-center gap-1.5 px-1.5">{body}</span>
            )}
          </li>
        )
      })}
    </ul>
  )
}

export function Tip({ x, width, children }: { x: number; width: number; children: ReactNode }) {
  const w = 200
  const left = Math.min(Math.max(0, x - w / 2), Math.max(0, width - w))
  return (
    <div role="tooltip" className="pointer-events-none absolute top-0 z-10 rounded-card border border-line bg-surface px-3 py-2 text-sm shadow-login" style={{ left, width: w }}>
      {children}
    </div>
  )
}

/** A column with a rounded top (r) and a square base: x, top y, width, height. */
export function barPath(x: number, y: number, w: number, h: number, r = 4): string {
  const rr = Math.max(0, Math.min(r, w / 2, h))
  return `M${x},${y + h} V${y + rr} Q${x},${y} ${x + rr},${y} H${x + w - rr} Q${x + w},${y} ${x + w},${y + rr} V${y + h} Z`
}
