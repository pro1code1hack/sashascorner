/**
 * What we buy here (§3.1 products table).
 *
 * ★ = the price recipe costs use. Starring a link, or changing the pack or
 * price of the starred one, opens a new dated ingredient price and re-costs
 * every menu item that uses the ingredient (server side; the design's
 * `syncPreferred` edited the ingredient in place, which history forbids).
 * The response says how many items were re-costed, and the header shows it.
 *
 * Money is typed in pounds and converted exactly (`poundsToPence`); pack sizes
 * stay decimal strings. A £0 price is refused: unknown is not free (inv. 8).
 */
import { useEffect, useMemo, useState } from 'react'
import {
  ActiveFilters,
  Button,
  FilterBar,
  FilterSelect,
  Input,
  MoneyInput,
  SearchInput,
  Select,
  cx,
} from '../../components/ui'
import type { ActiveFilterChip } from '../../components/ui'
import { penceToPounds, poundsToPence } from '../../components/confirm/numbers'
import { parseDec } from '../../lib/dec'
import { useOperator } from '../../lib/operator'
import { KEYS, supplierWrites } from '../../lib/stock-api'
import type { ProductWriteOut, SupplierProduct, SupplierProductsResponse, Unit } from '../../lib/types/stock'
import { unitPrice, unitWord } from '../stock/fmt'
import { OutcomeLine, useWrite } from '../stock/writes'
import type { Saved } from './SuppliersScreen'
import { UNITS } from './vocab'

const GRID =
  'grid grid-cols-[28px_minmax(0,2.2fr)_minmax(0,1.3fr)_70px_64px_80px_100px_minmax(0,1.1fr)_20px] items-center gap-2'
const CTRL = 'h-7! rounded-control! border-line-strong! px-1.5! text-base'

function afterProduct(supplierId: number) {
  return [KEYS.supplierProducts(supplierId), KEYS.suppliers, KEYS.stock, KEYS.draft]
}

function savedText(r: ProductWriteOut): string {
  if (r.restarred) return `Saved. ${r.restarred} now takes its recipe price from another supplier.`
  if (r.recosted_items > 0)
    return `Saved. Recipe price updated: ${r.recosted_items} menu ${r.recosted_items === 1 ? 'item' : 'items'} re-costed.`
  return 'Saved'
}

type Star = 'all' | 'star' | 'nostar'
type Vs = 'all' | 'dearer' | 'cheaper' | 'only'
type Sort = 'name' | 'dearer' | 'pack' | 'stale'

const SORTS: ReadonlyArray<{ value: Sort; label: string }> = [
  { value: 'name', label: 'Sort: ingredient A–Z' },
  { value: 'dearer', label: 'Sort: dearest vs others first' },
  { value: 'pack', label: 'Sort: pack price, high first' },
  { value: 'stale', label: 'Sort: price oldest first' },
]

function vsOf(p: SupplierProduct): Vs {
  const v = p.vs_best_other
  if (v === null) return 'only'
  return v.diff_pct > 0.5 ? 'dearer' : 'cheaper'
}

