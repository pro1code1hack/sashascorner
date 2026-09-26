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
import { useIngredients } from '../../lib/menu-api'
import { useIsDocked } from '../../lib/media'
import { href, navigate, useLocation } from '../../lib/router'
import { KEYS, stockApi } from '../../lib/stock-api'
import type { StockRow, StockSummary, TrustLabel, WrittenOff } from '../../lib/types/stock'
import { BuyList } from './BuyList'
import { CountFlow } from './CountFlow'
import { NeedsAttention } from './NeedsAttention'
import { StockItemPage } from './StockItemPage'
import { StockList } from './StockList'
import { fmtD } from './fmt'
import { STOCK_SORTS, TRUST_WORD, compareRows, matches, stockSorter, trustOf } from './model'
import type { StockFilter, StockSort, TierFilter } from './model'

const FILTERS: ReadonlyArray<{ id: StockFilter; label: string }> = [
  { id: 'all', label: 'All' },
  { id: 'out', label: 'Running out' },
  { id: 'soon', label: 'Use soon' },
  { id: 'count', label: 'Count these' },
  { id: 'drift', label: 'Drifting' },
  { id: 'check', label: 'Checklist' },
]

type Tab = 'shelf' | 'attention' | 'buy'
const catOf = (r: StockRow) => r.category ?? 'Uncategorised'

const STORAGE_LABEL: Record<string, string> = { AMBIENT: 'Ambient', CHILLED: 'Chilled', FROZEN: 'Frozen' }
const UNIT_LABEL: Record<string, string> = { L: 'litres', ML: 'ml', KG: 'kg', G: 'grams', EACH: 'units' }
const USE_LABEL: Record<string, string> = { used: 'Used in recipes', unused: 'Not used', '10': 'Used in 10+ items' }
const TRUST_KEYS: TrustLabel[] = ['trusted', 'drifting', 'excluded', 'not_yet_judged', 'never_counted']

/** The same filters as Ingredients (owner, 2026-09-26: "they are sort of identical entities"). */
interface MoreFilters {
  sup: string
  storage: string
  trust: string
  unit: string
  use: string
  est: boolean
}
const NO_MORE: MoreFilters = { sup: 'all', storage: 'all', trust: 'all', unit: 'all', use: 'all', est: false }

