/**
 * One ingredient (spec §V3.3), with the invariant-8 resolutions from spec C-1,
 * C-5, C-8 and C-9:
 *
 * - "Price is an estimate" is an indicator, not a checkbox. It clears only when
 *   a price is recorded from an invoice or a supplier price list; any pack or
 *   cost edit asks where the price is from, previews the menu impact, and
 *   records a dated price from today.
 * - The unit is read-only once anything records a quantity in it; pack units
 *   offer only units of the same kind (a kg pack of milk is refused).
 * - "Delete" is Retire, refused with the reason while a live recipe uses it.
 * - Supplier links are shown, not edited here (the Suppliers screen owns them).
 */
import { useEffect, useState } from 'react'
import {
  Button,
  Checkbox,
  ConfirmTwiceButton,
  ErrorBox,
  Input,
  Loading,
  Segmented,
  Select,
  cx,
} from '../../components/ui'
import { OperatorNeeded } from '../../components/shell/Operator'
import { ingredientApi, useIngredient, useInvalidateMenu } from '../../lib/menu-api'
import { useOperator } from '../../lib/operator'
import { href } from '../../lib/router'
import type { IngredientDetail, IngredientPricePreview, PriceSource, Unit } from '../../lib/types/menu'
import {
  MONEY_INPUT,
  QTY_INPUT,
  compatibleUnits,
  dayLabel,
  gbp,
  penceToPounds,
  poundsToPence,
  qtyOut,
  qtyText,
  sameQty,
  unitPrice,
  unitWord,
} from '../menu/common/figures'
import { ImpactPanel, impactFigures } from '../menu/common/Impact'
import { usePreview } from '../menu/common/usePreview'

const UNITS: Unit[] = ['L', 'ML', 'KG', 'G', 'EACH']

export function IngredientDetailPane({
  id,
  categories,
  onRetired,
}: {
  id: number
  categories: string[]
  onRetired: () => void
}) {
  const q = useIngredient(id)
  if (q.isLoading) return <Loading what="Loading the ingredient" />
  if (q.error) return <ErrorBox error={q.error} what="this ingredient" />
  if (!q.data) return null
  return <DetailBody key={`${id}-${q.data.row.pack?.effective_from ?? ''}`} d={q.data} categories={categories} onRetired={onRetired} />
}

