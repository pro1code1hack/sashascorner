/**
 * Ingredients & prices (recipes-menu-ingredients.md §V3): what things cost.
 *
 * Rail (All / Estimated prices / categories), a filterable list, and the
 * ingredient's detail. Selection is in the URL (`#/ingredients?id=12&cat=Syrup`).
 * Below 900px the list and the detail take turns (list → detail with a back
 * link), so nothing scrolls sideways.
 */
import { useMemo, useState } from 'react'
import { Button, ErrorBox, Loading, PageHeader, SearchInput, cx } from '../../components/ui'
import { cmp, fromMoney } from '../../lib/dec'
import { useIngredients } from '../../lib/menu-api'
import { useIsDocked } from '../../lib/media'
import { navigate, useLocation } from '../../lib/router'
import type { IngredientRow } from '../../lib/types/menu'
import { unitPrice, unitWord } from '../menu/common/figures'
import { RailChips, RailColumn } from '../menu/common/Rail'
import type { RailItem } from '../menu/common/Rail'
import { CreateIngredient, IngredientDetailPane } from './Detail'

type SortKey = 'az' | 'cost' | 'most' | 'sups'

export function IngredientsScreen() {
  const loc = useLocation()
  const data = useIngredients()
  const docked = useIsDocked()
  const [q, setQ] = useState('')
  const [sup, setSup] = useState('all')
  const [price, setPrice] = useState('all')
  const [use, setUse] = useState('all')
  const [sort, setSort] = useState<SortKey>('az')
  const cat = loc.query.get('cat') ?? 'all'
  const idParam = loc.query.get('id')
  const creating = loc.query.get('new') === '1'
  const rows = data.data?.rows ?? []
  const firstId = rows[0]?.ingredient_id ?? null
  const id = idParam && /^\d+$/.test(idParam) ? Number(idParam) : docked ? firstId : null

  const go = (next: { cat?: string; id?: number | null; create?: boolean }) => {
    const query: Record<string, string> = {}
    const c = next.cat ?? cat
    if (c !== 'all') query.cat = c
    const i = next.id === undefined ? (idParam ? Number(idParam) : null) : next.id
    if (i !== null && !next.create) query.id = String(i)
    if (next.create) query.new = '1'
    navigate('/ingredients', { query, replace: true })
  }

  const shown = useMemo(() => {
    const needle = q.trim().toLowerCase()
    const out = rows.filter((r) => {
      if (cat === 'est' && !r.unit_cost.is_estimate) return false
      if (cat !== 'all' && cat !== 'est' && (r.category ?? 'Other') !== cat) return false
      if (needle && !r.name.toLowerCase().includes(needle)) return false
      if (sup === 'none' && r.suppliers.length > 0) return false
      if (sup !== 'all' && sup !== 'none' && !r.suppliers.some((s) => String(s.supplier_id) === sup)) return false
      if (price === 'invoice' && (r.unit_cost.is_estimate || r.unit_cost.pence === null)) return false
      if (price === 'est' && !r.unit_cost.is_estimate) return false
      if (use === 'used' && r.used_in_count === 0) return false
      if (use === 'unused' && r.used_in_count > 0) return false
      return true
    })
    const byCost = (a: IngredientRow, b: IngredientRow) => {
      if (a.unit_cost.pence === null) return 1
      if (b.unit_cost.pence === null) return -1
      return cmp(fromMoney(b.unit_cost.pence), fromMoney(a.unit_cost.pence))
    }
    out.sort((a, b) =>
      sort === 'cost'
        ? byCost(a, b)
        : sort === 'most'
          ? b.used_in_count - a.used_in_count || a.name.localeCompare(b.name)
          : sort === 'sups'
            ? a.suppliers.length - b.suppliers.length || a.name.localeCompare(b.name)
            : a.name.localeCompare(b.name),
    )
    return out
  }, [rows, cat, q, sup, price, use, sort])

  const filtered = q !== '' || sup !== 'all' || price !== 'all' || use !== 'all' || cat !== 'all'
  const categories = (data.data?.categories ?? []).map(([n]) => n)
  const rail: RailItem[] = [
    { kind: 'row', key: 'all', label: 'All', count: data.data?.total ?? null, active: cat === 'all', onSelect: () => go({ cat: 'all' }) },
    {
      kind: 'row',
      key: 'est',
      label: 'Estimated prices',
      count: data.data?.estimated_count ?? null,
      active: cat === 'est',
      onSelect: () => go({ cat: 'est' }),
    },
    { kind: 'head', label: 'Categories' },
    ...(data.data?.categories ?? []).map(
      ([name, count]): RailItem => ({ kind: 'row', key: `c:${name}`, label: name, count, active: cat === name, onSelect: () => go({ cat: name }) }),
    ),
  ]

  const showList = docked || (id === null && !creating)
  const showDetail = docked || id !== null || creating
  const selectCls = 'min-w-0 rounded-control border border-line-strong bg-surface px-1 py-[3px] text-base'

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
      <RailChips items={rail} label="Ingredient categories" />
      <div className="flex min-h-0 flex-1">
        <RailColumn items={rail} label="Ingredient categories" />
        {showList && (
          <div className="flex min-h-0 w-full min-w-0 flex-col border-r border-line compact:w-[290px] compact:flex-none wide:w-[320px]">
            <div className="flex flex-none flex-col gap-1.5 border-b border-line px-3 py-2.5">
              <SearchInput label="Search ingredients" placeholder="Search ingredients" value={q} onChange={(e) => setQ(e.target.value)} />
              <div className="grid grid-cols-2 gap-1.5">
                <select aria-label="Supplier" value={sup} onChange={(e) => setSup(e.target.value)} className={selectCls}>
                  <option value="all">All suppliers</option>
                  <option value="none">No supplier yet</option>
                  {(data.data?.suppliers ?? []).map((s) => (
                    <option key={s.supplier_id} value={s.supplier_id}>
                      {s.name}
                    </option>
                  ))}
                </select>
                <select aria-label="Price" value={price} onChange={(e) => setPrice(e.target.value)} className={selectCls}>
                  <option value="all">All prices</option>
                  <option value="invoice">From invoice</option>
                  <option value="est">Estimates</option>
                </select>
                <select aria-label="Use" value={use} onChange={(e) => setUse(e.target.value)} className={selectCls}>
                  <option value="all">Used or not</option>
                  <option value="used">Used in recipes</option>
                  <option value="unused">Not used</option>
                </select>
                <select aria-label="Sort" value={sort} onChange={(e) => setSort(e.target.value as SortKey)} className={selectCls}>
                  <option value="az">Sort A–Z</option>
                  <option value="cost">Cost per unit</option>
                  <option value="most">Most used</option>
                  <option value="sups">Fewest suppliers</option>
                </select>
              </div>
              <div className="flex justify-between gap-2 text-sm text-ink-2">
                <span>
                  {shown.length} of {rows.length} · <em>italic</em> = estimated price
                </span>
                {filtered && (
                  <button
                    type="button"
                    className="underline"
                    onClick={() => {
                      setQ('')
                      setSup('all')
                      setPrice('all')
                      setUse('all')
                      go({ cat: 'all' })
                    }}
                  >
                    Clear filters
                  </button>
                )}
              </div>
            </div>
            <div className="min-h-0 flex-1 overflow-y-auto">
              {data.isLoading && <Loading what="Loading ingredients" />}
              {data.error && <ErrorBox error={data.error} what="ingredients" />}
              {shown.map((r) => (
                <button
                  key={r.ingredient_id}
                  type="button"
                  onClick={() => go({ id: r.ingredient_id })}
                  aria-current={r.ingredient_id === id ? 'true' : undefined}
                  className={cx(
                    'block w-full border-b border-line px-3.5 py-[9px] text-left',
                    r.ingredient_id === id ? 'bg-brand-wash' : 'hover:bg-canvas-2',
                  )}
                >
                  <span className="flex items-baseline justify-between gap-2">
                    <span className="min-w-0 truncate text-lg">{r.name}</span>
                    <span className={cx('fig whitespace-nowrap text-base', r.unit_cost.is_estimate && 'italic')}>
                      {r.unit_cost.pence === null ? 'no price' : `${unitPrice(r.unit_cost.pence)}/${unitWord(r.unit)}`}
                    </span>
                  </span>
                  <span className="block truncate text-sm text-ink-2">
                    {r.category ?? 'Other'} · {r.suppliers.length ? r.suppliers.map((s) => s.name).join(', ') : 'no supplier'} · in{' '}
                    {r.used_in_count} item{r.used_in_count === 1 ? '' : 's'}
                  </span>
                </button>
              ))}
            </div>
          </div>
        )}
        {showDetail && (
          <div className="flex min-h-0 min-w-0 flex-1 flex-col">
            {!docked && (
              <button type="button" className="flex-none px-4 pt-3 text-left text-base text-brand-ink" onClick={() => go({ id: null })}>
                ← All ingredients
              </button>
            )}
            {creating ? (
              <CreateIngredient
                categories={categories}
                defaultCategory={cat !== 'all' && cat !== 'est' ? cat : ''}
                onCreated={(newId) => go({ id: newId })}
              />
            ) : id !== null ? (
              <IngredientDetailPane key={id} id={id} categories={categories} onRetired={() => go({ id: null })} />
            ) : null}
          </div>
        )}
      </div>
    </>
  )
}
