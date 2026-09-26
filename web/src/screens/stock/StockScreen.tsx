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
  ActiveFilters,
  Button,
  Empty,
  ErrorBox,
  FilterBar,
  FilterChip,
  FilterChipRow,
  FilterSelect,
  FilterToggle,
  Loading,
  PageHeader,
  SearchInput,
  Segmented,
  cx,
} from '../../components/ui'
import { gbp, plural } from '../../lib/format'
import { navigate, useLocation } from '../../lib/router'
import { KEYS, stockApi } from '../../lib/stock-api'
import type { StockRow, StockSummary, WrittenOff } from '../../lib/types/stock'
import { Attention } from './Attention'
import { BuyList } from './BuyList'
import { CountFlow } from './CountFlow'
import { StockDrawer } from './StockDrawer'
import { StockList } from './StockList'
import { fmtD } from './fmt'
import { compareRows, matches } from './model'
import type { StockFilter, TierFilter } from './model'

const FILTERS: ReadonlyArray<{ id: StockFilter; label: string }> = [
  { id: 'all', label: 'All' },
  { id: 'out', label: 'Running out' },
  { id: 'soon', label: 'Use soon' },
  { id: 'count', label: 'Count these' },
  { id: 'drift', label: 'Drifting' },
  { id: 'check', label: 'Checklist' },
]

type Tab = 'shelf' | 'buy'
const catOf = (r: StockRow) => r.category ?? 'Uncategorised'

