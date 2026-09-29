/**
 * What we buy here (§3.1 products table).
 *
 * ★ = the price recipe costs use. Starring a link, or changing the pack or
 * price of the starred one, opens a new dated ingredient price and re-costs
 * every menu item that uses the ingredient (server side; the design's
 * `syncPreferred` edited the ingredient in place, which history forbids).
 * The response says how many items were re-costed; the section's own status
 * line (always mounted, under the table) says so, and says "Saving…" while a
 * blur-save is in flight, since the inputs give no other sign.
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
  IconButton,
  Input,
  MoneyInput,
  SearchInput,
  Select,
  StatusLine,
  THead,
  TBody,
  Table,
  Td,
  Th,
  Tr,
  cx,
} from '../../components/ui'
import type { ActiveFilterChip, Outcome } from '../../components/ui'
import { penceToPounds, poundsToPence } from '../../components/confirm/numbers'
import { parseDec } from '../../lib/dec'
import { useOperator } from '../../lib/operator'
import { KEYS, supplierWrites } from '../../lib/stock-api'
import type { ProductWriteOut, SupplierProduct, SupplierProductsResponse, Unit } from '../../lib/types/stock'
import { unitPrice, unitWord } from '../stock/fmt'
import { useWrite } from '../stock/writes'
import { UNITS, fromWrite } from './vocab'

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

type Report = (o: Outcome | null) => void

export function Products({ data }: { data: SupplierProductsResponse }) {
  const [filter, setFilter] = useState('')
  const [star, setStar] = useState<Star>('all')
  const [vs, setVs] = useState<Vs>('all')
  const [sort, setSort] = useState<Sort>('name')
  const [adding, setAdding] = useState(false)
  const [outcome, setOutcome] = useState<Outcome | null>(null)
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
            activeCount={chips.length}
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
      <div className="mt-2">
        <Table label="What we buy here" minWidth={820}>
          <THead>
            <tr>
              <Th width={40}>
                <span className="sr-only">Recipe price</span>
              </Th>
              <Th>Ingredient</Th>
              <Th>Their product / SKU</Th>
              <Th numeric width={76}>
                Pack
              </Th>
              <Th width={68}>Unit</Th>
              <Th numeric width={92}>
                Pack £
              </Th>
              <Th numeric width={104}>
                Per unit
              </Th>
              <Th>vs other suppliers</Th>
              <Th width={40}>
                <span className="sr-only">Unlink</span>
              </Th>
            </tr>
          </THead>
          <TBody>
            {rows.map((p) => (
              <ProductRow key={p.supplier_product_id} p={p} report={setOutcome} />
            ))}
            {rows.length === 0 && (
              <Tr>
                <Td colSpan={9} className="py-3 text-sm text-ink-2">
                  {data.products.length === 0 ? 'Nothing linked yet.' : 'No product matches these filters.'}
                </Td>
              </Tr>
            )}
            {adding && <NewLink data={data} onDone={() => setAdding(false)} report={setOutcome} />}
          </TBody>
        </Table>
      </div>
      <StatusLine outcome={outcome} className="mt-1.5" />
      {!adding && (
        <Button variant="add" size="sm" className="mt-2.5 rounded-card" onClick={() => setAdding(true)}>
          + Link an ingredient
        </Button>
      )}
    </section>
  )
}

/**
 * Cheaper or dearer than the best other supplier. Plain ink either way: a
 * dearer price is a comparison, not a crossed threshold, and the words say
 * which way it goes.
 */
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
    <span className="text-sm font-semibold text-ink">
      {Math.round(d)}% dearer than {vs.supplier_name}
    </span>
  )
}

