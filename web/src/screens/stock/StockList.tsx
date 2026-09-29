/**
 * The stock listing (§1.1): one row per ingredient, on the kit `<Table>` so
 * every figure has a column name a screen reader can hear. The estimate
 * column is always italic; the count column is upright and always dated; a
 * row with no count shows "—" (invariant 6, C9).
 *
 * Laid out like the Menu items List view: a rounded card, uppercase header,
 * photo/badge + name/subtitle, right-aligned figures. Columns come in by width
 * and whatever is not a column folds into a grey line under the name, so no
 * width loses a fact:
 *
 *   below 900px   tier · name · left; the line carries last count, drift,
 *                 trust, runs out, use by
 *   900-1279px    + last count, drift, trust; the line carries runs out, use by
 *   1280px up     every column
 *
 * The name column takes what the fixed columns leave (`w-full max-w-0`, so
 * long names truncate instead of pushing the table wider) and the table keeps
 * a minimum width that leaves it at least ~170px; below that it scrolls inside
 * the card. It used to collapse to 0px at 903px.
 *
 * Rows carry no aria-label: it would replace the row's content, and the
 * figures would never be read. The header row is a real `<thead>`, so each
 * cell is announced with its column name.
 */
import { Pill, TBody, THead, Table, Td, Th, Tr, TierBadge, TrustPill } from '../../components/ui'
import type { StockRow } from '../../lib/types/stock'
import { IngredientThumb } from '../ingredients/Thumb'
import type { ReactNode } from 'react'
import { driftShown, packEquiv } from './fmt'
import { TRUST_WORD, lastCountCell, leftCell, runsOutCell, trustOf, useByCell } from './model'

const STORAGE_WORD: Record<string, string> = { AMBIENT: 'ambient', CHILLED: 'chilled', FROZEN: 'frozen' }

/** Figure columns: hidden on a phone, where the sub-line carries them. */
const COL = 'hidden compact:table-cell'
/** Runs out and use by: only from `wide`; below it they ride in the sub-line. */
const WIDE = 'hidden wide:table-cell'
const COLUMNS = 8

/** The trust word as plain text, for the phone sub-line. */
export function trustWord(row: StockRow): string {
  if (row.tier === 'C') return row.checklist?.status === 'LOW' ? 'Low' : 'Checklist'
  return TRUST_WORD[trustOf(row)]
}

export function StockTrust({ row }: { row: StockRow }) {
  if (row.tier === 'C') {
    return row.checklist?.status === 'LOW' ? <TrustPill trust="low" /> : <TrustPill trust="checklist" />
  }
  const t = trustOf(row)
  if (t === 'not_yet_judged') {
    return (
      <Pill tone="muted">
        Not yet judged
        <span className="sr-only">: counted once, drift needs a second count to compare against</span>
      </Pill>
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
  const body: ReactNode[] = []
  let last: string | null = null
  for (const row of rows) {
    if (groupBy) {
      const g = groupBy(row)
      if (g !== last) {
        const n = rows.filter((r) => groupBy(r) === g).length
        body.push(
          <tr key={`g-${g}`} className="border-b border-line bg-canvas-2">
            <th
              scope="colgroup"
              colSpan={COLUMNS}
              className="px-2 py-1.5 text-left text-label font-bold uppercase tracking-[.06em] text-ink-2"
            >
              {g} <span className="fig font-normal normal-case tracking-normal text-ink-2">{n}</span>
            </th>
          </tr>,
        )
        last = g
      }
    }
    body.push(<Row key={row.ingredient_id} row={row} selected={selected === row.ingredient_id} onSelect={onSelect} />)
  }
  return (
    <div className="min-h-0 flex-1 overflow-y-auto bg-canvas px-4 pb-6 pt-3.5 sm:px-5">
      <div className="overflow-hidden rounded-card-lg bg-surface shadow-raised">
        <div className="px-2 sm:px-3.5">
          <Table label="Ingredients on the shelf" className="compact:min-w-[630px] wide:min-w-[860px]">
            <THead>
              <tr>
                <Th width={44}>
                  <span className="sr-only">Tier</span>
                </Th>
                <Th>Ingredient</Th>
                <Th numeric width={104}>
                  Left (est.)
                </Th>
                <Th width={108} className={COL}>
                  Last count
                </Th>
                <Th numeric width={64} className={COL}>
                  Drift
                </Th>
                <Th width={116} className={COL}>
                  Trust
                </Th>
                <Th width={150} className={WIDE}>
                  Runs out
                </Th>
                <Th numeric width={72} className={WIDE}>
                  Use by
                </Th>
              </tr>
            </THead>
            <TBody>{body}</TBody>
          </Table>
        </div>
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
  // Phone: the figure columns fold into one grey line. Trust and use-by included,
  // so "Excluded" and a short date reach a phone too.
  const phoneLine = [
    lastCountCell(row),
    drift !== null ? `drift ${driftShown(drift)}` : null,
    trustWord(row),
    ro.text ? `runs out ${ro.text}` : null,
    useBy.text ? `use by ${useBy.text}` : null,
  ]
    .filter(Boolean)
    .join(' · ')
  // 900-1279px: the two columns that only show from `wide`.
  const midLine = [ro.text ? `runs out ${ro.text}` : null, useBy.text ? `use by ${useBy.text}` : null]
    .filter(Boolean)
    .join(' · ')
  return (
    <Tr onClick={() => onSelect(row.ingredient_id)} selected={selected}>
      <Td className="w-11">
        <IngredientThumb
          url={row.photo_url}
          name={row.name}
          decorative
          fallback={<TierBadge tier={row.tier} />}
          corner={<TierBadge tier={row.tier} />}
        />
      </Td>
      <Td className="max-w-0">
        <span className="block truncate text-md font-bold" title={row.name}>
          {row.name}
        </span>
        <span className="block truncate text-sm text-ink-2">{sub}</span>
        <span className="block text-sm text-ink-2 compact:hidden">{phoneLine}</span>
        {midLine && (
          <span className={`hidden text-sm compact:block wide:hidden ${ro.alert || useBy.alert ? 'text-bad-ink' : 'text-ink-2'}`}>
            {midLine}
          </span>
        )}
      </Td>
      <Td numeric est>
        <span className="block">{leftCell(row)}</span>
        {left !== null && (
          <span className="block whitespace-normal text-xs text-ink-2">
            {left.text}
            <span className="sr-only">, {left.title.toLowerCase()}</span>
          </span>
        )}
      </Td>
      <Td className={`${COL} text-sm text-ink-2`}>{lastCountCell(row)}</Td>
      <Td numeric alert={abs > 15} strong={abs >= 10} className={COL}>
        {driftShown(drift)}
      </Td>
      <Td className={COL}>
        <StockTrust row={row} />
      </Td>
      {/* Wraps rather than truncates: a withheld forecast's reason is read in full, no hover. */}
      <Td className={`${WIDE} text-sm leading-snug ${ro.alert ? 'text-bad-ink' : ro.reason ? 'text-ink-2' : 'text-ink'}`}>
        {ro.text}
        {ro.title && ro.title !== ro.text && <span className="sr-only">. {ro.title}</span>}
      </Td>
      <Td numeric className={`${WIDE} text-sm ${useBy.alert ? 'text-bad-ink' : 'text-ink'}`}>
        {useBy.text}
      </Td>
    </Tr>
  )
}