function DetailBody({ d, categories, onRetired }: { d: IngredientDetail; categories: string[]; onRetired: () => void }) {
  const row = d.row
  const [operator] = useOperator()
  const invalidate = useInvalidateMenu()
  const [name, setName] = useState(row.name)
  const [cat, setCat] = useState(row.category ?? '')
  const [note, setNote] = useState(row.note ?? '')
  const [unit, setUnit] = useState<Unit>(row.unit)
  const [metaMsg, setMetaMsg] = useState<string | null>(null)
  const [saving, setSaving] = useState(false)
  useEffect(() => {
    setName(row.name)
    setCat(row.category ?? '')
    setNote(row.note ?? '')
    setUnit(row.unit)
  }, [row])

  const metaDirty = name.trim() !== row.name || cat !== (row.category ?? '') || note !== (row.note ?? '') || unit !== row.unit
  const unitLocked = Object.keys(d.unit_locked_by).length > 0

  const saveMeta = async () => {
    if (!operator) return
    setSaving(true)
    const body: Record<string, unknown> = { actor: operator }
    if (name.trim() !== row.name) body.name = name.trim()
    if (cat !== (row.category ?? '')) body.category = cat || null
    if (note !== (row.note ?? '')) body.note = note || null
    if (unit !== row.unit) body.unit = unit
    const r = await ingredientApi.meta(row.ingredient_id, body)
    setSaving(false)
    if (r.kind === 'ok') {
      setMetaMsg(null)
      await invalidate()
    } else setMetaMsg(r.message)
  }

  const cats = [...new Set([...categories, row.category ?? ''].filter(Boolean))].sort((a, b) => a.localeCompare(b))
  const lockedText = Object.entries(d.unit_locked_by)
    .map(([k, v]) => `${v} ${k}`)
    .join(', ')

  return (
    <div className="min-h-0 flex-1 overflow-y-auto px-4 py-[18px] sm:px-[22px]">
      <div className="flex items-center gap-3">
        <input
          aria-label="Ingredient name"
          value={name}
          onChange={(e) => setName(e.target.value)}
          className="min-w-0 flex-1 border-b border-line bg-transparent pb-1 text-3xl font-extrabold tracking-[-.01em] outline-none focus-visible:border-brand"
        />
        <ConfirmTwiceButton
          variant="danger"
          armedLabel="Tap again to retire"
          disabled={operator === null}
          onConfirm={async () => {
            if (!operator) return
            const r = await ingredientApi.retire(row.ingredient_id, operator)
            if (r.kind === 'ok') {
              await invalidate()
              onRetired()
            } else setMetaMsg(r.message)
          }}
        >
          Retire
        </ConfirmTwiceButton>
      </div>
      {Object.keys(d.retire_blocked_by).length > 0 && (
        <p className="mt-1 text-sm text-ink-2">
          Cannot be retired while{' '}
          {Object.entries(d.retire_blocked_by)
            .map(([k, v]) => `${v} ${k}`)
            .join(', ')}{' '}
          use it. Nothing is ever deleted; retired ingredients keep their history.
        </p>
      )}

      <div className="my-4 grid grid-cols-[repeat(auto-fit,minmax(150px,1fr))] gap-3.5">
        <label className="flex min-w-0 flex-col gap-1 text-base text-ink-2">
          Category
          <Select value={cat} onChange={(e) => setCat(e.target.value)} className="text-md text-ink">
            <option value="">No category</option>
            {cats.map((c) => (
              <option key={c} value={c}>
                {c}
              </option>
            ))}
          </Select>
        </label>
        <label className="flex min-w-0 flex-col gap-1 text-base text-ink-2">
          Costed per
          <Select
            value={unit}
            disabled={unitLocked}
            onChange={(e) => setUnit(e.target.value as Unit)}
            className="text-md text-ink"
            title={unitLocked ? `Fixed: ${lockedText} record quantities in ${unitWord(row.unit)}` : undefined}
          >
            {UNITS.map((u) => (
              <option key={u} value={u}>
                {unitWord(u)}
              </option>
            ))}
          </Select>
        </label>
      </div>

      <PriceEditor d={d} />

      <label className="mb-4 flex flex-col gap-1 text-base text-ink-2">
        Notes
        <Input value={note} onChange={(e) => setNote(e.target.value)} className="text-ink" />
      </label>
      {metaDirty && (
        <div className="mb-4 flex flex-wrap items-center gap-2">
          <Button variant="primary" size="sm" pending={saving} pendingLabel="Saving…" disabled={operator === null} onClick={saveMeta}>
            Save name, category and notes
          </Button>
          <Button
            variant="ghost"
            size="sm"
            onClick={() => {
              setName(row.name)
              setCat(row.category ?? '')
              setNote(row.note ?? '')
              setUnit(row.unit)
            }}
          >
            Undo
          </Button>
          <OperatorNeeded what="save this ingredient" />
        </div>
      )}
      {metaMsg && (
        <p role="alert" className="mb-3 text-sm text-bad-ink">
          {metaMsg}
        </p>
      )}

      <section className="mb-4" aria-labelledby="ing-buy">
        <div className="flex flex-wrap items-baseline gap-x-2.5">
          <h3 id="ing-buy" className="text-lg font-extrabold">
            Where to buy
          </h3>
          <span className="text-sm text-ink-2">
            Recipe cost uses the price recorded above
            {d.preferred_supplier ? `; ★ ${d.preferred_supplier} is the preferred supplier for orders` : ''}. Links are managed on{' '}
            <a href={href('/suppliers')}>Suppliers</a>.
          </span>
        </div>
        <div className="mt-1.5 rounded-card border border-line px-3.5 pb-2.5 pt-1">
          {d.offers.length === 0 ? (
            <p className="py-2 text-base text-ink-2">No supplier linked yet. The price above is used as-is.</p>
          ) : (
            d.offers.map((o) => (
              <div
                key={o.supplier_product_id}
                className="grid grid-cols-[28px_minmax(0,1.4fr)_minmax(0,1.4fr)_minmax(0,110px)_minmax(0,1fr)] items-center gap-2.5 border-b border-line py-2 text-base last:border-b-0"
              >
                <span
                  className={cx('text-[17px]', o.is_preferred ? 'text-alert' : 'text-ink-3')}
                  title={o.is_preferred ? 'Preferred supplier' : 'Not preferred'}
                  aria-label={o.is_preferred ? 'Preferred' : 'Not preferred'}
                >
                  {o.is_preferred ? '★' : '☆'}
                </span>
                <a href={href('/suppliers', { id: o.supplier_id })} className="truncate underline">
                  {o.supplier_name}
                </a>
                <span className="truncate text-ink-2">
                  {qtyText(o.pack_size)} {unitWord(o.pack_unit)} for {gbp(o.price_pence)}
                  {o.sku ? ` · ${o.sku}` : ''}
                </span>
                <span className="fig text-right">
                  {o.unit_cost_pence === null ? '—' : `${unitPrice(o.unit_cost_pence)}/${unitWord(row.unit)}`}
                </span>
                <span className={cx('text-sm', o.is_cheapest ? 'text-ink' : 'text-ink-2')}>
                  {o.vs_cheapest_pct === null ? '' : o.is_cheapest ? 'cheapest' : `+${Math.round(o.vs_cheapest_pct)}% vs cheapest`}
                </span>
              </div>
            ))
          )}
          {d.offers.some((o) => o.terms_are_placeholders) && (
            <p className="pt-1.5 text-xs text-ink-2">Some suppliers’ terms are still guesses (see Suppliers).</p>
          )}
        </div>
      </section>

      <section className="mb-4 text-base" aria-label="Shelf life">
        <span className="text-ink-2">Shelf life: </span>
        {row.shelf_life_days === null ? 'does not expire' : `${row.shelf_life_days} days`}
        {row.shelf_life_source === 'ESTIMATE' && <em className="text-ink-2"> (estimate)</em>}
        <span className="text-ink-2"> · {row.storage.toLowerCase()} · </span>
        <a href={href('/stock', { id: row.ingredient_id })}>confirm it on Stock</a>
      </section>

      <section className="mb-4" aria-labelledby="ing-used">
        <h3 id="ing-used" className="mb-1.5 text-lg font-extrabold">
          Used in {d.used_in.length} item{d.used_in.length === 1 ? '' : 's'}
        </h3>
        <div className="flex flex-wrap gap-1.5">
          {d.used_in.slice(0, 40).map((u) => (
            <a
              key={u.menu_item_id}
              href={href('/menu', { item: u.menu_item_id })}
              title={u.template_name ? `through the ${u.template_name} recipe` : 'one-off recipe'}
              className="rounded-button border border-line-strong px-3.5 py-1.5 text-base no-underline hover:bg-canvas"
            >
              {u.name}
            </a>
          ))}
          {d.used_in.length > 40 && <span className="px-2 py-1.5 text-base text-ink-2">and {d.used_in.length - 40} more</span>}
        </div>
      </section>

      <section aria-labelledby="ing-hist">
        <h3 id="ing-hist" className="mb-1 text-lg font-extrabold">
          Price history
        </h3>
        {d.price_history.map((p, i) => (
          <div key={i} className="flex flex-wrap gap-x-3 border-b border-line py-1 text-base">
            <span className="w-[110px] flex-none text-ink-2">from {dayLabel(p.effective_from)}</span>
            <span className={cx('fig', p.source === 'ESTIMATE' && 'italic')}>
              {qtyText(p.pack_size)} {unitWord(p.pack_unit)} for {gbp(p.pack_cost_pence)} · {unitPrice(p.cost_per_unit_pence)}/
              {unitWord(row.unit)}
            </span>
            <span className="text-ink-2">
              {p.source === 'ESTIMATE' ? 'estimate' : p.source === 'INVOICE' ? 'invoice' : 'supplier list'}
              {p.supplier_name ? ` · ${p.supplier_name}` : ''}
              {p.recorded_by ? ` · ${p.recorded_by}` : ''}
            </span>
          </div>
        ))}
      </section>
    </div>
  )
}

