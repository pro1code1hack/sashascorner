/**
 * "What to buy": the shopping list, as one spreadsheet-like table.
 *
 * Every number is the ordering run's own (`GET /api/orders/draft`, which
 * computes a full run through services/build_order.py and WRITES NOTHING). This
 * screen only flattens the per-supplier drafts into rows, filters, sorts and
 * adds up integer pence for the rows shown. No forecast, cover window, cap or
 * pack count is worked out here: a second copy of that maths would be a list
 * that disagrees with the order you are asked to confirm.
 *
 * Invariant 1: nothing is ordered from this page. Orders are created and
 * confirmed by a person on the Orders page. Invariant 4: a capped line says why, on the line. Invariant 9: a
 * withheld forecast shows its reason where the number would be.
 */
import { useMemo, useState } from 'react'
import type { ReactNode } from 'react'
import { useQuery } from '@tanstack/react-query'
import {
  ActiveFilters,
  Button,
  Empty,
  ErrorBox,
  FilterBar,
  FilterSelect,
  FilterToggle,
  InfoPanel,
  Loading,
  SearchInput,
  TBody,
  THead,
  Table,
  Td,
  Th,
  TotalRow,
  Tr,
  LinkButton,
  cx,
} from '../../components/ui'
import { gbp, plural } from '../../lib/format'
import { href } from '../../lib/router'
import { KEYS, stockApi } from '../../lib/stock-api'
import type { Forecast } from '../../lib/types'
import type { DraftOrdersResponse, StockRow, SupplierOrder } from '../../lib/types/stock'
import { dayMonth, fmtD, humanQty, packEquiv } from './fmt'

type SortKey = 'supplier' | 'name' | 'category' | 'packs' | 'cost'

interface BuyRow {
  key: string
  kind: 'line' | 'skipped'
  ingredientId: number
  name: string
  category: string
  supplierId: number
  supplierName: string
  placeholder: boolean
  unit: string
  onHand: string | null
  need: string | null
  forecast: Forecast | null
  coverDays: number | null
  fullCoverDays: number | null
  packs: number | null
  packSize: string | null
  packUnit: string | null
  packPrice: number | null
  lineTotal: number | null
  cap: string | null
  capDetail: string | null
  topUp: boolean
  note: string | null
}

const CAP_WORD: Record<string, string> = {
  SHELF_LIFE: 'Shelf life',
  SEASON_END: 'Season ends',
  OUT_OF_SEASON: 'Out of season',
}

function clean(text: string | null | undefined): string | null {
  return text ? text.replace(/ -- /g, ' — ') : null
}

function flatten(draft: DraftOrdersResponse, cats: Map<number, string>): BuyRow[] {
  const out: BuyRow[] = []
  for (const o of draft.suppliers) {
    const s = o.supplier
    for (const l of o.lines) {
      out.push({
        key: `l-${s.supplier_id}-${l.ingredient_id}`,
        kind: 'line',
        ingredientId: l.ingredient_id,
        name: l.ingredient_name,
        category: cats.get(l.ingredient_id) ?? 'Uncategorised',
        supplierId: s.supplier_id,
        supplierName: s.name,
        placeholder: s.terms_are_placeholders,
        unit: l.unit,
        onHand: l.on_hand_qty,
        need: l.need_qty === 'withheld' ? null : l.need_qty,
        forecast: l.forecast,
        coverDays: l.cover_days,
        fullCoverDays: l.full_cover_days,
        packs: l.packs,
        packSize: l.pack_size,
        packUnit: l.pack_unit,
        packPrice: l.pack_price_pence,
        lineTotal: l.line_total_pence,
        cap: l.is_capped ? (CAP_WORD[l.cap_kind ?? ''] ?? 'Capped') : null,
        capDetail: clean(l.cap_reason ?? l.cap_detail),
        topUp: l.is_top_up,
        note: clean(l.clamped),
      })
    }
    for (const sk of o.skipped) {
      out.push({
        key: `s-${s.supplier_id}-${sk.ingredient_id}`,
        kind: 'skipped',
        ingredientId: sk.ingredient_id,
        name: sk.ingredient_name,
        category: cats.get(sk.ingredient_id) ?? 'Uncategorised',
        supplierId: s.supplier_id,
        supplierName: s.name,
        placeholder: s.terms_are_placeholders,
        unit: '',
        onHand: null,
        need: null,
        forecast: null,
        coverDays: null,
        fullCoverDays: null,
        packs: null,
        packSize: null,
        packUnit: null,
        packPrice: null,
        lineTotal: null,
        cap: sk.out_of_season ? 'Out of season' : sk.is_capped ? 'Capped' : sk.below_par_floor ? 'Below reorder point' : null,
        capDetail: clean(sk.cap_reason),
        topUp: false,
        note: clean(sk.reason.split(' | ').map((r) => r.replace(`${sk.ingredient_name}: `, '')).join(' · ')),
      })
    }
  }
  return out
}