export function StockScreen() {
  const loc = useLocation()
  const qt = loc.query.get('tab')
  const tab: Tab = qt === 'buy' ? 'buy' : qt === 'attention' ? 'attention' : 'shelf'
  const setTab = (t: Tab) => navigate('/stock', { replace: true, query: { tab: t === 'shelf' ? undefined : t } })
  // `#/stock/<id>`: the ingredient's own page. This component stays mounted, so
  // the list's filters survive the round trip.
  const itemId = loc.segments[1] !== undefined && /^\d+$/.test(loc.segments[1]) ? Number(loc.segments[1]) : null

  const q = useQuery({ queryKey: KEYS.stock, queryFn: stockApi.stock, staleTime: 60_000 })
  const [cat, setCat] = useState('all')
  const [tierF, setTierF] = useState<TierFilter>('all')
  const [stF, setStF] = useState<StockFilter>('all')
  const [search, setSearch] = useState('')
  const [grouped, setGrouped] = useState(false)
  const [more, setMore] = useState<MoreFilters>(NO_MORE)
  const [sort, setSort] = useState<StockSort>('attention')
  const docked = useIsDocked()
  const setM = <K extends keyof MoreFilters>(k: K, v: MoreFilters[K]) => setMore((p) => ({ ...p, [k]: v }))
  // Suppliers and recipe use come from the ingredient list (same ids): /api/stock
  // carries only the preferred pack's supplier.
  const ingredients = useIngredients()
  const ingById = useMemo(() => new Map((ingredients.data?.rows ?? []).map((r) => [r.ingredient_id, r])), [ingredients.data])
  const suppliers = ingredients.data?.suppliers ?? []
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
      .filter((r) => needle === '' || r.name.toLowerCase().includes(needle) || (ingById.get(r.ingredient_id)?.note ?? '').toLowerCase().includes(needle))
      .filter((r) => {
        const ing = ingById.get(r.ingredient_id)
        const sups = ing ? ing.suppliers.map((x) => x.supplier_id) : r.pack ? [r.pack.supplier_id] : []
        if (more.sup === 'none' && sups.length > 0) return false
        if (more.sup !== 'all' && more.sup !== 'none' && !sups.includes(Number(more.sup))) return false
        if (more.storage !== 'all' && r.shelf_life.storage !== more.storage) return false
        if (more.trust !== 'all' && trustOf(r) !== more.trust) return false
        if (more.unit !== 'all' && r.unit !== more.unit) return false
        const used = ing?.used_in_count ?? null
        if (more.use === 'used' && (used === null || used === 0)) return false
        if (more.use === 'unused' && used !== 0) return false
        if (more.use === '10' && (used === null || used < 10)) return false
        if (more.est && !r.unit_cost.is_estimate) return false
        return true
      })
    const by = stockSorter(sort)
    out.sort(by)
    return grouped ? out.sort((a, b) => catOf(a).localeCompare(catOf(b)) || by(a, b)) : out
  }, [inCat, stF, tierF, search, grouped, more, sort, ingById])
  const units = useMemo(() => [...new Set(rows.map((r) => r.unit))].sort(), [rows])

  const pickFilter = (f: StockFilter) => {
    setStF(f)
    setQueue(null)
  }

  // §1.3: every non-C ingredient with a sales rate or a count, least trusted first.
  const startCount = () => {
    const base = stF === 'all' && cat === 'all' ? rows : shown
    setQueue(
      base
        .filter((r) => r.tier !== 'C' && (r.on_hand.has_count_basis || (r.run_out?.daily_rate_qty ?? null) !== null))
        .sort(compareRows)
        .map((r) => r.ingredient_id),
    )
  }

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
    ...(more.sup !== 'all'
      ? [
          {
            key: 'sup',
            label:
              more.sup === 'none'
                ? 'No supplier'
                : `Supplier: ${suppliers.find((x) => String(x.supplier_id) === more.sup)?.name ?? '?'}`,
            onRemove: () => setM('sup', 'all'),
          },
        ]
      : []),
    ...(more.storage !== 'all'
      ? [{ key: 'storage', label: STORAGE_LABEL[more.storage] ?? more.storage, onRemove: () => setM('storage', 'all') }]
      : []),
    ...(more.trust !== 'all'
      ? [{ key: 'trust', label: TRUST_WORD[more.trust as TrustLabel], onRemove: () => setM('trust', 'all') }]
      : []),
    ...(more.unit !== 'all'
      ? [{ key: 'unit', label: `Counted in ${UNIT_LABEL[more.unit] ?? more.unit}`, onRemove: () => setM('unit', 'all') }]
      : []),
    ...(more.use !== 'all' ? [{ key: 'use', label: USE_LABEL[more.use] ?? more.use, onRemove: () => setM('use', 'all') }] : []),
    ...(more.est ? [{ key: 'est', label: 'Estimated prices', onRemove: () => setM('est', false) }] : []),
  ]
  const searchBox = (
    <SearchInput
      label="Search ingredients"
      placeholder="Search name or note"
      value={search}
      onChange={(e) => setSearch(e.target.value)}
    />
  )
  const countButton = (
    <Button variant="primary" onClick={() => startCount()} disabled={!q.data}>
      {queue !== null ? 'Counting…' : shown.length < rows.length && stF !== 'all' ? 'Count these' : 'Start a count'}
    </Button>
  )

  if (itemId !== null) return <StockItemPage id={itemId} back={href('/stock', { tab: tab === 'shelf' ? undefined : tab })} />
  const attentionCount = new Set(
    (['soon', 'out', 'drift', 'count', 'low'] as const).flatMap((f) => rows.filter((r) => matches(r, f)).map((r) => r.ingredient_id)),
  ).size

  return (
    <>
      <PageHeader title="Stock" subtitle="what is on the shelf: worked out from sales, checked by counting" />
      <div role="tablist" aria-label="Stock views" className="flex flex-none gap-1 border-b border-line-soft px-4 sm:px-5">
        {(
          [
            ['shelf', 'On the shelf'],
            ['attention', 'Needs attention'],
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
            {id === 'attention' && q.data && attentionCount > 0 && (
              <span className="fig ml-1.5 rounded-full bg-alert-wash px-1.5 text-xs font-bold text-bad-ink">{attentionCount}</span>
            )}
          </button>
        ))}
      </div>

      {queue !== null ? (
        <div className="flex min-h-0 min-w-0 flex-1 flex-col">
          <CountFlow queue={queue} byId={byId} onDone={() => setQueue(null)} />
        </div>
      ) : tab === 'attention' ? (
        <div role="tabpanel" aria-label="Needs attention" className="flex min-h-0 min-w-0 flex-1 flex-col">
          {q.isPending ? (
            <Loading what="Working out stock" />
          ) : q.isError ? (
            <ErrorBox error={q.error} what="stock" />
          ) : (
            <NeedsAttention
              rows={rows}
              summary={summary}
              onCount={(ids) => setQueue(ids)}
              onBuy={() => setTab('buy')}
              onShowInList={(f) => {
                pickFilter(f)
                setTab('shelf')
              }}
            />
          )}
        </div>
      ) : tab === 'buy' ? (
        <div role="tabpanel" aria-label="What to buy" className="flex min-h-0 min-w-0 flex-1 flex-col">
          <BuyList stockRows={rows} />
        </div>
      ) : (
        <div role="tabpanel" aria-label="On the shelf" className="flex min-h-0 min-w-0 flex-1 flex-col">
          <div className="flex flex-none flex-col gap-2 border-b border-line bg-surface px-4 pb-2.5 pt-3 sm:px-5">
            <FilterBar
              label="Filter stock"
              search={searchBox}
              activeCount={activeChips.length}
              trailing={
                <>
                  <FilterSelect label="Sort" value={sort} allValue={sort} onChange={(v) => setSort(v as StockSort)} options={STOCK_SORTS} />
                  {docked && countButton}
                </>
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
              <FilterSelect
                label="Supplier"
                value={more.sup}
                onChange={(v) => setM('sup', v)}
                options={[
                  { value: 'all', label: 'All suppliers' },
                  { value: 'none', label: 'No supplier yet' },
                  ...suppliers.map((x) => ({ value: String(x.supplier_id), label: x.name })),
                ]}
              />
              <FilterSelect
                label="Storage"
                value={more.storage}
                onChange={(v) => setM('storage', v)}
                options={[{ value: 'all', label: 'Any storage' }, ...Object.entries(STORAGE_LABEL).map(([value, label]) => ({ value, label }))]}
              />
              <FilterSelect
                label="Trust"
                value={more.trust}
                onChange={(v) => setM('trust', v)}
                options={[{ value: 'all', label: 'Any trust' }, ...TRUST_KEYS.map((t) => ({ value: t, label: TRUST_WORD[t] }))]}
              />
              <FilterSelect
                label="Counted in"
                value={more.unit}
                onChange={(v) => setM('unit', v)}
                options={[{ value: 'all', label: 'Any unit' }, ...units.map((u) => ({ value: u, label: `In ${UNIT_LABEL[u] ?? u}` }))]}
              />
              <FilterSelect
                label="Used in"
                value={more.use}
                onChange={(v) => setM('use', v)}
                options={[{ value: 'all', label: 'Used or not' }, ...Object.entries(USE_LABEL).map(([value, label]) => ({ value, label }))]}
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
              <FilterToggle active={more.est} onToggle={() => setM('est', !more.est)}>
                Estimated prices only
              </FilterToggle>
              <FilterToggle active={grouped} onToggle={() => setGrouped((v) => !v)}>
                Group by category
              </FilterToggle>
            </FilterBar>
            {!docked && <div className="flex">{countButton}</div>}
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
                setMore(NO_MORE)
              }}
              summary={q.data ? `${shown.length} of ${rows.length} ingredients` : undefined}
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
          ) : (
            <div className="flex min-h-0 min-w-0 flex-1 flex-col">
              {shown.length === 0 ? (
                <Empty>Nothing in this view.</Empty>
              ) : (
                <StockList rows={shown} selected={null} onSelect={(id) => navigate(`/stock/${id}`)} groupBy={grouped ? catOf : undefined} />
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
      <button type="button" onClick={onShow} className="min-h-6 text-left font-semibold text-ink-2 hover:text-ink">
        Nothing goes out of date in the next 3 days
      </button>
    )
  }
  const value = summary.expiring_value_pence
  return (
    <button type="button" onClick={onShow} className="min-h-6 text-left font-semibold text-bad-ink hover:underline">
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