function PriceEditor({ d }: { d: IngredientDetail }) {
  const row = d.row
  const pack = row.pack
  const [size, setSize] = useState(pack ? qtyText(pack.pack_size) : '1')
  const [packUnit, setPackUnit] = useState<Unit>(pack?.pack_unit ?? row.unit)
  const [cost, setCost] = useState(pack ? penceToPounds(pack.pack_cost_pence) : '')
  const [source, setSource] = useState<PriceSource | ''>('')
  const preferred = d.offers.find((o) => o.is_preferred)
  const [supplier, setSupplier] = useState<number | null>(pack?.supplier_id ?? preferred?.supplier_id ?? null)
  const [operator] = useOperator()
  const [applying, setApplying] = useState(false)
  const [done, setDone] = useState<string | null>(null)
  const invalidate = useInvalidateMenu()

  const sizeOut = qtyOut(size)
  const pence = poundsToPence(cost)
  const changed =
    !pack ||
    !sameQty(sizeOut ?? '', pack.pack_size) ||
    packUnit !== pack.pack_unit ||
    pence !== pack.pack_cost_pence ||
    source !== ''
  const valid = sizeOut !== null && sizeOut !== '0' && pence !== null
  const body =
    valid && source !== ''
      ? { pack_size: sizeOut, pack_unit: packUnit, pack_cost_pence: pence, source, supplier_id: supplier }
      : null
  const key = changed && body ? JSON.stringify(body) : null
  const pv = usePreview<IngredientPricePreview>(key, () => ingredientApi.pricePreview(row.ingredient_id, body!))
  const cur = pv.key === key ? pv : null
  const est = row.unit_cost.is_estimate
  const suppliers = [...new Map(d.offers.map((o) => [o.supplier_id, o.supplier_name])).entries()]

  return (
    <>
      <div className="mb-4 grid grid-cols-[repeat(auto-fit,minmax(150px,1fr))] gap-3.5">
        <label className="flex min-w-0 flex-col gap-1 text-base text-ink-2">
          Pack size
          <span className="flex gap-1.5">
            <Input numeric value={size} onChange={(e) => QTY_INPUT.test(e.target.value) && setSize(e.target.value)} className="text-ink" />
            <span className="w-[76px] flex-none">
              <Select aria-label="Pack unit" value={packUnit} onChange={(e) => setPackUnit(e.target.value as Unit)} className="text-ink">
                {compatibleUnits(row.unit).map((u) => (
                  <option key={u} value={u}>
                    {unitWord(u)}
                  </option>
                ))}
              </Select>
            </span>
          </span>
        </label>
        <label className="flex min-w-0 flex-col gap-1 text-base text-ink-2">
          Pack cost £
          <Input
            numeric
            aria-label="Pack cost in pounds"
            value={cost}
            placeholder="0.00"
            onChange={(e) => MONEY_INPUT.test(e.target.value) && setCost(e.target.value)}
            className="text-ink"
          />
        </label>
      </div>

      <div className="mb-4 flex flex-wrap items-center gap-[18px] rounded-card border border-line px-4 py-3">
        <div>
          <div className="text-base text-ink-2">Cost per unit</div>
          <div className={cx('fig text-2xl', est && 'italic')}>
            {row.unit_cost.pence === null ? 'no price' : `${unitPrice(row.unit_cost.pence)} / ${unitWord(row.unit)}`}
          </div>
        </div>
        <Checkbox
          checked={est}
          disabled
          onChange={() => undefined}
          label={<span className="text-md">Price is an estimate</span>}
        />
        <div className="flex-1" />
        <p className="max-w-[320px] text-base text-ink-2">
          {row.used_in_count === 0
            ? 'Not used in any recipe yet.'
            : `A new price updates the cost of ${row.used_in_count} menu item${row.used_in_count === 1 ? '' : 's'} from today, after you check it.`}
          {est && ' The estimate flag clears only when you record a price from an invoice or a supplier price list.'}
        </p>
      </div>

      {changed && (
        <div className="mb-4 flex max-w-[640px] flex-col gap-2.5">
          <div className="flex flex-wrap items-center gap-2.5">
            <span className="text-base font-bold">Where is this price from?</span>
            <Segmented
              label="Price source"
              value={source === '' ? ('' as never) : source}
              onChange={(v) => setSource(v)}
              options={[
                { value: 'INVOICE', label: 'Invoice' },
                { value: 'SUPPLIER_FEED', label: 'Supplier price list' },
                { value: 'ESTIMATE', label: 'Still an estimate' },
              ]}
            />
          </div>
          {suppliers.length > 0 && (
            <label className="flex max-w-[320px] flex-col gap-1 text-xs font-bold text-ink-2">
              Supplier
              <Select
                value={supplier === null ? '' : String(supplier)}
                onChange={(e) => setSupplier(e.target.value === '' ? null : Number(e.target.value))}
                className="font-normal text-ink"
              >
                <option value="">Not from a linked supplier</option>
                {suppliers.map(([id, n]) => (
                  <option key={id} value={id}>
                    {n}
                  </option>
                ))}
              </Select>
            </label>
          )}
          {!valid && <p className="text-sm text-bad-ink">Enter a pack size above 0 and a pack cost in pounds.</p>}
          {valid && source === '' && <p className="text-sm text-ink-2">Say where the price is from to see what it changes.</p>}
          {done && <p role="status" className="text-sm">{done}</p>}
          {body && (
            <ImpactPanel
              title="What this price does"
              status={cur?.status ?? { kind: 'loading' }}
              diff={cur?.data?.diff ?? []}
              figures={cur?.data ? impactFigures(cur.data.impact) : []}
              warnings={cur?.data?.impact.warnings ?? []}
              note="The price is recorded from today. Costs already reported for past sales keep the price in force then."
              applyLabel="Record price from today"
              onDiscard={() => {
                setSize(pack ? qtyText(pack.pack_size) : '1')
                setPackUnit(pack?.pack_unit ?? row.unit)
                setCost(pack ? penceToPounds(pack.pack_cost_pence) : '')
                setSource('')
              }}
              applying={applying}
              ready={cur?.status.kind === 'ready'}
              operatorWhat="record a price"
              onApply={async () => {
                if (!operator || !body) return
                setApplying(true)
                const r = await ingredientApi.priceApply(row.ingredient_id, { ...body, actor: operator })
                setApplying(false)
                if (r.kind === 'ok') {
                  setDone(`Recorded. ${r.data.rollup_items_recosted} menu item cost(s) recalculated.`)
                  setSource('')
                  await invalidate()
                } else setDone(r.message)
              }}
            />
          )}
        </div>
      )}
    </>
  )
}