function compare(k: SortKey, a: BuyRow, b: BuyRow): number {
  switch (k) {
    case 'supplier':
      return a.supplierName.localeCompare(b.supplierName)
    case 'name':
      return a.name.localeCompare(b.name)
    case 'category':
      return a.category.localeCompare(b.category)
    case 'packs':
      return (a.packs ?? -1) - (b.packs ?? -1)
    case 'cost':
      return (a.lineTotal ?? -1) - (b.lineTotal ?? -1)
  }
}

export function BuyList({ stockRows }: { stockRows: StockRow[] }) {
  const q = useQuery({ queryKey: KEYS.draft, queryFn: stockApi.draft, staleTime: 60_000 })
  const [search, setSearch] = useState('')
  const [supplier, setSupplier] = useState('all')
  const [category, setCategory] = useState('all')
  const [showSkipped, setShowSkipped] = useState(false)
  const [sort, setSort] = useState<{ key: SortKey; desc: boolean }>({ key: 'supplier', desc: false })

  const byId = useMemo(() => new Map(stockRows.map((r) => [r.ingredient_id, r])), [stockRows])
  const cats = useMemo(
    () => new Map(stockRows.map((r) => [r.ingredient_id, r.category ?? 'Uncategorised'])),
    [stockRows],
  )
  const all = useMemo(() => (q.data ? flatten(q.data, cats) : []), [q.data, cats])

  const lines = all.filter((r) => r.kind === 'line')
  const skippedCount = all.length - lines.length
  const supplierOptions = useMemo(() => {
    const m = new Map<number, string>()
    for (const r of all) m.set(r.supplierId, r.supplierName)
    return [...m.entries()].sort((a, b) => a[1].localeCompare(b[1]))
  }, [all])
  const categoryOptions = useMemo(() => [...new Set(all.map((r) => r.category))].sort(), [all])

  const shown = useMemo(() => {
    const needle = search.trim().toLowerCase()
    return all
      .filter((r) => showSkipped || r.kind === 'line')
      .filter((r) => supplier === 'all' || String(r.supplierId) === supplier)
      .filter((r) => category === 'all' || r.category === category)
      .filter((r) => needle === '' || r.name.toLowerCase().includes(needle))
      .sort((a, b) => {
        const c = compare(sort.key, a, b) * (sort.desc ? -1 : 1)
        if (c !== 0) return c
        if (a.kind !== b.kind) return a.kind === 'line' ? -1 : 1
        return a.supplierName.localeCompare(b.supplierName) || a.name.localeCompare(b.name)
      })
  }, [all, showSkipped, supplier, category, search, sort])

  const shownLines = shown.filter((r) => r.kind === 'line')
  // Invariant 8: an unpriced line is left out of the total and the total says so,
  // rather than counting it as £0.
  const shownTotal = shownLines.reduce((a, r) => a + (r.lineTotal ?? 0), 0)
  const unpriced = shownLines.filter((r) => r.lineTotal === null).length
  const clearAll = () => {
    setSupplier('all')
    setCategory('all')
    setSearch('')
  }

  if (q.isPending) return <Loading what="Working out what to buy" />
  if (q.isError) return <ErrorBox error={q.error} what="the shopping list" />
  const draft = q.data

  const chips = [
    ...(supplier !== 'all'
      ? [
          {
            key: 'sup',
            label: `Supplier: ${supplierOptions.find(([id]) => String(id) === supplier)?.[1] ?? supplier}`,
            onRemove: () => setSupplier('all'),
          },
        ]
      : []),
    ...(category !== 'all' ? [{ key: 'cat', label: `Category: ${category}`, onRemove: () => setCategory('all') }] : []),
    ...(search.trim() !== '' ? [{ key: 'q', label: `“${search.trim()}”`, onRemove: () => setSearch('') }] : []),
  ]

  const sortHead = (key: SortKey, label: string, numeric = false) => {
    const on = sort.key === key
    return (
      <Th numeric={numeric} aria-sort={on ? (sort.desc ? 'descending' : 'ascending') : 'none'}>
        <button
          type="button"
          onClick={() => setSort(on ? { key, desc: !sort.desc } : { key, desc: key === 'cost' || key === 'packs' })}
          className={cx('inline-flex items-center gap-1 uppercase hover:text-ink', on && 'text-ink')}
        >
          {label}
          <span aria-hidden="true" className={on ? '' : 'opacity-0'}>
            {sort.desc ? '↓' : '↑'}
          </span>
        </button>
      </Th>
    )
  }

  return (
    <div className="flex min-h-0 min-w-0 flex-1 flex-col overflow-y-auto">
      <div className="flex flex-none flex-col gap-2.5 border-b border-line-soft px-4 py-3.5 sm:px-5">
        <FilterBar
          activeCount={chips.length}
          label="Filter the shopping list"
          search={
            <SearchInput
              label="Search ingredients"
              placeholder="Search ingredients"
              value={search}
              onChange={(e) => setSearch(e.target.value)}
            />
          }
          trailing={
            <FilterToggle active={showSkipped} onToggle={() => setShowSkipped((v) => !v)} count={skippedCount}>
              Show not bought
            </FilterToggle>
          }
        >
          <FilterSelect
            label="Supplier"
            value={supplier}
            onChange={setSupplier}
            options={[
              { value: 'all', label: 'All suppliers' },
              ...supplierOptions.map(([id, name]) => ({ value: String(id), label: name })),
            ]}
          />
          <FilterSelect
            label="Category"
            value={category}
            onChange={setCategory}
            options={[{ value: 'all', label: 'All categories' }, ...categoryOptions.map((c) => ({ value: c, label: c }))]}
          />
        </FilterBar>
        <ActiveFilters
          chips={chips}
          onClearAll={clearAll}
          summary={`${shownLines.length} of ${lines.length} ${plural(lines.length, 'line')} to buy`}
        />
      </div>

      <div className="flex flex-none flex-col gap-3 px-4 pt-3.5 sm:px-5">
        <InfoPanel>
          A list, not an order. Worked out {fmtD(draft.order_date)} by the same run that drafts the
          orders; nothing is ordered from here — orders are created and confirmed on the Orders page. On-hand figures are
          estimates (<em>italic</em>).
        </InfoPanel>
        <Leftovers draft={draft} />
      </div>

      <div className="flex-none px-4 py-3.5 sm:px-5">
        {shown.length === 0 ? (
          <Empty
            action={
              lines.length === 0 ? (
                <LinkButton href={href('/orders/drafts')}>See draft orders</LinkButton>
              ) : (
                <Button onClick={clearAll}>Clear filters</Button>
              )
            }
          >
            {lines.length === 0 ? 'Nothing needs buying right now.' : 'Nothing in this view.'}
          </Empty>
        ) : (
          // No sticky header: the table scrolls sideways inside its own box, so a
          // sticky <th> could never stick to the page scroll anyway.
          <Table label="What to buy" minWidth={980}>
            <THead>
              <tr>
                {sortHead('name', 'Ingredient')}
                {sortHead('supplier', 'Supplier')}
                <Th numeric>On hand (est.)</Th>
                <Th numeric>Use over cover</Th>
                <Th numeric>Short by</Th>
                {sortHead('packs', 'Buy', true)}
                <Th numeric>Pack price</Th>
                {sortHead('cost', 'Line cost', true)}
                <Th>Why this much</Th>
              </tr>
            </THead>
            <TBody>
              {shown.map((r) => (
                <BuyTr key={r.key} r={r} stock={byId.get(r.ingredientId)} />
              ))}
              <TotalRow>
                <Td colSpan={7}>
                  {shownLines.length === lines.length ? 'All lines' : `${shownLines.length} lines shown`}
                  <span className="ml-2 text-sm font-normal text-ink-2">
                    before delivery fees
                    {unpriced > 0 && `; ${unpriced} unpriced ${plural(unpriced, 'line')} not included`}
                  </span>
                </Td>
                <Td numeric>{gbp(shownTotal)}</Td>
                <Td />
              </TotalRow>
            </TBody>
          </Table>
        )}
      </div>

      <SupplierTotals orders={draft.suppliers} only={supplier} />
      {draft.emergency.length > 0 && <Emergency draft={draft} />}
    </div>
  )
}

