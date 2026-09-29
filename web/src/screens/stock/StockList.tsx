/**
 * The stock listing (§1.1): the design's 7-column grid, one row per
 * ingredient. The estimate column is always italic; the count column is
 * upright and always dated; a row with no count shows "—" (invariant 6, C9).
 *
 * Laid out like the Menu items List view: a rounded card, uppercase header,
 * badge + name/subtitle, right-aligned figures. Below the compact breakpoint
 * the figures fold into a grey line under the name; the page never scrolls
 * sideways.
 */
import { Pill, TierBadge, TrustPill, cx } from '../../components/ui'
import type { StockRow } from '../../lib/types/stock'
import { IngredientThumb } from '../ingredients/Thumb'
import type { ReactNode } from 'react'
import { driftShown, packEquiv } from './fmt'
import { lastCountCell, leftCell, runsOutCell, trustOf, useByCell } from './model'

/** Same list anatomy as Menu items' List view (owner, 2026-09-26): badge slot,
 * bold name over a grey subtitle, right-aligned figure columns. */
export const GRID =
  'grid grid-cols-[44px_minmax(0,1fr)_auto] items-center gap-x-3 compact:grid-cols-[44px_minmax(0,1fr)_104px_112px_68px_122px_minmax(0,150px)_64px]'

const STORAGE_WORD: Record<string, string> = { AMBIENT: 'ambient', CHILLED: 'chilled', FROZEN: 'frozen' }

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
  groupBy,
}: {
  rows: StockRow[]
  selected: number | null
  onSelect: (id: number) => void
  /** Insert a heading whenever this key changes (rows arrive sorted by it). */
  groupBy?: (row: StockRow) => string
}) {
  const items: ReactNode[] = []
  let last: string | null = null
  for (const row of rows) {
    if (groupBy) {
      const g = groupBy(row)
      if (g !== last) {
        const n = rows.filter((r) => groupBy(r) === g).length
        items.push(
          <li
            key={`g-${g}`}
            className="flex items-baseline gap-2 border-b border-line bg-canvas-2 px-3.5 py-1.5 text-label font-bold uppercase tracking-[.06em] text-ink-2"
          >
            {g}
            <span className="fig font-normal normal-case tracking-normal text-ink-3">{n}</span>
          </li>,
        )
        last = g
      }
    }
    items.push(
      <li key={row.ingredient_id} className="border-b border-line-row last:border-b-0">
        <Row row={row} selected={selected === row.ingredient_id} onSelect={onSelect} />
      </li>,
    )
  }
  return (
    <div className="min-h-0 flex-1 overflow-y-auto bg-canvas px-4 pb-6 pt-3.5 sm:px-5">
      <div className="overflow-hidden rounded-card-lg bg-surface shadow-raised">
        <div
          className={cx(
            GRID,
            'hidden whitespace-nowrap border-b border-line px-3.5 py-2 text-label font-bold uppercase tracking-[.06em] text-ink-3 compact:grid [&>span]:truncate',
          )}
          aria-hidden="true"
        >
          <span />
          <span>Ingredient</span>
          <span className="text-right">Left (est.)</span>
          <span>Last count</span>
          <span className="text-right">Drift</span>
          <span>Trust</span>
          <span>Runs out</span>
          <span className="text-right">Use by</span>
        </div>
        <ul aria-label="Ingredients">{items}</ul>
        <p className="border-t border-line px-3.5 py-2 text-xs text-ink-2">
          Left is worked out from sales since the last count, so it is an estimate (<em>italic</em>); a count is upright
          and dated.
        </p>
      </div>
    </div>
  )
}

function Row({ row, selected, onSelect }: { row: StockRow; selected: boolean; onSelect: (id: number) => void }) {
  const drift = row.tier === 'C' ? null : row.drift.drift_pct
  const abs = drift === null ? 0 : Math.abs(drift)
  const ro = runsOutCell(row)
  const useBy = useByCell(row)
  // Pack-equivalent under the estimate, italic like it (still theoretical).
  const left =
    row.tier !== 'C' && row.on_hand.has_count_basis ? packEquiv(row.on_hand.qty, row.pack, row.category) : null
  const sub = [row.category ?? 'Uncategorised', row.pack?.supplier_name, STORAGE_WORD[row.shelf_life.storage]]
    .filter(Boolean)
    .join(' · ')
  return (
    <button
      type="button"
      onClick={() => onSelect(row.ingredient_id)}
      aria-pressed={selected}
      aria-label={`${row.name}: open details`}
      className={cx(
        GRID,
        'w-full px-3.5 py-2.5 text-left text-ink transition-[background-color]',
        selected ? 'bg-brand-wash' : 'hover:bg-canvas-2',
        'focus-visible:outline-offset-[-2px]',
      )}
    >
      <IngredientThumb url={row.photo_url} name={row.name} fallback={<TierBadge tier={row.tier} />} corner={<TierBadge tier={row.tier} />} />
      <span className="min-w-0">
        <span className="block truncate text-md font-bold">{row.name}</span>
        <span className="block truncate text-sm text-ink-2">{sub}</span>
        {/* Phone: the rest of the row folds into a second grey line. */}
        <span className="block truncate text-sm text-ink-2 compact:hidden">
          <span className="fig">{lastCountCell(row)}</span>
          {drift !== null && <> · drift {driftShown(drift)}</>}
          {ro.text ? ` · runs out ${ro.text}` : ''}
        </span>
      </span>
      <span className="flex min-w-0 flex-col items-end leading-tight">
        <span className="fig max-w-full truncate text-base italic">{leftCell(row)}</span>
        {left !== null && (
          <span className="fig max-w-full truncate text-xs italic text-ink-2" title={left.title}>
            {left.text}
          </span>
        )}
      </span>
      <span className="hidden truncate text-sm text-ink-2 compact:block">{lastCountCell(row)}</span>
      <span
        className={cx(
          'fig hidden text-right text-base compact:block',
          abs > 15 ? 'text-alert' : 'text-ink',
          abs >= 10 ? 'font-bold' : 'font-normal',
        )}
      >
        {driftShown(drift)}
      </span>
      <span className="hidden compact:block">
        <StockTrust row={row} />
      </span>
      <span
        title={ro.title}
        className={cx('hidden truncate text-sm compact:block', ro.alert ? 'text-bad-ink' : ro.reason ? 'text-ink-2' : 'text-ink')}
      >
        {ro.text}
      </span>
      <span className={cx('fig hidden whitespace-nowrap text-right text-sm compact:block', useBy.alert ? 'text-bad-ink' : 'text-ink')}>
        {useBy.text}
      </span>
    </button>
  )
}
