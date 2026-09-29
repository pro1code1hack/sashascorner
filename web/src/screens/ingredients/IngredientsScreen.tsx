/**
 * Ingredients & prices (recipes-menu-ingredients.md §V3): what things cost.
 *
 * Filters sit in one bar across the top (owner, 2026-09-26: no category rail, no
 * stacked selects in the list column), with a removable chip per active filter
 * and "Clear all". Below: the list and the ingredient's detail. Selection is in
 * the URL (`#/ingredients?id=12`); `?cat=` is still read once for old links.
 * Below 900px the list and the detail take turns, and the filters fold behind a
 * "Filters" button so the list is not pushed off the first screen.
 */
import { useMemo, useState } from 'react'
import {
  ActiveFilters,
  Button,
  Empty,
  ErrorBox,
  FilterBar,
  FilterSelect,
  FilterToggle,
  Loading,
  PageHeader,
  SearchInput,
  cx,
} from '../../components/ui'
import type { ActiveFilterChip, FilterOption } from '../../components/ui'
import { cmp, fromMoney } from '../../lib/dec'
import { useIngredients } from '../../lib/menu-api'
import { navigate, useLocation } from '../../lib/router'
import type { IngredientRow, Unit } from '../../lib/types/menu'
import { MONEY_INPUT, gbp, poundsToPence, qtyText, unitPrice, unitWord } from '../menu/common/figures'
import { CreateIngredient, IngredientDetailPane } from './Detail'
import { IngredientThumb } from './Thumb'

const ROW_GRID = 'compact:grid-cols-[44px_minmax(0,1fr)_150px_170px_70px_90px]'

type SortKey = 'az' | 'za' | 'unit-desc' | 'unit-asc' | 'pack-desc' | 'pack-asc' | 'most' | 'least' | 'sups' | 'life'

const SORTS: FilterOption[] = [
  { value: 'az', label: 'Sort: A–Z' },
  { value: 'za', label: 'Sort: Z–A' },
  { value: 'unit-desc', label: 'Sort: cost per unit, high' },
  { value: 'unit-asc', label: 'Sort: cost per unit, low' },
  { value: 'pack-desc', label: 'Sort: pack cost, high' },
  { value: 'pack-asc', label: 'Sort: pack cost, low' },
  { value: 'most', label: 'Sort: most used' },
  { value: 'least', label: 'Sort: least used' },
  { value: 'sups', label: 'Sort: fewest suppliers' },
  { value: 'life', label: 'Sort: shortest shelf life' },
]

const UNITS: Unit[] = ['L', 'ML', 'KG', 'G', 'EACH']
const STORAGE_LABEL: Record<string, string> = { AMBIENT: 'Ambient', CHILLED: 'Chilled', FROZEN: 'Frozen' }
const USE_LABEL: Record<string, string> = {
  used: 'Used in recipes',
  unused: 'Not used',
  '3': 'Used in 3+ items',
  '10': 'Used in 10+ items',
  '25': 'Used in 25+ items',
}

const packKey = (r: IngredientRow): string | null => (r.pack ? `${qtyText(r.pack.pack_size)} ${unitWord(r.pack.pack_unit)}` : null)

interface Filters {
  q: string
  cat: string
  sup: string
  unit: string
  storage: string
  pack: string
  use: string
  min: string
  max: string
  est: boolean
}

const EMPTY: Filters = { q: '', cat: 'all', sup: 'all', unit: 'all', storage: 'all', pack: 'all', use: 'all', min: '', max: '', est: false }