function BuyTr({ r, stock }: { r: BuyRow; stock: StockRow | undefined }) {
  if (r.kind === 'skipped') {
    return (
      <Tr className="text-ink-2">
        <Td>
          <NameCell name={r.name} sub={r.category} />
        </Td>
        <Td secondary>{r.supplierName}</Td>
        <Td colSpan={6} className="text-sm">
          <span className="font-semibold text-ink">Not bought</span>
          {r.cap && <span> · {r.cap}</span>}
        </Td>
        <Td className="max-w-[320px] text-sm">{r.note}</Td>
      </Tr>
    )
  }
  const packWord = r.packSize && r.packUnit ? `${humanQty(r.packSize, r.packUnit)}${r.packUnit === 'EACH' ? ' pcs' : ''}` : ''
  const pack = stock?.pack ?? null
  const eq = r.need !== null ? packEquiv(r.need, pack, r.category) : null
  const lowConf = r.forecast?.is_low_confidence ?? false
  return (
    <Tr>
      <Td>
        <NameCell name={r.name} sub={r.category} />
      </Td>
      <Td className="whitespace-nowrap">
        {r.supplierName}
        {r.placeholder && (
          <span className="block text-xs text-ink-2" title="Lead time, delivery days and minimum were never confirmed with this supplier.">
            terms not confirmed
          </span>
        )}
      </Td>
      <Td numeric est>
        {r.onHand === null ? '—' : `~${humanQty(r.onHand, r.unit)}`}
      </Td>
      <Td numeric className={lowConf ? 'whitespace-normal text-left text-sm text-ink-2' : undefined}>
        {lowConf ? (
          // Invariant 9: the reason in place of the number.
          <span className="block max-w-[200px]">{r.forecast?.reasons.join(' ') || 'not enough history to say'}</span>
        ) : (
          <>
            <span className="italic">{humanQty(r.forecast?.qty ?? null, r.unit)}</span>
            {r.coverDays !== null && <span className="block text-xs text-ink-2">{r.coverDays} days</span>}
          </>
        )}
      </Td>
      <Td numeric>
        {r.need === null ? (
          // The forecast was held back (reason in the column before), so no shortfall either.
          <span className="text-sm text-ink-2">not worked out</span>
        ) : (
          <>
            <span className="italic">{humanQty(r.need, r.unit)}</span>
            {eq && (
              <span className="block text-xs italic text-ink-2" title={eq.title}>
                {eq.text}
              </span>
            )}
          </>
        )}
      </Td>
      <Td numeric strong>
        {r.packs}
        <span className="font-normal text-ink-2"> × {packWord}</span>
      </Td>
      <Td numeric>{r.packPrice === null ? '—' : gbp(r.packPrice)}</Td>
      <Td numeric strong>
        {r.lineTotal === null ? '—' : gbp(r.lineTotal)}
      </Td>
      <Td className="max-w-[300px] text-sm">
        <Why r={r} />
      </Td>
    </Tr>
  )
}