export function CreateIngredient({ categories, defaultCategory, onCreated }: { categories: string[]; defaultCategory: string; onCreated: (id: number) => void }) {
  const [name, setName] = useState('')
  const [cat, setCat] = useState(defaultCategory)
  const [unit, setUnit] = useState<Unit>('EACH')
  const [storage, setStorage] = useState<'AMBIENT' | 'CHILLED' | 'FROZEN'>('AMBIENT')
  const [life, setLife] = useState('')
  const [size, setSize] = useState('1')
  const [cost, setCost] = useState('')
  const [source, setSource] = useState<PriceSource>('ESTIMATE')
  const [operator] = useOperator()
  const [busy, setBusy] = useState(false)
  const [msg, setMsg] = useState<string | null>(null)
  const invalidate = useInvalidateMenu()
  const pence = poundsToPence(cost)
  const sizeOut = qtyOut(size)
  return (
    <div className="min-h-0 flex-1 overflow-y-auto px-4 py-[18px] sm:px-[22px]">
      <h2 className="text-3xl font-extrabold tracking-[-.01em]">New ingredient</h2>
      <p className="mb-4 mt-1 text-base text-ink-2">Starts on tier C, not stock-tracked. A shelf life you type is kept as an estimate until confirmed on Stock.</p>
      <div className="grid max-w-[720px] grid-cols-[repeat(auto-fit,minmax(150px,1fr))] gap-3.5">
        <label className="flex min-w-0 flex-col gap-1 text-base text-ink-2">
          Name
          <Input value={name} onChange={(e) => setName(e.target.value)} className="text-ink" autoFocus />
        </label>
        <label className="flex min-w-0 flex-col gap-1 text-base text-ink-2">
          Category
          <Select value={cat} onChange={(e) => setCat(e.target.value)} className="text-ink">
            <option value="">No category</option>
            {categories.map((c) => (
              <option key={c} value={c}>
                {c}
              </option>
            ))}
          </Select>
        </label>
        <label className="flex min-w-0 flex-col gap-1 text-base text-ink-2">
          Costed per
          <Select value={unit} onChange={(e) => setUnit(e.target.value as Unit)} className="text-ink">
            {UNITS.map((u) => (
              <option key={u} value={u}>
                {unitWord(u)}
              </option>
            ))}
          </Select>
        </label>
        <label className="flex min-w-0 flex-col gap-1 text-base text-ink-2">
          Storage
          <Select value={storage} onChange={(e) => setStorage(e.target.value as typeof storage)} className="text-ink">
            <option value="AMBIENT">Ambient</option>
            <option value="CHILLED">Chilled</option>
            <option value="FROZEN">Frozen</option>
          </Select>
        </label>
        <label className="flex min-w-0 flex-col gap-1 text-base text-ink-2">
          Shelf life, days
          <Input numeric value={life} placeholder={storage === 'AMBIENT' ? 'blank = never expires' : 'required'} onChange={(e) => /^\d*$/.test(e.target.value) && setLife(e.target.value)} className="text-ink" />
        </label>
        <label className="flex min-w-0 flex-col gap-1 text-base text-ink-2">
          Pack size ({unitWord(unit)})
          <Input numeric value={size} onChange={(e) => QTY_INPUT.test(e.target.value) && setSize(e.target.value)} className="text-ink" />
        </label>
        <label className="flex min-w-0 flex-col gap-1 text-base text-ink-2">
          Pack cost £
          <Input numeric value={cost} placeholder="leave blank if unknown" onChange={(e) => MONEY_INPUT.test(e.target.value) && setCost(e.target.value)} className="text-ink" />
        </label>
      </div>
      {pence !== null && (
        <div className="mt-3.5 flex flex-wrap items-center gap-2.5">
          <span className="text-base font-bold">Where is this price from?</span>
          <Segmented
            label="Price source"
            value={source}
            onChange={setSource}
            options={[
              { value: 'INVOICE', label: 'Invoice' },
              { value: 'SUPPLIER_FEED', label: 'Supplier price list' },
              { value: 'ESTIMATE', label: 'Still an estimate' },
            ]}
          />
        </div>
      )}
      {msg && (
        <p role="alert" className="mt-3 text-sm text-bad-ink">
          {msg}
        </p>
      )}
      <div className="mt-4 flex items-center gap-3">
        <Button
          variant="primary"
          pending={busy}
          pendingLabel="Adding…"
          disabled={operator === null || name.trim() === '' || (pence !== null && (sizeOut === null || sizeOut === '0'))}
          onClick={async () => {
            if (!operator) return
            setBusy(true)
            const r = await ingredientApi.create({
              name: name.trim(),
              unit,
              category: cat || null,
              storage,
              shelf_life_days: life === '' ? null : Number(life),
              price:
                pence === null
                  ? null
                  : { pack_size: sizeOut, pack_unit: unit, pack_cost_pence: pence, source, supplier_id: null },
              actor: operator,
            })
            setBusy(false)
            if (r.kind === 'ok') {
              await invalidate()
              onCreated(r.data.ingredient_id)
            } else setMsg(r.message)
          }}
        >
          Add ingredient
        </Button>
        <OperatorNeeded what="add an ingredient" />
      </div>
    </div>
  )
}