export function IngredientsScreen() {
  const loc = useLocation()
  const data = useIngredients()
  const [f, setF] = useState<Filters>(() => {
    const c = loc.query.get('cat')
    return c === 'est' ? { ...EMPTY, est: true } : c ? { ...EMPTY, cat: c } : EMPTY
  })
  const [sort, setSort] = useState<SortKey>('az')
  const set = <K extends keyof Filters>(k: K, v: Filters[K]) => setF((p) => ({ ...p, [k]: v }))

  const idParam = loc.query.get('id')
  const creating = loc.query.get('new') === '1'
  const rows = useMemo(() => data.data?.rows ?? [], [data.data])
  const id = idParam && /^\d+$/.test(idParam) ? Number(idParam) : null

  const go = (next: { id?: number | null; create?: boolean }) => {
    const query: Record<string, string> = {}
    const i = next.id === undefined ? (idParam ? Number(idParam) : null) : next.id
    if (i !== null && !next.create) query.id = String(i)
    if (next.create) query.new = '1'
    navigate('/ingredients', { query, replace: true })
  }

  const minP = poundsToPence(f.min)
  const maxP = poundsToPence(f.max)

  const shown = useMemo(() => {
    const needle = f.q.trim().toLowerCase()
    const min = f.use === 'used' ? 1 : /^\d+$/.test(f.use) ? Number(f.use) : 0
    const out = rows.filter((r) => {
      if (needle && !r.name.toLowerCase().includes(needle) && !(r.note ?? '').toLowerCase().includes(needle)) return false
      if (f.cat !== 'all' && (r.category ?? 'Other') !== f.cat) return false
      if (f.sup === 'none' && r.suppliers.length > 0) return false
      if (f.sup !== 'all' && f.sup !== 'none' && !r.suppliers.some((s) => String(s.supplier_id) === f.sup)) return false
      if (f.unit !== 'all' && r.unit !== f.unit) return false
      if (f.storage !== 'all' && r.storage !== f.storage) return false
      if (f.pack !== 'all' && packKey(r) !== f.pack) return false
      if (f.use === 'unused' && r.used_in_count > 0) return false
      if (min > 0 && r.used_in_count < min) return false
      if (f.est && !r.unit_cost.is_estimate) return false
      // A range excludes ingredients with no price: unknown is not zero (invariant 8).
      if ((minP !== null || maxP !== null) && !r.pack) return false
      if (minP !== null && r.pack && r.pack.pack_cost_pence < minP) return false
      if (maxP !== null && r.pack && r.pack.pack_cost_pence > maxP) return false
      return true
    })
    const byName = (a: IngredientRow, b: IngredientRow) => a.name.localeCompare(b.name)
    // Missing values always sort last, whichever direction.
    const byUnit = (dir: 1 | -1) => (a: IngredientRow, b: IngredientRow) => {
      if (a.unit_cost.pence === null) return b.unit_cost.pence === null ? byName(a, b) : 1
      if (b.unit_cost.pence === null) return -1
      return dir * cmp(fromMoney(a.unit_cost.pence), fromMoney(b.unit_cost.pence)) || byName(a, b)
    }
    const byPack = (dir: 1 | -1) => (a: IngredientRow, b: IngredientRow) => {
      if (!a.pack) return b.pack ? 1 : byName(a, b)
      if (!b.pack) return -1
      return dir * (a.pack.pack_cost_pence - b.pack.pack_cost_pence) || byName(a, b)
    }
    const cmpFn: Record<SortKey, (a: IngredientRow, b: IngredientRow) => number> = {
      az: byName,
      za: (a, b) => byName(b, a),
      'unit-desc': byUnit(-1),
      'unit-asc': byUnit(1),
      'pack-desc': byPack(-1),
      'pack-asc': byPack(1),
      most: (a, b) => b.used_in_count - a.used_in_count || byName(a, b),
      least: (a, b) => a.used_in_count - b.used_in_count || byName(a, b),
      sups: (a, b) => a.suppliers.length - b.suppliers.length || byName(a, b),
      life: (a, b) => (a.shelf_life_days ?? Infinity) - (b.shelf_life_days ?? Infinity) || byName(a, b),
    }
    out.sort(cmpFn[sort])
    return out
  }, [rows, f, sort, minP, maxP])

  const categories = (data.data?.categories ?? []).map(([n]) => n)
  const suppliers = data.data?.suppliers ?? []
  const packs = useMemo(() => {
    const m = new Map<string, number>()
    for (const r of rows) {
      const k = packKey(r)
      if (k) m.set(k, (m.get(k) ?? 0) + 1)
    }
    return [...m.entries()].sort((a, b) => b[1] - a[1] || a[0].localeCompare(b[0]))
  }, [rows])

  const chips: ActiveFilterChip[] = []
  if (f.q.trim()) chips.push({ key: 'q', label: `“${f.q.trim()}”`, onRemove: () => set('q', '') })
  if (f.cat !== 'all') chips.push({ key: 'cat', label: `Category: ${f.cat}`, onRemove: () => set('cat', 'all') })
  if (f.sup !== 'all')
    chips.push({
      key: 'sup',
      label: f.sup === 'none' ? 'No supplier' : `Supplier: ${suppliers.find((s) => String(s.supplier_id) === f.sup)?.name ?? '?'}`,
      onRemove: () => set('sup', 'all'),
    })
  if (f.storage !== 'all') chips.push({ key: 'storage', label: STORAGE_LABEL[f.storage] ?? f.storage, onRemove: () => set('storage', 'all') })
  if (f.unit !== 'all') chips.push({ key: 'unit', label: `Per ${unitWord(f.unit as Unit)}`, onRemove: () => set('unit', 'all') })
  if (f.pack !== 'all') chips.push({ key: 'pack', label: `Pack: ${f.pack}`, onRemove: () => set('pack', 'all') })
  if (f.use !== 'all') chips.push({ key: 'use', label: USE_LABEL[f.use] ?? f.use, onRemove: () => set('use', 'all') })
  if (minP !== null || maxP !== null)
    chips.push({
      key: 'range',
      label: `Pack ${minP !== null ? `£${f.min}` : 'any'}–${maxP !== null ? `£${f.max}` : 'any'}`,
      onRemove: () => setF((p) => ({ ...p, min: '', max: '' })),
    })
  if (f.est) chips.push({ key: 'est', label: 'Estimated prices', onRemove: () => set('est', false) })

  const showList = id === null && !creating
  const rangeCls =
    'fig h-[34px] w-[64px] rounded-full border border-line-control bg-surface px-2.5 text-right text-base outline-none focus-visible:edge-brand'

  return (
    <>
      <PageHeader
        title="Ingredients & prices"
        subtitle="what things cost"
        saved={data.isFetching ? 'Loading…' : 'Saved'}
        actions={
          <Button variant="primary" className="rounded-[18px] px-[18px] text-lg" onClick={() => go({ create: true })}>
            + Add ingredient
          </Button>
        }
      />
      {showList && (
        <div className="flex-none border-b border-line bg-surface px-4 pb-2.5 pt-3 sm:px-5">
          <FilterBar
            activeCount={chips.length}
            label="Filter ingredients"
            search={
              <SearchInput label="Search ingredients" placeholder="Search name or note" value={f.q} onChange={(e) => set('q', e.target.value)} />
            }
            trailing={<FilterSelect label="Sort" value={sort} allValue={sort} onChange={(v) => setSort(v as SortKey)} options={SORTS} />}
          >
            <FilterSelect
              label="Category"
              value={f.cat}
              onChange={(v) => set('cat', v)}
              options={[{ value: 'all', label: 'All categories' }, ...categories.map((c) => ({ value: c, label: c }))]}
            />
            <FilterSelect
              label="Supplier"
              value={f.sup}
              onChange={(v) => set('sup', v)}
              options={[
                { value: 'all', label: 'All suppliers' },
                { value: 'none', label: 'No supplier yet' },
                ...suppliers.map((s) => ({ value: String(s.supplier_id), label: s.name })),
              ]}
            />
            <FilterSelect
              label="Storage"
              value={f.storage}
              onChange={(v) => set('storage', v)}
              options={[{ value: 'all', label: 'Any storage' }, ...Object.entries(STORAGE_LABEL).map(([value, label]) => ({ value, label }))]}
            />
            <FilterSelect
              label="Costed per"
              value={f.unit}
              onChange={(v) => set('unit', v)}
              options={[{ value: 'all', label: 'Any unit' }, ...UNITS.map((u) => ({ value: u, label: `Per ${unitWord(u)}` }))]}
            />
            <FilterSelect
              label="Pack size"
              value={f.pack}
              onChange={(v) => set('pack', v)}
              options={[{ value: 'all', label: 'Any pack size' }, ...packs.map(([k, n]) => ({ value: k, label: `${k} (${n})` }))]}
            />
            <FilterSelect
              label="Used in"
              value={f.use}
              onChange={(v) => set('use', v)}
              options={[{ value: 'all', label: 'Used or not' }, ...Object.entries(USE_LABEL).map(([value, label]) => ({ value, label }))]}
            />
            <span className="flex items-center gap-1 text-sm text-ink-2" role="group" aria-label="Pack cost range in pounds">
              Pack £
              <input
                aria-label="Pack cost from, pounds"
                inputMode="decimal"
                placeholder="min"
                value={f.min}
                onChange={(e) => MONEY_INPUT.test(e.target.value) && set('min', e.target.value)}
                className={rangeCls}
              />
              –
              <input
                aria-label="Pack cost to, pounds"
                inputMode="decimal"
                placeholder="max"
                value={f.max}
                onChange={(e) => MONEY_INPUT.test(e.target.value) && set('max', e.target.value)}
                className={rangeCls}
              />
            </span>
            <FilterToggle active={f.est} onToggle={() => set('est', !f.est)} count={data.data?.estimated_count}>
              Estimated prices only
            </FilterToggle>
          </FilterBar>
          <ActiveFilters
            className="mt-2"
            summary={data.data ? `${shown.length} of ${rows.length} ingredients` : undefined}
            chips={chips}
            onClearAll={() => setF(EMPTY)}
          />
        </div>
      )}
      {showList ? (
        <div className="min-h-0 flex-1 overflow-y-auto bg-canvas px-4 pb-6 pt-3.5 sm:px-5">
          {data.isLoading && <Loading what="Loading ingredients" />}
          {data.error && <ErrorBox error={data.error} what="ingredients" />}
          {data.data && shown.length === 0 && (
            <Empty roomy>
              Nothing matches these filters.{' '}
              <button type="button" className="font-bold text-brand-ink underline" onClick={() => setF(EMPTY)}>
                Clear them
              </button>
            </Empty>
          )}
          {shown.length > 0 && <ListView rows={shown} onOpen={(i) => go({ id: i })} />}
        </div>
      ) : (
        <div className="flex min-h-0 flex-1">
          <div className="flex min-h-0 min-w-0 flex-1 flex-col">
            <button type="button" className="flex-none px-4 pt-3 text-left text-base font-bold text-brand-ink sm:px-5" onClick={() => go({ id: null })}>
              ‹ All ingredients
            </button>
            {creating ? (
              <CreateIngredient
                categories={categories}
                suppliers={suppliers}
                defaultCategory={f.cat !== 'all' ? f.cat : ''}
                onCreated={(newId) => go({ id: newId })}
                onCancel={() => go({ id: null })}
              />
            ) : id !== null ? (
              <IngredientDetailPane key={id} id={id} categories={categories} onRetired={() => go({ id: null })} />
            ) : null}
          </div>
        </div>
      )}
    </>
  )
}