function Why({ r }: { r: BuyRow }) {
  const parts: ReactNode[] = []
  if (r.cap) {
    parts.push(
      <span key="cap" className="font-semibold text-bad-ink">
        {r.cap}
      </span>,
    )
  }
  if (r.topUp) parts.push(<span key="top">top-up to reach the minimum</span>)
  if (r.note) parts.push(<span key="n">{r.note}</span>)
  if (parts.length === 0) return <span className="text-ink-3">covers {r.fullCoverDays ?? r.coverDays} days</span>
  return (
    <span className="block">
      {parts.map((p, i) => (
        <span key={i}>
          {i > 0 && ' · '}
          {p}
        </span>
      ))}
      {r.capDetail && <span className="block text-ink-2">{r.capDetail}</span>}
    </span>
  )
}

function NameCell({ name, sub }: { name: string; sub: string }) {
  return (
    <div className="min-w-0">
      <div className="truncate font-semibold">{name}</div>
      <div className="truncate text-xs text-ink-2">{sub}</div>
    </div>
  )
}

/** Per-supplier totals, straight from the run: subtotal, fee, total, minimum. */
function SupplierTotals({ orders, only }: { orders: SupplierOrder[]; only: string }) {
  const rows = orders.filter(
    (o) => (o.lines.length > 0 || o.skipped.length > 0) && (only === 'all' || String(o.supplier.supplier_id) === only),
  )
  if (rows.length === 0) return null
  const grand = rows.reduce((a, o) => a + o.total_pence, 0)
  return (
    <section aria-label="Totals by supplier" className="flex-none px-4 pb-4 sm:px-5">
      <h2 className="pb-1.5 text-label font-bold uppercase tracking-[.08em] text-ink-3">By supplier</h2>
      <Table label="Totals by supplier" minWidth={720}>
        <THead>
          <tr>
            <Th>Supplier</Th>
            <Th numeric>Lines</Th>
            <Th numeric>Goods</Th>
            <Th numeric>Delivery</Th>
            <Th numeric>Total</Th>
            <Th>Minimum</Th>
            <Th>Delivery day</Th>
          </tr>
        </THead>
        <TBody>
          {rows.map((o) => {
            const short = o.supplier.min_order_pence - o.subtotal_pence
            return (
              <Tr key={o.supplier.supplier_id}>
                <Td>
                  <span className="font-semibold">{o.supplier.name}</span>
                  {o.supplier.terms_are_placeholders && (
                    <span className="ml-1.5 text-xs text-ink-2">terms not confirmed</span>
                  )}
                </Td>
                <Td numeric>{o.lines.length}</Td>
                <Td numeric>{gbp(o.subtotal_pence)}</Td>
                <Td numeric>{o.fee_applies === false ? '—' : gbp(o.delivery_fee_pence)}</Td>
                <Td numeric strong>
                  {gbp(o.total_pence)}
                </Td>
                <Td className={cx('text-sm', !o.meets_minimum && o.lines.length > 0 && 'text-bad-ink')}>
                  {o.lines.length === 0
                    ? '—'
                    : o.meets_minimum
                      ? 'met'
                      : `${gbp(short > 0 ? short : 0)} short of ${gbp(o.supplier.min_order_pence)}`}
                </Td>
                <Td className="whitespace-nowrap text-sm text-ink-2">
                  {o.lines.length === 0 ? '—' : dayMonth(o.target_delivery_date)}
                  {o.order_by_date && o.lines.length > 0 && <span> · order by {dayMonth(o.order_by_date)}</span>}
                </Td>
              </Tr>
            )
          })}
          <TotalRow>
            <Td>All suppliers</Td>
            <Td numeric>{rows.reduce((a, o) => a + o.lines.length, 0)}</Td>
            <Td numeric>{gbp(rows.reduce((a, o) => a + o.subtotal_pence, 0))}</Td>
            <Td numeric>{gbp(rows.reduce((a, o) => a + (o.fee_applies === false ? 0 : o.delivery_fee_pence), 0))}</Td>
            <Td numeric>{gbp(grand)}</Td>
            <Td colSpan={2} />
          </TotalRow>
        </TBody>
      </Table>
    </section>
  )
}