function ProductRow({ p, report }: { p: SupplierProduct; report: Report }) {
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
    if (w.outcome?.tone === 'bad') report({ kind: 'error', text: `${p.ingredient_name}: ${w.outcome.text}` })
  }, [w.outcome, report, p.ingredient_name])

  const edit = async (body: { sku?: string; pack_size?: string; pack_unit?: Unit; price_pence?: number }) => {
    report({ kind: 'info', text: `Saving ${p.ingredient_name}…` })
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
    if (r) report({ kind: 'ok', text: savedText(r) })
  }

  const blurPack = () => {
    if (pack.trim() === p.pack_size) return
    const d = parseDec(pack)
    if (d === null || d.u <= 0n) {
      report({
        kind: 'error',
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
      report({
        kind: 'error',
        text: `${p.ingredient_name}: ${v.kind === 'bad' ? v.message : 'a pack price must be more than £0.'}`,
      })
      setPrice(penceToPounds(p.price_pence))
      return
    }
    void edit({ price_pence: v.value })
  }

  return (
    <Tr className="[&>td]:py-1">
      <Td>
        <IconButton
          label={
            p.is_preferred
              ? `${p.ingredient_name}: the price recipes use`
              : `Use this price for ${p.ingredient_name} in recipes`
          }
          aria-pressed={p.is_preferred}
          disabled={w.pending || p.is_preferred}
          onClick={async () => {
            report({ kind: 'info', text: `Saving ${p.ingredient_name}…` })
            const r = await w.run(() => supplierWrites.prefer(p.supplier_product_id, operator), {
              invalidate: afterProduct(p.supplier_id),
            })
            if (r) report({ kind: 'ok', text: savedText(r) })
          }}
          // The starred one is disabled (it cannot be un-starred here) but is not "off": keep it at full contrast.
          className={cx(p.is_preferred && 'text-brand-ink! disabled:opacity-100!')}
        >
          <span aria-hidden="true">{p.is_preferred ? '★' : '☆'}</span>
        </IconButton>
      </Td>
      <Td className="font-semibold">{p.ingredient_name}</Td>
      <Td>
        <Input
          size="xs"
          aria-label={`${p.ingredient_name} product code`}
          value={sku}
          onChange={(e) => setSku(e.target.value)}
          onBlur={() => sku !== p.sku && edit({ sku })}
          className={CTRL}
        />
      </Td>
      <Td>
        <Input
          size="xs"
          numeric
          aria-label={`${p.ingredient_name} pack size`}
          value={pack}
          onChange={(e) => setPack(e.target.value)}
          onBlur={blurPack}
          className={CTRL}
        />
      </Td>
      <Td>
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
      </Td>
      <Td>
        <MoneyInput
          size="xs"
          aria-label={`${p.ingredient_name} pack price`}
          value={price}
          onChange={(e) => setPrice(e.target.value)}
          onBlur={blurPrice}
          className={CTRL}
        />
      </Td>
      <Td numeric est={p.price_source === 'ESTIMATE'}>
        {p.unit_price_pence === null ? '—' : `${unitPrice(p.unit_price_pence)}/${unitWord(p.ingredient_unit)}`}
      </Td>
      <Td>
        <Compare p={p} />
      </Td>
      <Td>
        <IconButton
          label={armed ? `Tap again to unlink ${p.ingredient_name}` : `Unlink ${p.ingredient_name}`}
          aria-live="polite"
          disabled={w.pending}
          onClick={async () => {
            if (!armed) {
              setArmed(true)
              return
            }
            setArmed(false)
            report({ kind: 'info', text: `Unlinking ${p.ingredient_name}…` })
            const r = await w.run(() => supplierWrites.archiveProduct(p.supplier_product_id, operator), {
              invalidate: afterProduct(p.supplier_id),
            })
            if (r) report({ kind: 'ok', text: savedText(r) })
          }}
          className={cx(armed && 'font-bold text-alert!')}
        >
          <span aria-hidden="true">{armed ? '!' : '×'}</span>
        </IconButton>
      </Td>
    </Tr>
  )
}

function NewLink({
  data,
  onDone,
  report,
}: {
  data: SupplierProductsResponse
  onDone: () => void
  report: Report
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
      report({ kind: 'ok', text: savedText(r) })
      onDone()
    }
  }
  const local: Outcome | null = priceP.kind === 'bad' ? { kind: 'error', text: priceP.message } : fromWrite(w.outcome)
  return (
    <>
      <Tr className="[&>td]:py-1">
        <Td className="text-center text-ink-3" aria-hidden="true">
          ☆
        </Td>
        <Td>
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
        </Td>
        <Td>
          <Input
            size="xs"
            aria-label="Their product code"
            value={sku}
            onChange={(e) => setSku(e.target.value)}
            className={CTRL}
          />
        </Td>
        <Td>
          <Input
            size="xs"
            numeric
            aria-label="Pack size"
            value={pack}
            onChange={(e) => setPack(e.target.value)}
            className={CTRL}
          />
        </Td>
        <Td>
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
        </Td>
        <Td>
          <MoneyInput
            size="xs"
            aria-label="Pack price"
            value={price}
            onChange={(e) => setPrice(e.target.value)}
            className={CTRL}
          />
        </Td>
        <Td numeric className="text-sm text-ink-2">
          {opt ? `per ${unitWord(opt.unit)}` : ''}
        </Td>
        <Td colSpan={2} />
      </Tr>
      <Tr>
        <Td colSpan={9} className="py-2">
          <div className="flex flex-wrap items-center gap-2">
            <Button variant="primary" size="sm" disabled={!ok} pending={w.pending} pendingLabel="Linking…" onClick={submit}>
              Link
            </Button>
            <Button variant="ghost" size="sm" onClick={onDone}>
              Cancel
            </Button>
            <StatusLine outcome={local} className="min-w-0 flex-1" />
          </div>
        </Td>
      </Tr>
    </>
  )
}