export function Products({ data, onSaved }: { data: SupplierProductsResponse; onSaved: (s: Saved) => void }) {
  const [filter, setFilter] = useState('')
  const [star, setStar] = useState<Star>('all')
  const [vs, setVs] = useState<Vs>('all')
  const [sort, setSort] = useState<Sort>('name')
  const [adding, setAdding] = useState(false)
  const rows = useMemo(() => {
    const f = filter.trim().toLowerCase()
    const out = data.products.filter(
      (p) =>
        (f === '' || p.ingredient_name.toLowerCase().includes(f) || p.sku.toLowerCase().includes(f)) &&
        (star === 'all' || (star === 'star') === p.is_preferred) &&
        (vs === 'all' || vsOf(p) === vs),
    )
    const byName = (a: SupplierProduct, b: SupplierProduct) => a.ingredient_name.localeCompare(b.ingredient_name)
    const cmp: Record<Sort, (a: SupplierProduct, b: SupplierProduct) => number> = {
      name: byName,
      dearer: (a, b) =>
        (b.vs_best_other?.diff_pct ?? -Infinity) - (a.vs_best_other?.diff_pct ?? -Infinity) || byName(a, b),
      pack: (a, b) => b.price_pence - a.price_pence || byName(a, b),
      stale: (a, b) => (a.last_seen_price_at ?? '').localeCompare(b.last_seen_price_at ?? '') || byName(a, b),
    }
    return [...out].sort(cmp[sort])
  }, [data.products, filter, star, vs, sort])

  const count = (v: Vs) => data.products.filter((p) => vsOf(p) === v).length
  const chips: ActiveFilterChip[] = []
  if (filter.trim())
    chips.push({
      key: 'q',
      label: `“${filter.trim()}”`,
      onRemove: () => setFilter(''),
    })
  if (star !== 'all')
    chips.push({
      key: 'star',
      label: star === 'star' ? '★ recipe price' : 'Not the recipe price',
      onRemove: () => setStar('all'),
    })
  if (vs !== 'all')
    chips.push({
      key: 'vs',
      label: vs === 'dearer' ? 'Dearer than elsewhere' : vs === 'cheaper' ? 'Cheapest here' : 'Only supplier',
      onRemove: () => setVs('all'),
    })

  return (
    <section aria-label="What we buy here">
      <div className="mb-2 flex flex-wrap items-baseline gap-x-3 gap-y-1">
        <h2 className="text-lg font-extrabold">What we buy here · {data.products.length}</h2>
        <span className="text-sm text-ink-2">★ = the price used in recipe costs</span>
      </div>
      {data.products.length > 0 && (
        <>
          <FilterBar
            label="Filter products"
            search={
              <SearchInput
                label="Search products"
                placeholder="Ingredient or code"
                value={filter}
                onChange={(e) => setFilter(e.target.value)}
              />
            }
            trailing={
              <FilterSelect
                label="Sort products"
                value={sort}
                allValue={sort}
                onChange={(v) => setSort(v as Sort)}
                options={SORTS}
              />
            }
          >
            <FilterSelect
              label="Recipe price"
              value={star}
              onChange={(v) => setStar(v as Star)}
              options={[
                { value: 'all', label: 'Starred or not' },
                { value: 'star', label: '★ Recipe price' },
                { value: 'nostar', label: 'Not the recipe price' },
              ]}
            />
            <FilterSelect
              label="Compared with other suppliers"
              value={vs}
              onChange={(v) => setVs(v as Vs)}
              options={[
                { value: 'all', label: 'Any price vs others' },
                {
                  value: 'dearer',
                  label: `Dearer than elsewhere (${count('dearer')})`,
                },
                {
                  value: 'cheaper',
                  label: `Cheapest here (${count('cheaper')})`,
                },
                { value: 'only', label: `Only supplier (${count('only')})` },
              ]}
            />
          </FilterBar>
          <ActiveFilters
            className="mt-2"
            chips={chips}
            onClearAll={() => {
              setFilter('')
              setStar('all')
              setVs('all')
            }}
            summary={chips.length > 0 ? `${rows.length} of ${data.products.length}` : undefined}
          />
        </>
      )}
      <div className="scroll-x relative mt-2">
        <div className="min-w-[820px]">
          <div
            className={cx(GRID, 'border-b border-line py-1 text-label font-bold uppercase tracking-[.05em] text-ink-2')}
          >
            <span>
              <span className="sr-only">Preferred</span>
            </span>
            <span>Ingredient</span>
            <span>Their product / SKU</span>
            <span className="text-right">Pack</span>
            <span>Unit</span>
            <span className="text-right">Pack £</span>
            <span className="text-right">Per unit</span>
            <span>vs other suppliers</span>
            <span>
              <span className="sr-only">Remove</span>
            </span>
          </div>
          {rows.map((p) => (
            <ProductRow key={p.supplier_product_id} p={p} onSaved={onSaved} />
          ))}
          {rows.length === 0 && (
            <p className="py-3 text-sm text-ink-2">
              {data.products.length === 0 ? 'Nothing linked yet.' : 'No product matches these filters.'}
            </p>
          )}
          {adding && <NewLink data={data} onDone={() => setAdding(false)} onSaved={onSaved} />}
        </div>
      </div>
      {!adding && (
        <Button variant="add" size="sm" className="mt-2.5 rounded-card" onClick={() => setAdding(true)}>
          + Link an ingredient
        </Button>
      )}
    </section>
  )
}

function Compare({ p }: { p: SupplierProduct }) {
  const vs = p.vs_best_other
  if (vs === null) return <span className="text-sm text-ink-2">only supplier</span>
  const d = vs.diff_pct
  if (Math.abs(d) <= 0.5) return <span className="text-sm text-ink-2">same as {vs.supplier_name}</span>
  if (d < 0)
    return (
      <span className="text-sm text-ink">
        {Math.round(-d)}% cheaper than {vs.supplier_name}
      </span>
    )
  return (
    <span className="text-sm text-bad-ink">
      {Math.round(d)}% dearer than {vs.supplier_name}
    </span>
  )
}