function ListView({ rows, onOpen }: { rows: IngredientRow[]; onOpen: (id: number) => void }) {
  return (
    <div className="overflow-hidden rounded-card-lg bg-surface shadow-raised">
      <div className={cx('hidden gap-3 border-b border-line px-3.5 py-2 text-label font-bold uppercase tracking-[.06em] text-ink-3 compact:grid', ROW_GRID)}>
        <span />
        <span>Ingredient</span>
        <span className="text-right">Cost / unit</span>
        <span className="text-right">Pack</span>
        <span className="text-right">Keeps</span>
        <span className="text-right">Used in</span>
      </div>
      <ul>
        {rows.map((r) => {
          const preferred = r.suppliers.find((s) => s.is_preferred) ?? r.suppliers[0]
          const more = r.suppliers.length > 1 ? ` +${r.suppliers.length - 1}` : ''
          return (
            <li key={r.ingredient_id} className="border-b border-line-row last:border-b-0">
              <button
                type="button"
                onClick={() => onOpen(r.ingredient_id)}
                className={cx(
                  'grid w-full grid-cols-[44px_minmax(0,1fr)_auto] items-center gap-x-3 px-3.5 py-2.5 text-left text-ink hover:bg-canvas-2',
                  ROW_GRID,
                  r.retired && 'opacity-60',
                )}
              >
                <IngredientThumb
                  url={r.photo_url}
                  name={r.name}
                  fallback={
                    <span aria-hidden="true" className="text-lg font-extrabold text-ink-3">
                      {r.name.trim().charAt(0).toUpperCase()}
                    </span>
                  }
                />
                <span className="min-w-0">
                  <span className="block truncate text-md font-bold">{r.name}</span>
                  <span className="block truncate text-sm text-ink-2">
                    {r.category ?? 'Other'}
                    {preferred ? ` · ${preferred.name}${more}` : ' · no supplier'}
                    {r.storage !== 'AMBIENT' ? ` · ${(STORAGE_LABEL[r.storage] ?? r.storage).toLowerCase()}` : ''}
                    {r.retired ? ' · retired' : ''}
                  </span>
                </span>
                <span className={cx('fig whitespace-nowrap text-right text-base', r.unit_cost.is_estimate && 'italic')}>
                  {r.unit_cost.pence === null ? (
                    <span className="text-ink-2">no price</span>
                  ) : (
                    `${unitPrice(r.unit_cost.pence)} / ${unitWord(r.unit)}`
                  )}
                </span>
                <span className={cx('fig hidden truncate text-right text-base compact:block', r.unit_cost.is_estimate && 'italic')}>
                  {r.pack ? `${packKey(r)} · ${gbp(r.pack.pack_cost_pence)}` : <span className="text-ink-2">—</span>}
                </span>
                <span className="fig hidden text-right text-base compact:block">
                  {r.shelf_life_days === null ? <span className="text-ink-2">—</span> : `${r.shelf_life_days}d`}
                </span>
                <span className="fig hidden text-right text-base compact:block">
                  {r.used_in_count} {r.used_in_count === 1 ? 'item' : 'items'}
                </span>
              </button>
            </li>
          )
        })}
      </ul>
      <p className="border-t border-line px-3.5 py-2 text-xs text-ink-2">
        <em>Italic</em> costs are estimates. Keeps is the unopened shelf life.
      </p>
    </div>
  )
}
