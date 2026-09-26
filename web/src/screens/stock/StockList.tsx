/**
 * The stock listing (§1.1): the design's 7-column grid, one row per
 * ingredient. The estimate column is always italic; the count column is
 * upright and always dated; a row with no count shows "—" (invariant 6, C9).
 *
 * A grid rather than the kit Table because the design's widths and 12px gap
 * are the spec, and the kit's cell padding cannot reproduce them. Below 640px
 * the grid scrolls sideways inside itself; the page never does.
 */
import { Pill, TierBadge, TrustPill, cx } from '../../components/ui'
import type { StockRow } from '../../lib/types/stock'
import { driftShown } from './fmt'
import { lastCountCell, leftCell, runsOutCell, trustOf, useByCell } from './model'

export const GRID =
  'grid grid-cols-[minmax(0,2.2fr)_84px_minmax(0,1.3fr)_64px_118px_minmax(0,1.2fr)_70px] gap-3'

export function StockTrust({ row }: { row: StockRow }) {
  if (row.tier === 'C') {
    return row.checklist?.status === 'LOW' ? <TrustPill trust="low" /> : <TrustPill trust="checklist" />
  }
  const t = trustOf(row)
  if (t === 'not_yet_judged') {
    return (
      <span title="Counted once: drift needs a second count to compare against.">
        <Pill tone="muted">Not yet judged</Pill>
      </span>
    )
  }
  return <TrustPill trust={t} />
}

export function StockList({
  rows,
  selected,
  onSelect,
}: {
  rows: StockRow[]
  selected: number | null
  onSelect: (id: number) => void
}) {
  return (
    <div className="scroll-x flex min-h-0 flex-1 flex-col overflow-y-auto">
      <div className="flex min-w-[760px] flex-col compact:min-w-0">
        <div
          className={cx(
            GRID,
            'sticky top-0 z-[1] whitespace-nowrap border-b [&>span]:truncate border-line-soft bg-surface px-5 py-2.5 text-label font-bold uppercase tracking-[.06em] text-ink-3',
          )}
          aria-hidden="true"
        >
          <span>Ingredient</span>
          <span className="text-right">Left (est.)</span>
          <span>Last count</span>
          <span className="text-right">Drift</span>
          <span>Trust</span>
          <span>Runs out</span>
          <span>Use by</span>
        </div>
        <ul aria-label="Ingredients">
          {rows.map((row) => (
            <li key={row.ingredient_id}>
              <Row row={row} selected={selected === row.ingredient_id} onSelect={onSelect} />
            </li>
          ))}
        </ul>
      </div>
    </div>
  )
}

function Row({ row, selected, onSelect }: { row: StockRow; selected: boolean; onSelect: (id: number) => void }) {
  const drift = row.tier === 'C' ? null : row.drift.drift_pct
  const abs = drift === null ? 0 : Math.abs(drift)
  const ro = runsOutCell(row)
  const useBy = useByCell(row)
  return (
    <button
      type="button"
      onClick={() => onSelect(row.ingredient_id)}
      aria-pressed={selected}
      aria-label={`${row.name}: open details`}
      className={cx(
        GRID,
        'min-h-[52px] w-full items-center border-b border-line-row px-5 py-1.5 text-left text-base transition-[background-color]',
        selected ? 'bg-brand-wash' : 'hover:bg-canvas-2',
        'focus-visible:outline-offset-[-2px]',
      )}
    >
      <span className="flex min-w-0 items-center gap-2">
        <TierBadge tier={row.tier} />
        <span className="truncate font-semibold">{row.name}</span>
      </span>
      <span className="fig truncate text-right italic">{leftCell(row)}</span>
      <span className="truncate text-sm text-ink-2">{lastCountCell(row)}</span>
      <span
        className={cx(
          'fig text-right',
          abs > 15 ? 'text-alert' : 'text-ink',
          abs >= 10 ? 'font-bold' : 'font-normal',
        )}
      >
        {driftShown(drift)}
      </span>
      <span>
        <StockTrust row={row} />
      </span>
      <span
        title={ro.title}
        className={cx('truncate text-sm', ro.alert ? 'text-bad-ink' : ro.reason ? 'text-ink-2' : 'text-ink')}
      >
        {ro.text}
      </span>
      <span className={cx('fig whitespace-nowrap text-sm', useBy.alert ? 'text-bad-ink' : 'text-ink')}>
        {useBy.text}
      </span>
    </button>
  )
}