function ProductRow({ p, onSaved }: { p: SupplierProduct; onSaved: (s: Saved) => void }) {
  const [operator] = useOperator()
  const [sku, setSku] = useState(p.sku)
  const [pack, setPack] = useState(p.pack_size)
  const [price, setPrice] = useState(penceToPounds(p.price_pence))
  const [armed, setArmed] = useState(false)
  const w = useWrite()
  useEffect(() => {
    setSku(p.sku)
    setPack(p.pack_size)
    setPrice(penceToPounds(p.price_pence))
  }, [p.sku, p.pack_size, p.price_pence])
  useEffect(() => {
    if (!armed) return
    const t = setTimeout(() => setArmed(false), 4000)
    return () => clearTimeout(t)
  }, [armed])
  useEffect(() => {
    if (w.outcome?.tone === 'bad') onSaved({ tone: 'bad', text: `${p.ingredient_name}: ${w.outcome.text}` })
  }, [w.outcome, onSaved, p.ingredient_name])

  const edit = async (body: { sku?: string; pack_size?: string; pack_unit?: Unit; price_pence?: number }) => {
    const r = await w.run(
      () =>
        supplierWrites.editProduct(p.supplier_product_id, {
          changed_by: operator,
          ...body,
        }),
      {
        invalidate: afterProduct(p.supplier_id),
      },
    )
    if (r) onSaved({ tone: 'ok', text: savedText(r) })
  }

  const blurPack = () => {
    if (pack.trim() === p.pack_size) return
    const d = parseDec(pack)
    if (d === null || d.u <= 0n) {
      onSaved({
        tone: 'bad',
        text: `${p.ingredient_name}: a pack size is a plain number above zero, like 4 or 2.5.`,
      })
      setPack(p.pack_size)
      return
    }
    void edit({ pack_size: pack.trim() })
  }
  const blurPrice = () => {
    if (price.trim() === penceToPounds(p.price_pence)) return
    const v = poundsToPence(price)
    if (v.kind !== 'value' || v.value <= 0) {
      onSaved({
        tone: 'bad',
        text: `${p.ingredient_name}: ${v.kind === 'bad' ? v.message : 'a pack price must be more than £0.'}`,
      })
      setPrice(penceToPounds(p.price_pence))
      return
    }
    void edit({ price_pence: v.value })
  }

  return (
    <div className={cx(GRID, 'border-b border-line py-1.5 text-base')}>
      <button
        type="button"
        aria-label={
          p.is_preferred
            ? `${p.ingredient_name}: the price recipes use`
            : `Use this price for ${p.ingredient_name} in recipes`
        }
        aria-pressed={p.is_preferred}
        disabled={w.pending || p.is_preferred}
        onClick={async () => {
          const r = await w.run(() => supplierWrites.prefer(p.supplier_product_id, operator), {
            invalidate: afterProduct(p.supplier_id),
          })
          if (r) onSaved({ tone: 'ok', text: savedText(r) })
        }}
        className={cx('text-[17px] leading-none', p.is_preferred ? 'text-alert' : 'text-ink-3 hover:text-ink-2')}
      >
        <span aria-hidden="true">{p.is_preferred ? '★' : '☆'}</span>
      </button>
      <span className="truncate" title={p.ingredient_name}>
        {p.ingredient_name}
      </span>
      <Input
        size="xs"
        aria-label={`${p.ingredient_name} product code`}
        value={sku}
        onChange={(e) => setSku(e.target.value)}
        onBlur={() => sku !== p.sku && edit({ sku })}
        className={CTRL}
      />
      <Input
        size="xs"
        numeric
        aria-label={`${p.ingredient_name} pack size`}
        value={pack}
        onChange={(e) => setPack(e.target.value)}
        onBlur={blurPack}
        className={CTRL}
      />
      <Select
        size="xs"
        aria-label={`${p.ingredient_name} pack unit`}
        value={p.pack_unit}
        onChange={(e) => edit({ pack_unit: e.target.value as Unit })}
        className={cx(CTRL, 'px-1!')}
      >
        {UNITS.map((u) => (
          <option key={u.value} value={u.value}>
            {u.label}
          </option>
        ))}
      </Select>
      <MoneyInput
        size="xs"
        aria-label={`${p.ingredient_name} pack price`}
        value={price}
        onChange={(e) => setPrice(e.target.value)}
        onBlur={blurPrice}
        className={CTRL}
      />
      <span className={cx('fig text-right', p.price_source === 'ESTIMATE' && 'italic')}>
        {p.unit_price_pence === null ? '—' : `${unitPrice(p.unit_price_pence)}/${unitWord(p.ingredient_unit)}`}
      </span>
      <Compare p={p} />
      <button
        type="button"
        aria-label={armed ? `Tap again to unlink ${p.ingredient_name}` : `Unlink ${p.ingredient_name}`}
        disabled={w.pending}
        onClick={async () => {
          if (!armed) {
            setArmed(true)
            return
          }
          setArmed(false)
          const r = await w.run(() => supplierWrites.archiveProduct(p.supplier_product_id, operator), {
            invalidate: afterProduct(p.supplier_id),
          })
          if (r) onSaved({ tone: 'ok', text: savedText(r) })
        }}
        className={cx('text-center', armed ? 'font-bold text-alert' : 'text-ink-3 hover:text-ink-2')}
        title={armed ? 'Tap again to unlink' : 'Unlink'}
      >
        <span aria-hidden="true">{armed ? '!' : '×'}</span>
      </button>
    </div>
  )
}