export function StockScreen() {
  const loc = useLocation()
  const tab: Tab = loc.query.get('tab') === 'buy' ? 'buy' : 'shelf'
  const setTab = (t: Tab) => navigate('/stock', { replace: true, query: { tab: t === 'buy' ? 'buy' : undefined } })

  const q = useQuery({ queryKey: KEYS.stock, queryFn: stockApi.stock, staleTime: 60_000 })
  const [cat, setCat] = useState('all')
  const [tierF, setTierF] = useState<TierFilter>('all')
  const [stF, setStF] = useState<StockFilter>('all')
  const [search, setSearch] = useState('')
  const [grouped, setGrouped] = useState(false)
  const [sel, setSel] = useState<number | null>(null)
  const [queue, setQueue] = useState<number[] | null>(null)

  const rows = useMemo(() => q.data?.rows ?? [], [q.data])
  const byId = useMemo(() => new Map(rows.map((r) => [r.ingredient_id, r])), [rows])

  const categories = useMemo(() => {
    const m = new Map<string, number>()
    for (const r of rows) m.set(catOf(r), (m.get(catOf(r)) ?? 0) + 1)
    return [...m.entries()].sort((a, b) => a[0].localeCompare(b[0]))
  }, [rows])

  // Chip counts are over the category-filtered set, before tier and search (§1.1).
  const inCat = useMemo(() => (cat === 'all' ? rows : rows.filter((r) => catOf(r) === cat)), [rows, cat])
  const shown = useMemo(() => {
    const needle = search.trim().toLowerCase()
    const out = inCat
      .filter((r) => matches(r, stF))
      .filter((r) => tierF === 'all' || r.tier === tierF)
      .filter((r) => needle === '' || r.name.toLowerCase().includes(needle))
      .sort(compareRows)
    return grouped ? out.sort((a, b) => catOf(a).localeCompare(catOf(b)) || compareRows(a, b)) : out
  }, [inCat, stF, tierF, search, grouped])

  const pickFilter = (f: StockFilter) => {
    setStF(f)
    setQueue(null)
  }

  // §1.3: every non-C ingredient with a sales rate or a count, least trusted first.
  const startCount = (only?: number) => {
    setSel(null)
    if (only !== undefined) {
      setQueue([only])
      return
    }
    const base = stF === 'all' && cat === 'all' ? rows : shown
    setQueue(
      base
        .filter((r) => r.tier !== 'C' && (r.on_hand.has_count_basis || (r.run_out?.daily_rate_qty ?? null) !== null))
        .sort(compareRows)
        .map((r) => r.ingredient_id),
    )
  }

  const selected = sel === null ? null : (byId.get(sel) ?? null)
  const summary = q.data?.summary

  const activeChips = [
    ...(cat !== 'all' ? [{ key: 'cat', label: `Category: ${cat}`, onRemove: () => setCat('all') }] : []),
    ...(tierF !== 'all' ? [{ key: 'tier', label: `Tier ${tierF}`, onRemove: () => setTierF('all') }] : []),
    ...(stF !== 'all'
      ? [
          {
            key: 'st',
            label: FILTERS.find((f) => f.id === stF)?.label ?? (stF === 'low' ? 'Marked low' : stF),
            onRemove: () => pickFilter('all'),
          },
        ]
      : []),
    ...(search.trim() !== '' ? [{ key: 'q', label: `“${search.trim()}”`, onRemove: () => setSearch('') }] : []),
  ]

  return (
    <>
      <PageHeader title="Stock" subtitle="what is on the shelf: worked out from sales, checked by counting" />
      <div role="tablist" aria-label="Stock views" className="flex flex-none gap-1 border-b border-line-soft px-4 sm:px-5">
        {(
          [
            ['shelf', 'On the shelf'],
            ['buy', 'What to buy'],
          ] as const
        ).map(([id, label]) => (
          <button
            key={id}
            type="button"
            role="tab"
            aria-selected={tab === id}
            onClick={() => setTab(id)}
            className={cx(
              '-mb-px border-b-2 px-3 py-2.5 text-base transition-[color]',
              tab === id ? 'border-brand font-bold text-brand-ink' : 'border-transparent font-medium text-ink-2 hover:text-ink',
            )}
          >
            {label}
          </button>
        ))}
      </div>

      {tab === 'buy' ? (
        <div role="tabpanel" aria-label="What to buy" className="flex min-h-0 min-w-0 flex-1 flex-col">
          <BuyList stockRows={rows} />
        </div>
      ) : (
        <div role="tabpanel" aria-label="On the shelf" className="flex min-h-0 min-w-0 flex-1 flex-col">
          {q.data && queue === null && (
            <Attention
              rows={inCat}
              summary={summary}
              active={stF}
              onPick={pickFilter}
              onBuy={() => setTab('buy')}
            />
          )}

          <div className="flex flex-none flex-col gap-2.5 border-b border-line-soft px-4 py-3 sm:px-5">
            <FilterBar
              label="Filter stock"
              search={
                <SearchInput
                  label="Search ingredients"
                  placeholder="Search ingredients"
                  value={search}
                  onChange={(e) => setSearch(e.target.value)}
                />
              }
              trailing={
                <Button variant="primary" onClick={() => startCount()} disabled={!q.data}>
                  {queue !== null ? 'Counting…' : shown.length < rows.length && stF !== 'all' ? 'Count these' : 'Start a count'}
                </Button>
              }
            >
              <FilterSelect
                label="Category"
                value={cat}
                onChange={setCat}
                options={[
                  { value: 'all', label: `All categories (${rows.length})` },
                  ...categories.map(([c, n]) => ({ value: c, label: `${c} (${n})` })),
                ]}
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
              <FilterToggle active={grouped} onToggle={() => setGrouped((v) => !v)}>
                Group by category
              </FilterToggle>
            </FilterBar>
            <FilterChipRow label="Show">
              {FILTERS.map((f) => (
                <FilterChip
                  key={f.id}
                  active={stF === f.id}
                  onClick={() => pickFilter(f.id)}
                  count={q.data ? inCat.filter((r) => matches(r, f.id)).length : undefined}
                >
                  {f.label}
                </FilterChip>
              ))}
            </FilterChipRow>
            <ActiveFilters
              chips={activeChips}
              onClearAll={() => {
                setCat('all')
                setTierF('all')
                pickFilter('all')
                setSearch('')
              }}
              summary={q.data && activeChips.length > 0 ? `${shown.length} of ${rows.length}` : undefined}
            />
          </div>

          <div className="flex flex-none flex-wrap items-center gap-x-5 gap-y-2 border-b border-line-soft bg-canvas-2 px-4 py-2.5 text-sm text-ink-2 sm:px-5">
            {q.data && <span>Worked out to {fmtD(q.data.as_of)}</span>}
            {summary && <SoonLine rows={rows} summary={summary} onShow={() => pickFilter('soon')} />}
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
                  <StockList rows={shown} selected={sel} onSelect={setSel} groupBy={grouped ? catOf : undefined} />
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
      )}
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
