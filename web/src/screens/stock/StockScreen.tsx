/**
 * Stock (stock-orders-suppliers.md §1). What is on the shelf: worked out from
 * sales, checked by counting.
 *
 * Every figure is the server's: `on_hand.qty` (always theoretical, always
 * italic), the count behind it, drift and trust from the gate, run-out from
 * the same forecast the orders use. Theoretical and counted never share a
 * weight: the estimate is italic, the count upright, and a row with no count
 * shows "—" rather than a ledger sum dressed as an estimate (invariant 6, C9).
 */
import { useMemo, useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import {
  Button,
  Empty,
  ErrorBox,
  FilterChip,
  FilterChipRow,
  Loading,
  PageHeader,
  SearchInput,
  Segmented,
} from '../../components/ui'
import { gbp, plural } from '../../lib/format'
import { KEYS, stockApi } from '../../lib/stock-api'
import type { StockRow, StockSummary, WrittenOff } from '../../lib/types/stock'
import { CountFlow } from './CountFlow'
import { RailChips, RailColumn } from './Rail'
import type { RailItem } from './Rail'
import { StockDrawer } from './StockDrawer'
import { StockList } from './StockList'
import { fmtD } from './fmt'
import { compareRows, matches } from './model'
import type { StockFilter, TierFilter } from './model'

const FILTERS: ReadonlyArray<{ id: StockFilter; label: string }> = [
  { id: 'all', label: 'All' },
  { id: 'count', label: 'Count these' },
  { id: 'out', label: 'Running out' },
  { id: 'soon', label: 'Use soon' },
  { id: 'check', label: 'Checklist' },
]

export function StockScreen() {
  const q = useQuery({ queryKey: KEYS.stock, queryFn: stockApi.stock, staleTime: 60_000 })
  const [cat, setCat] = useState<string | null>(null)
  const [tierF, setTierF] = useState<TierFilter>('all')
  const [stF, setStF] = useState<StockFilter>('all')
  const [search, setSearch] = useState('')
  const [sel, setSel] = useState<number | null>(null)
  const [queue, setQueue] = useState<number[] | null>(null)

  const rows = useMemo(() => q.data?.rows ?? [], [q.data])
  const byId = useMemo(() => new Map(rows.map((r) => [r.ingredient_id, r])), [rows])

  const categories = useMemo(() => {
    const m = new Map<string, number>()
    for (const r of rows) {
      const c = r.category ?? 'Uncategorised'
      m.set(c, (m.get(c) ?? 0) + 1)
    }
    return [...m.entries()].sort((a, b) => a[0].localeCompare(b[0]))
  }, [rows])

  // Chip counts are over the rail-filtered set, before tier and search (§1.1).
  const railed = useMemo(
    () => (cat === null ? rows : rows.filter((r) => (r.category ?? 'Uncategorised') === cat)),
    [rows, cat],
  )
  const shown = useMemo(() => {
    const needle = search.trim().toLowerCase()
    return railed
      .filter((r) => matches(r, stF))
      .filter((r) => tierF === 'all' || r.tier === tierF)
      .filter((r) => needle === '' || r.name.toLowerCase().includes(needle))
      .sort(compareRows)
  }, [railed, stF, tierF, search])

  const railItems: RailItem[] = [
    { kind: 'row', key: 'all', label: 'All', count: rows.length, active: cat === null, onSelect: () => setCat(null) },
    { kind: 'head', label: 'Categories' },
    ...categories.map(
      ([c, n]): RailItem => ({ kind: 'row', key: c, label: c, count: n, active: cat === c, onSelect: () => setCat(c) }),
    ),
  ]

  // §1.3: every non-C ingredient with a sales rate or a count, least trusted first.
  const startCount = (only?: number) => {
    setSel(null)
    if (only !== undefined) {
      setQueue([only])
      return
    }
    setQueue(
      rows
        .filter((r) => r.tier !== 'C' && (r.on_hand.has_count_basis || (r.run_out?.daily_rate_qty ?? null) !== null))
        .sort(compareRows)
        .map((r) => r.ingredient_id),
    )
  }

  const selected = sel === null ? null : (byId.get(sel) ?? null)
  const summary = q.data?.summary

  return (
    <>
      <PageHeader title="Stock" subtitle="what is on the shelf: worked out from sales, checked by counting" />
      <div className="flex min-h-0 min-w-0 flex-1">
        <RailColumn items={railItems} label="Ingredient categories" />
        <div className="flex min-h-0 min-w-0 flex-1 flex-col">
          <RailChips items={railItems} label="Ingredient categories" />

          <div className="flex flex-none flex-col gap-3 border-b border-line-soft px-4 py-3.5 sm:px-5">
            <div className="flex flex-wrap items-center gap-2.5">
              <SearchInput
                label="Search ingredients"
                placeholder="Search ingredients"
                value={search}
                onChange={(e) => setSearch(e.target.value)}
                className="min-w-[12rem] flex-1"
              />
              <Segmented<TierFilter>
                label="Tier"
                showLabel
                value={tierF}
                onChange={setTierF}
                options={[
                  { value: 'all', label: 'All' },
                  { value: 'A', label: 'A', ariaLabel: 'Tier A' },
                  { value: 'B', label: 'B', ariaLabel: 'Tier B' },
                  { value: 'C', label: 'C', ariaLabel: 'Tier C' },
                ]}
              />
              <Button variant="primary" onClick={() => startCount()} disabled={!q.data}>
                {queue !== null ? 'Counting…' : 'Start a count'}
              </Button>
            </div>
            <FilterChipRow label="Filter stock">
              {FILTERS.map((f) => (
                <FilterChip
                  key={f.id}
                  active={stF === f.id}
                  onClick={() => {
                    setStF(f.id)
                    setQueue(null)
                  }}
                  count={q.data ? railed.filter((r) => matches(r, f.id)).length : undefined}
                >
                  {f.label}
                </FilterChip>
              ))}
            </FilterChipRow>
          </div>

          <div className="flex flex-none flex-wrap items-center gap-x-5 gap-y-2 border-b border-line-soft bg-canvas-2 px-4 py-2.5 text-sm text-ink-2 sm:px-5">
            {q.data && <span>Worked out to {fmtD(q.data.as_of)}</span>}
            {summary && (
              <SoonLine
                rows={rows}
                summary={summary}
                onShow={() => {
                  setStF('soon')
                  setQueue(null)
                }}
              />
            )}
            {summary?.written_off_month && <WasteLine w={summary.written_off_month} />}
            <span className="compact:ml-auto">
              Estimates in <em>italic</em>, counts upright
            </span>
          </div>

          {q.isPending ? (
            <Loading what="Working out stock" />
          ) : q.isError ? (
            <ErrorBox error={q.error} what="stock" />
          ) : queue !== null ? (
            <CountFlow queue={queue} byId={byId} onDone={() => setQueue(null)} />
          ) : (
            <div className="flex min-h-0 min-w-0 flex-1">
              <div className="flex min-h-0 min-w-0 flex-1 flex-col">
                {shown.length === 0 ? (
                  <Empty>Nothing in this view.</Empty>
                ) : (
                  <StockList rows={shown} selected={sel} onSelect={setSel} />
                )}
              </div>
              {selected && (
                <StockDrawer
                  row={selected}
                  onClose={() => setSel(null)}
                  onCountNow={() => startCount(selected.ingredient_id)}
                />
              )}
            </div>
          )}
        </div>
      </div>
    </>
  )
}

function SoonLine({ rows, summary, onShow }: { rows: StockRow[]; summary: StockSummary; onShow: () => void }) {
  const n =
    summary.short_dated_batches ??
    rows.reduce((a, r) => a + r.batches.filter((b) => b.days_left !== null && b.days_left <= 3).length, 0)
  if (n === 0) {
    return (
      <button type="button" onClick={onShow} className="font-semibold text-ink-2 hover:text-ink">
        Nothing goes out of date in the next 3 days
      </button>
    )
  }
  const value = summary.expiring_value_pence
  return (
    <button type="button" onClick={onShow} className="font-semibold text-bad-ink hover:underline">
      {n} {plural(n, 'batch', 'batches')} go out of date in 3 days ·{' '}
      {value === null ? 'value unknown (unpriced batch)' : <span className="fig">{gbp(value)}</span>}
    </button>
  )
}

function WasteLine({ w }: { w: WrittenOff }) {
  const v = w.value
  return (
    <span>
      Written off this month:{' '}
      {v.pence === null ? (
        <span title={v.note ?? undefined}>value unknown</span>
      ) : (
        <span className={v.is_estimate ? 'fig italic' : 'fig'}>{gbp(v.pence)}</span>
      )}{' '}
      ({w.count})
    </span>
  )
}
