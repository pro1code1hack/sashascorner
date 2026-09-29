/**
 * Where one size's ingredient cost goes: a small hand-rolled SVG donut and a
 * legend with the figures. The legend carries the numbers; the ring only
 * shows proportion. Largest five lines get their own slice, the rest is
 * "Everything else".
 *
 * Invariant 8: a line with no price has no slice (a zero-width slice would
 * read as "costs nothing"). It is listed under the chart instead, and the
 * total says it is incomplete. Estimated costs are italic, as everywhere.
 */
import { cx } from '../../components/ui'
import { fromMoney, sum } from '../../lib/dec'
import { gbp } from '../../lib/format'
import { decStr } from './common/figures'
import type { MenuLine } from '../../lib/types/menu'

/**
 * Design tokens only (styles.css). Every slice is >= 3:1 on white (WCAG 1.4.11;
 * brand-line was 1.35:1, seq-2 is 2.1:1 so it is not used), and neighbours
 * alternate dark/light so adjacent slices stay apart. The legend carries the
 * name and share, so colour is never the only encoding.
 */
const COLOURS = [
  'var(--color-seq-5)',
  'var(--color-seq-3)',
  'var(--color-brand-deep)',
  'var(--color-seq-4)',
  'var(--color-warn-ink)',
  'var(--color-ink-3)',
]
const R = 42
const STROKE = 16
const C = 2 * Math.PI * R

interface Slice {
  key: string
  name: string
  /** Exact pence for the figures shown. */
  exact: string
  /** Float copy for geometry only (never shown, never summed into money). */
  pence: number
  est: boolean
}

export function CostDonut({ lines, className }: { lines: MenuLine[]; className?: string }) {
  const priced = lines
    .filter((l) => l.line_cost.pence !== null && !l.line_cost.is_missing)
    .map<Slice>((l) => ({
      key: String(l.ingredient_id),
      name: l.ingredient_name,
      exact: l.line_cost.pence as string,
      pence: Number(l.line_cost.pence),
      est: l.line_cost.is_estimate,
    }))
    .filter((s) => s.pence > 0)
    .sort((a, b) => b.pence - a.pence)
  const missing = lines.filter((l) => l.line_cost.pence === null || l.line_cost.is_missing)
  // Six or fewer lines each get a slice; beyond that the five largest do.
  const top = priced.length <= 6 ? priced : priced.slice(0, 5)
  const rest = priced.length <= 6 ? [] : priced.slice(5)
  const slices: Slice[] = rest.length
    ? [
        ...top,
        {
          key: 'rest',
          name: `Everything else (${rest.length})`,
          exact: decStr(sum(rest.map((s) => fromMoney(s.exact)))),
          pence: rest.reduce((a, s) => a + s.pence, 0),
          est: rest.some((s) => s.est),
        },
      ]
    : top
  const total = slices.reduce((a, s) => a + s.pence, 0)
  const totalEst = slices.some((s) => s.est)
  const totalExact = decStr(sum(slices.map((s) => fromMoney(s.exact))))

  if (lines.length === 0) return null
  if (total <= 0) {
    return (
      <p className={cx('text-sm text-ink-2', className)}>
        No slice to draw: none of these ingredients has a price yet, so what this costs is unknown.
      </p>
    )
  }

  let offset = 0
  return (
    <figure className={cx('flex flex-wrap items-center gap-4', className)}>
      <svg
        viewBox="0 0 100 100"
        className="size-[132px] flex-none -rotate-90"
        role="img"
        aria-label={`Ingredient cost split: ${slices.map((s) => `${s.name} ${Math.round((s.pence / total) * 100)}%`).join(', ')}`}
      >
        <circle cx="50" cy="50" r={R} fill="none" stroke="var(--color-line-soft)" strokeWidth={STROKE} />
        {slices.map((s, i) => {
          const len = (s.pence / total) * C
          // A hairline gap between slices so neighbours stay distinguishable.
          const gap = slices.length > 1 ? Math.min(0.8, len / 3) : 0
          const el = (
            <circle
              key={s.key}
              cx="50"
              cy="50"
              r={R}
              fill="none"
              stroke={COLOURS[i % COLOURS.length]}
              strokeWidth={STROKE}
              strokeDasharray={`${Math.max(0, len - gap)} ${C}`}
              strokeDashoffset={-offset}
            />
          )
          offset += len
          return el
        })}
        <text
          x="50"
          y="50"
          textAnchor="middle"
          dominantBaseline="central"
          transform="rotate(90 50 50)"
          className={cx('fig fill-ink text-md font-extrabold', totalEst && 'italic')}
        >
          {gbp(totalExact)}
        </text>
      </svg>
      <figcaption className="min-w-[180px] flex-1">
        <ul className="flex flex-col gap-1">
          {slices.map((s, i) => (
            <li key={s.key} className="flex items-center gap-2 text-sm">
              <span aria-hidden="true" className="size-2.5 flex-none rounded-sm" style={{ background: COLOURS[i % COLOURS.length] }} />
              <span className="min-w-0 flex-1 truncate">{s.name}</span>
              <span className={cx('fig text-ink-2', s.est && 'italic')}>{gbp(s.exact)}</span>
              <span className="fig w-9 text-right font-bold">{Math.round((s.pence / total) * 100)}%</span>
            </li>
          ))}
        </ul>
        {missing.length > 0 && (
          <p className="mt-1.5 text-xs text-bad-ink">
            Not in the chart, no price yet: {missing.map((m) => m.ingredient_name).join(', ')}. The real total is higher than
            shown.
          </p>
        )}
      </figcaption>
    </figure>
  )
}