/** Ingredients the run could not put on any list, and why. Said, not hidden. */
function Leftovers({ draft }: { draft: DraftOrdersResponse }) {
  const uncounted = draft.uncounted ?? []
  const unsourced = draft.unsourced ?? []
  if (uncounted.length === 0 && unsourced.length === 0) return null
  return (
    <div className="flex flex-col gap-1 text-sm text-ink-2">
      {uncounted.length > 0 && (
        <p>
          <span className="font-semibold text-ink">Not on this list — never counted ({uncounted.length}):</span>{' '}
          {uncounted.map((u) => u.name).join(', ')}. Count them and they can be sized.
        </p>
      )}
      {unsourced.length > 0 && (
        <p>
          <span className="font-semibold text-ink">No supplier linked ({unsourced.length}):</span>{' '}
          {unsourced.map((u) => u.name).join(', ')}.
        </p>
      )}
    </div>
  )
}

function Emergency({ draft }: { draft: DraftOrdersResponse }) {
  return (
    <section aria-label="Can't wait for a delivery" className="flex-none px-4 pb-5 sm:px-5">
      <h2 className="pb-1.5 text-label font-bold uppercase tracking-[.08em] text-ink-3">
        Can&rsquo;t wait for a delivery — shop run
      </h2>
      <Table label="Shop run" minWidth={640}>
        <THead>
          <tr>
            <Th>Ingredient</Th>
            <Th numeric>Qty</Th>
            <Th numeric>Retail premium</Th>
            <Th>Why</Th>
          </tr>
        </THead>
        <TBody>
          {draft.emergency.map((e) => (
            <Tr key={e.ingredient_id}>
              <Td className="font-semibold">{e.ingredient_name}</Td>
              <Td numeric>{humanQty(e.qty, e.unit)}</Td>
              <Td numeric>{e.premium_pence === null ? 'unknown' : gbp(e.premium_pence)}</Td>
              <Td className="max-w-[360px] text-sm text-ink-2">
                <span className="line-clamp-2">{clean(e.reason)}</span>
              </Td>
            </Tr>
          ))}
        </TBody>
      </Table>
    </section>
  )
}