function NewLink({
  data,
  onDone,
  onSaved,
}: {
  data: SupplierProductsResponse
  onDone: () => void
  onSaved: (s: Saved) => void
}) {
  const [operator] = useOperator()
  const [ing, setIng] = useState<number | null>(null)
  const [sku, setSku] = useState('')
  const [pack, setPack] = useState('1')
  const [unit, setUnit] = useState<Unit>('EACH')
  const [price, setPrice] = useState('')
  const w = useWrite()
  const opt = data.ingredients.find((i) => i.ingredient_id === ing) ?? null
  const packOk = (() => {
    const d = parseDec(pack)
    return d !== null && d.u > 0n
  })()
  const priceP = poundsToPence(price)
  const ok = ing !== null && packOk && priceP.kind === 'value' && priceP.value > 0
  const submit = async () => {
    if (!ok || ing === null || priceP.kind !== 'value') return
    const r = await w.run(
      () =>
        supplierWrites.link(data.supplier.supplier_id, {
          ingredient_id: ing,
          sku: sku.trim(),
          pack_size: pack.trim(),
          pack_unit: unit,
          price_pence: priceP.value,
          changed_by: operator,
        }),
      { invalidate: afterProduct(data.supplier.supplier_id) },
    )
    if (r) {
      onSaved({ tone: 'ok', text: savedText(r) })
      onDone()
    }
  }
  return (
    <div className="border-b border-line py-1.5">
      <div className={cx(GRID, 'text-base')}>
        <span className="text-center text-ink-3" aria-hidden="true">
          ☆
        </span>
        <Select
          size="xs"
          aria-label="Ingredient"
          value={ing === null ? '' : String(ing)}
          onChange={(e) => {
            const id = e.target.value === '' ? null : Number(e.target.value)
            setIng(id)
            const o = data.ingredients.find((i) => i.ingredient_id === id)
            if (o) setUnit(o.unit)
          }}
          className={CTRL}
        >
          <option value="">— pick ingredient —</option>
          {data.ingredients.map((i) => (
            <option key={i.ingredient_id} value={i.ingredient_id}>
              {i.name}
            </option>
          ))}
        </Select>
        <Input
          size="xs"
          aria-label="Their product code"
          value={sku}
          onChange={(e) => setSku(e.target.value)}
          className={CTRL}
        />
        <Input
          size="xs"
          numeric
          aria-label="Pack size"
          value={pack}
          onChange={(e) => setPack(e.target.value)}
          className={CTRL}
        />
        <Select
          size="xs"
          aria-label="Pack unit"
          value={unit}
          onChange={(e) => setUnit(e.target.value as Unit)}
          className={cx(CTRL, 'px-1!')}
        >
          {UNITS.map((u) => (
            <option key={u.value} value={u.value}>
              {u.label}
            </option>
          ))}
        </Select>
        <MoneyInput
          size="xs"
          aria-label="Pack price"
          value={price}
          onChange={(e) => setPrice(e.target.value)}
          className={CTRL}
        />
        <span className="text-right text-sm text-ink-2">{opt ? `per ${unitWord(opt.unit)}` : ''}</span>
        <span />
        <span />
      </div>
      <div className="mt-2 flex items-center gap-2">
        <Button variant="primary" size="sm" disabled={!ok} pending={w.pending} pendingLabel="Linking…" onClick={submit}>
          Link
        </Button>
        <Button variant="ghost" size="sm" onClick={onDone}>
          Cancel
        </Button>
        {priceP.kind === 'bad' && <span className="text-sm text-bad-ink">{priceP.message}</span>}
      </div>
      <OutcomeLine outcome={w.outcome} className="mt-1" />
    </div>
  )
}
