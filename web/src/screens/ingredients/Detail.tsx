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
import { useEffect, useId, useState } from 'react'
import type { ReactNode } from 'react'
import {
  Button,
  ConfirmTwiceButton,
  ErrorBox,
  Field,
  Input,
  Loading,
  Pill,
  Segmented,
  Select,
  StatusLine,
  THead,
  TBody,
  Table,
  Td,
  Textarea,
  Th,
  TierBadge,
  TitleInput,
  Tr,
  cx,
} from '../../components/ui'
import type { Outcome } from '../../components/ui'
import { parseDec, toFixed, trimQty } from '../../lib/dec'
import { ingredientApi, useIngredient, useInvalidateMenu } from '../../lib/menu-api'
import { useOperator } from '../../lib/operator'
import { href } from '../../lib/router'
import type { IngredientDetail, IngredientPricePreview, IngredientRow, PriceSource, Unit } from '../../lib/types/menu'
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
import { Allergens, IngredientPhoto } from './PhotoAllergens'

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
  const [metaMsg, setMetaMsg] = useState<Outcome | null>(null)
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
    setSaving(true)
    const body: Record<string, unknown> = { actor: operator }
    if (name.trim() !== row.name) body.name = name.trim()
    if (cat !== (row.category ?? '')) body.category = cat || null
    if (note !== (row.note ?? '')) body.note = note || null
    if (unit !== row.unit) body.unit = unit
    const r = await ingredientApi.meta(row.ingredient_id, body)
    setSaving(false)
    if (r.kind === 'ok') {
      setMetaMsg({ kind: 'ok', text: 'Saved.' })
      await invalidate()
    } else setMetaMsg({ kind: 'error', text: r.message })
  }

  const cats = [...new Set([...categories, row.category ?? ''].filter(Boolean))].sort((a, b) => a.localeCompare(b))
  const lockedText = Object.entries(d.unit_locked_by)
    .map(([k, v]) => `${v} ${k}`)
    .join(', ')

  return (
    <div className="min-h-0 flex-1 overflow-y-auto px-4 py-4 sm:px-5">
      {/* The name is an input; the heading outline still needs the entity's name. */}
      <h2 className="sr-only">{row.name}</h2>
      <div className="flex items-center gap-3">
        <TitleInput aria-label="Ingredient name" value={name} onChange={(e) => setName(e.target.value)} className="flex-1" />
        <ConfirmTwiceButton
          variant="danger"
          armedLabel="Tap again to retire"
          onConfirm={async () => {
            const r = await ingredientApi.retire(row.ingredient_id, operator)
            if (r.kind === 'ok') {
              await invalidate()
              onRetired()
            } else setMetaMsg({ kind: 'error', text: r.message })
          }}
        >
          Retire
        </ConfirmTwiceButton>
      </div>
      <NoteField value={note} onChange={setNote} />
      {Object.keys(d.retire_blocked_by).length > 0 && (
        <p className="mt-1 text-sm text-ink-2">
          Cannot be retired while{' '}
          {Object.entries(d.retire_blocked_by)
            .map(([k, v]) => `${v} ${k}`)
            .join(', ')}{' '}
          use it. Nothing is ever deleted; retired ingredients keep their history.
        </p>
      )}

      <div className="mt-4 flex flex-col gap-4 sm:flex-row sm:items-start">
        <IngredientPhoto row={row} />
        <Allergens key={`${row.allergens_source ?? ''}-${(row.allergens ?? ['?']).join()}`} row={row} />
      </div>

      <div className="my-4 grid grid-cols-[repeat(auto-fit,minmax(150px,1fr))] gap-3.5">
        <Field label="Category">
          <Select value={cat} onChange={(e) => setCat(e.target.value)}>
            <option value="">No category</option>
            {cats.map((c) => (
              <option key={c} value={c}>
                {c}
              </option>
            ))}
          </Select>
        </Field>
        <Field
          label="Costed per"
          hint={unitLocked ? `Fixed: ${lockedText} record quantities in ${unitWord(row.unit)}.` : undefined}
        >
          <Select value={unit} disabled={unitLocked} onChange={(e) => setUnit(e.target.value as Unit)}>
            {UNITS.map((u) => (
              <option key={u} value={u}>
                {unitWord(u)}
              </option>
            ))}
          </Select>
        </Field>
      </div>

      {metaDirty && (
        <div className="mb-4 flex flex-wrap items-center gap-2">
          <Button variant="primary" size="sm" pending={saving} pendingLabel="Saving…" onClick={saveMeta}>
            Save changes
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
        </div>
      )}
      <StatusLine outcome={metaMsg} className="mb-3" />

      <PriceEditor d={d} />
      <ShelfLife row={row} />

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
            <Table label="Where to buy" minWidth={520}>
              <THead>
                <tr>
                  <Th width={28}>
                    <span className="sr-only">Preferred</span>
                  </Th>
                  <Th>Supplier</Th>
                  <Th>Pack</Th>
                  <Th numeric>Per unit</Th>
                  <Th>vs cheapest</Th>
                </tr>
              </THead>
              <TBody>
                {d.offers.map((o) => (
                  <Tr key={o.supplier_product_id}>
                    <Td className={cx('text-lg', o.is_preferred ? 'text-brand-ink' : 'text-ink-3')}>
                      <span aria-hidden="true">{o.is_preferred ? '★' : '☆'}</span>
                      <span className="sr-only">{o.is_preferred ? 'Preferred' : 'Not preferred'}</span>
                    </Td>
                    <Td>
                      <a href={href(`/suppliers/${o.supplier_id}`)} className="underline">
                        {o.supplier_name}
                      </a>
                    </Td>
                    <Td className="text-ink-2">
                      {qtyText(o.pack_size)} {unitWord(o.pack_unit)} for {gbp(o.price_pence)}
                      {o.sku ? ` · ${o.sku}` : ''}
                    </Td>
                    <Td numeric>{o.unit_cost_pence === null ? '—' : `${unitPrice(o.unit_cost_pence)}/${unitWord(row.unit)}`}</Td>
                    <Td className={cx('text-sm', o.is_cheapest ? 'text-ink' : 'text-ink-2')}>
                      {o.vs_cheapest_pct === null ? '' : o.is_cheapest ? 'cheapest' : `+${Math.round(o.vs_cheapest_pct)}% vs cheapest`}
                    </Td>
                  </Tr>
                ))}
              </TBody>
            </Table>
          )}
          {d.offers.some((o) => o.terms_are_placeholders) && (
            <p className="pt-1.5 text-xs text-ink-2">Some suppliers’ terms are still guesses (see Suppliers).</p>
          )}
        </div>
      </section>

      <details className="group mb-4 rounded-card border border-line">
        <summary className="flex cursor-pointer list-none items-center gap-2 px-3.5 py-2.5 [&::-webkit-details-marker]:hidden">
          <span aria-hidden="true" className="text-ink-2 transition-transform group-open:rotate-90 motion-reduce:transition-none">
            ▸
          </span>
          <span className="text-lg font-extrabold">Used in</span>
          <span className="fig text-lg font-extrabold">{d.used_in.length}</span>
          <span className="text-lg font-extrabold">item{d.used_in.length === 1 ? '' : 's'}</span>
          {d.used_in.length === 0 && <span className="text-sm text-ink-2">· not in any recipe yet</span>}
        </summary>
        <div className="flex flex-wrap gap-1.5 border-t border-line px-3.5 py-3">
          {d.used_in.slice(0, 40).map((u) => (
            <a
              key={u.menu_item_id}
              href={href('/menu', { item: u.menu_item_id })}
              className="rounded-button border border-line-strong px-3.5 py-1.5 text-base no-underline hover:bg-canvas"
            >
              {u.name}
              <span className="sr-only">{u.template_name ? ` (through the ${u.template_name} recipe)` : ' (one-off recipe)'}</span>
            </a>
          ))}
          {d.used_in.length > 40 && <span className="px-2 py-1.5 text-base text-ink-2">and {d.used_in.length - 40} more</span>}
        </div>
      </details>

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
  const [done, setDone] = useState<Outcome | null>(null)
  const packUnitId = useId()
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
        <Field label="Pack size">
          <span className="flex gap-1.5">
            <Input numeric value={size} onChange={(e) => QTY_INPUT.test(e.target.value) && setSize(e.target.value)} />
            <span className="w-[76px] flex-none">
              {/* Its own id: two controls in one Field would otherwise share the label's id. */}
              <Select id={packUnitId} aria-label="Pack unit" value={packUnit} onChange={(e) => setPackUnit(e.target.value as Unit)}>
                {compatibleUnits(row.unit).map((u) => (
                  <option key={u} value={u}>
                    {unitWord(u)}
                  </option>
                ))}
              </Select>
            </span>
          </span>
        </Field>
        <Field label="Pack cost £">
          <Input
            numeric
            value={cost}
            placeholder="0.00"
            onChange={(e) => MONEY_INPUT.test(e.target.value) && setCost(e.target.value)}
          />
        </Field>
      </div>

      <div className="mb-4 flex flex-wrap items-center gap-4 rounded-card border border-line px-4 py-3">
        <div>
          <div className="text-base text-ink-2">Cost per unit</div>
          <div className={cx('fig text-2xl', est && 'italic')}>
            {row.unit_cost.pence === null ? 'no price' : `${unitPrice(row.unit_cost.pence)} / ${unitWord(row.unit)}`}
          </div>
        </div>
        {row.unit_cost.pence !== null && (est ? <Pill tone="warn">Estimate</Pill> : <Pill tone="neutral">Recorded price</Pill>)}
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
            <Field label="Supplier" className="max-w-[320px]">
              <Select
                value={supplier === null ? '' : String(supplier)}
                onChange={(e) => setSupplier(e.target.value === '' ? null : Number(e.target.value))}
              >
                <option value="">Not from a linked supplier</option>
                {suppliers.map(([id, n]) => (
                  <option key={id} value={id}>
                    {n}
                  </option>
                ))}
              </Select>
            </Field>
          )}
          {!valid && <p className="text-sm text-bad-ink">Enter a pack size above 0 and a pack cost in pounds.</p>}
          {valid && source === '' && <p className="text-sm text-ink-2">Say where the price is from to see what it changes.</p>}
          <StatusLine outcome={done} />
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
                if (!body) return
                setApplying(true)
                const r = await ingredientApi.priceApply(row.ingredient_id, { ...body, actor: operator })
                setApplying(false)
                if (r.kind === 'ok') {
                  setDone({ kind: 'ok', text: `Recorded. ${r.data.rollup_items_recosted} menu item cost(s) recalculated.` })
                  setSource('')
                  await invalidate()
                } else setDone({ kind: 'error', text: r.message })
              }}
            />
          )}
        </div>
      )}
    </>
  )
}

/* ------------------------------------------------------------ small parts --- */

/** One line until focused; grows while you type. */
function NoteField({ value, onChange }: { value: string; onChange: (v: string) => void }) {
  const [focus, setFocus] = useState(false)
  const rows = focus ? Math.min(6, Math.max(3, value.split('\n').length)) : 1
  return (
    <textarea
      aria-label="Notes"
      rows={rows}
      maxLength={400}
      placeholder="Add a note…"
      value={value}
      onChange={(e) => onChange(e.target.value)}
      onFocus={() => setFocus(true)}
      onBlur={() => setFocus(false)}
      className={cx(
        'mt-1.5 block w-full resize-none rounded-control border px-2 py-1 text-sm outline-none placeholder:text-ink-3',
        focus
          ? 'edge-brand bg-surface text-ink'
          : 'overflow-hidden text-ellipsis whitespace-nowrap border-transparent bg-transparent text-ink-2 hover:border-line',
      )}
    />
  )
}

/** "0.05" → "5%". Exact: a Dec shifted two places, never a float. */
function wastePct(raw: string | undefined): string {
  const d = raw ? parseDec(raw) : null
  if (d === null) return '—'
  return `${trimQty(toFixed({ u: d.u * 100n, s: d.s }, d.s))}%`
}

const STORAGE_WORD: Record<string, string> = { AMBIENT: 'Ambient', CHILLED: 'Chilled', FROZEN: 'Frozen' }

function ShelfLife({ row }: { row: IngredientRow }) {
  const est = row.shelf_life_source === 'ESTIMATE' && (row.shelf_life_days !== null || row.open_life_days != null)
  const transit = row.transit_buffer_days ?? 0
  const usable = row.shelf_life_days === null ? null : Math.max(0, row.shelf_life_days - transit)
  const tiles: { k: string; label: string; value: string; est?: boolean; hint?: string }[] = [
    { k: 'storage', label: 'Storage', value: STORAGE_WORD[row.storage] ?? row.storage },
    {
      k: 'life',
      label: 'Shelf life',
      value: row.shelf_life_days === null ? 'Does not expire' : `${row.shelf_life_days} days`,
      est: est && row.shelf_life_days !== null,
      hint: 'unopened',
    },
    {
      k: 'open',
      label: 'Once opened',
      value: row.open_life_days == null ? '—' : `${row.open_life_days} days`,
      est: est && row.open_life_days != null,
    },
    { k: 'transit', label: 'Transit buffer', value: `${transit} day${transit === 1 ? '' : 's'}` },
    {
      k: 'usable',
      label: 'Order covers at most',
      value: usable === null ? 'No cap' : `${usable} days`,
      est: est && usable !== null,
      hint: usable === null ? undefined : 'shelf life − transit',
    },
    { k: 'waste', label: 'Waste factor', value: wastePct(row.waste_factor), hint: 'stock only, not cost' },
  ]
  return (
    <section className="mb-4" aria-labelledby="ing-life">
      <div className="mb-1.5 flex flex-wrap items-baseline gap-x-2.5">
        <h3 id="ing-life" className="text-lg font-extrabold">
          Storage & shelf life
        </h3>
        <span className="text-sm text-ink-2">
          {est ? (
            <>
              <em>Italic = estimate</em>, still capping orders. <a href={href(`/stock/${row.ingredient_id}`)}>Confirm it on Stock</a>
            </>
          ) : row.shelf_life_source && row.shelf_life_source !== 'ESTIMATE' ? (
            'Confirmed.'
          ) : (
            <a href={href(`/stock/${row.ingredient_id}`)}>Set on Stock</a>
          )}
        </span>
        <span className="ml-auto flex items-center gap-1.5 text-sm text-ink-2">
          <TierBadge tier={row.tier ?? 'C'} /> tier
        </span>
      </div>
      <dl className="grid grid-cols-2 gap-px overflow-hidden rounded-card border border-line bg-line sm:grid-cols-3">
        {tiles.map((t) => (
          <div key={t.k} className="min-w-0 bg-surface px-3.5 py-2.5">
            <dt className="text-label font-bold uppercase tracking-[.05em] text-ink-2">{t.label}</dt>
            <dd className={cx('fig mt-0.5 text-lg', t.est && 'italic')}>{t.value}</dd>
            {t.hint && <dd className="text-xs text-ink-2">{t.hint}</dd>}
          </div>
        ))}
      </dl>
    </section>
  )
}

/* -------------------------------------------------------------- creation --- */

/**
 * Display-only cost per ingredient unit for a pack being typed: exact BigInt
 * division to 6 dp of a penny. The server computes the recorded figure.
 */
function perUnitPence(pence: number, sizeText: string, packUnit: Unit, unit: Unit): string | null {
  const d = parseDec(sizeText)
  if (d === null || d.u <= 0n) return null
  let mulF = 1n
  let divF = 1n
  if ((packUnit === 'L' && unit === 'ML') || (packUnit === 'KG' && unit === 'G')) mulF = 1000n
  else if ((packUnit === 'ML' && unit === 'L') || (packUnit === 'G' && unit === 'KG')) divF = 1000n
  else if (packUnit !== unit) return null
  const num = BigInt(pence) * 10n ** BigInt(d.s) * divF * 10n ** 6n
  const den = d.u * mulF
  const q = (num * 2n + den) / (2n * den)
  return toFixed({ u: q, s: 6 }, 6)
}

/** "5" or "2.5" percent → the fraction string the API takes ("0.05"). */
function pctToFraction(text: string): string | null {
  const t = text.trim()
  if (t === '') return '0'
  const d = parseDec(t.endsWith('.') ? t.slice(0, -1) : t)
  if (d === null || d.u < 0n) return null
  return toFixed({ u: d.u, s: d.s + 2 }, d.s + 2)
}

function FormSection({ title, note, children }: { title: string; note?: string; children: ReactNode }) {
  return (
    <section className="border-t border-line pt-4">
      <h3 className="text-label font-bold uppercase tracking-[.05em] text-ink-2">{title}</h3>
      {note && <p className="mt-0.5 text-sm text-ink-2">{note}</p>}
      <div className="mt-3 grid grid-cols-1 gap-3.5 sm:grid-cols-2 wide:grid-cols-3">{children}</div>
    </section>
  )
}

const DAYS_INPUT = /^\d{0,4}$/

export function CreateIngredient({
  categories,
  suppliers,
  defaultCategory,
  onCreated,
  onCancel,
}: {
  categories: string[]
  suppliers: { supplier_id: number; name: string }[]
  defaultCategory: string
  onCreated: (id: number) => void
  onCancel: () => void
}) {
  const [name, setName] = useState('')
  const [cat, setCat] = useState(defaultCategory)
  const [unit, setUnitRaw] = useState<Unit>('EACH')
  const [supplier, setSupplier] = useState('')
  const [sku, setSku] = useState('')
  const [size, setSize] = useState('1')
  const [packUnit, setPackUnit] = useState<Unit>('EACH')
  const [cost, setCost] = useState('')
  const [source, setSource] = useState<PriceSource>('ESTIMATE')
  const [storage, setStorage] = useState<'AMBIENT' | 'CHILLED' | 'FROZEN'>('AMBIENT')
  const [life, setLife] = useState('')
  const [openLife, setOpenLife] = useState('')
  const [transit, setTransit] = useState('0')
  const [waste, setWaste] = useState('')
  const [note, setNote] = useState('')
  const [tried, setTried] = useState(false)
  const [operator] = useOperator()
  const [busy, setBusy] = useState(false)
  const [msg, setMsg] = useState<string | null>(null)
  const packUnitId = useId()
  const invalidate = useInvalidateMenu()

  const setUnit = (u: Unit) => {
    setUnitRaw(u)
    if (!compatibleUnits(u).includes(packUnit)) setPackUnit(u)
  }
  const pence = poundsToPence(cost)
  const sizeOut = qtyOut(size)
  const lifeN = life === '' ? null : Number(life)
  const openN = openLife === '' ? null : Number(openLife)
  const transitN = transit === '' ? 0 : Number(transit)
  const wasteOut = pctToFraction(waste)
  const perUnit = pence !== null && sizeOut !== null ? perUnitPence(pence, sizeOut, packUnit, unit) : null
  const cats = [...new Set([...categories, defaultCategory].filter(Boolean))].sort((a, b) => a.localeCompare(b))

  const errors: Partial<Record<'name' | 'size' | 'cost' | 'life' | 'open' | 'transit' | 'waste', string>> = {}
  if (name.trim() === '') errors.name = 'Give it a name.'
  if (cost !== '' && pence === null) errors.cost = 'Pounds and pence, e.g. 3.90.'
  if (supplier !== '' && pence === null) errors.cost = 'A supplier link needs the pack cost.'
  if (pence !== null && (sizeOut === null || sizeOut === '0')) errors.size = 'A pack size above 0.'
  if (storage !== 'AMBIENT' && lifeN === null)
    errors.life = `A ${storage.toLowerCase()} ingredient needs a shelf life; blank means it never expires.`
  if (lifeN !== null && lifeN < 1) errors.life = 'At least 1 day.'
  if (openN !== null && openN < 1) errors.open = 'At least 1 day.'
  else if (openN !== null && lifeN !== null && openN > lifeN) errors.open = 'Cannot be longer than the unopened shelf life.'
  if (lifeN !== null && transitN >= lifeN) errors.transit = 'Must be shorter than the shelf life.'
  if (wasteOut === null || Number(waste || '0') > 50) errors.waste = 'A percentage from 0 to 50.'
  const blocking = Object.keys(errors).length > 0
  // Required-but-empty errors wait for a submit attempt; wrong values show at once.
  const show = (k: keyof typeof errors) => (tried || (k !== 'name' && k !== 'life') ? errors[k] : undefined)

  const submit = async () => {
    setTried(true)
    if (blocking) return
    setBusy(true)
    const r = await ingredientApi.create({
      name: name.trim(),
      unit,
      category: cat.trim() || null,
      storage,
      shelf_life_days: lifeN,
      open_life_days: openN,
      transit_buffer_days: transitN,
      waste_factor: wasteOut ?? '0',
      note: note.trim() || null,
      price:
        pence === null
          ? null
          : { pack_size: sizeOut, pack_unit: packUnit, pack_cost_pence: pence, source, supplier_id: supplier === '' ? null : Number(supplier) },
      sku: supplier !== '' && sku.trim() ? sku.trim() : null,
      actor: operator,
    })
    setBusy(false)
    if (r.kind === 'ok') {
      await invalidate()
      onCreated(r.data.ingredient_id)
    } else setMsg(r.message)
  }

  return (
    <form
      className="min-h-0 flex-1 overflow-y-auto px-4 py-4 sm:px-5"
      onSubmit={(e) => {
        e.preventDefault()
        void submit()
      }}
      noValidate
    >
      <div className="max-w-[780px]">
        <h2 className="text-3xl font-extrabold tracking-[-.01em]">New ingredient</h2>
        <p className="mb-4 mt-1 text-base text-ink-2">Only the name is required. Anything you leave blank can be filled in later.</p>

        <div className="flex flex-col gap-5">
          <FormSection title="Basics">
            <Field label="Name" error={show('name')} className="sm:col-span-2 wide:col-span-2">
              <Input value={name} onChange={(e) => setName(e.target.value)} autoFocus placeholder="e.g. Oat milk (Oatly Barista)" />
            </Field>
            <Field label="Category" hint="Pick one or type a new one">
              <Input list="ingredient-categories" value={cat} onChange={(e) => setCat(e.target.value)} placeholder="No category" />
              <datalist id="ingredient-categories">
                {cats.map((c) => (
                  <option key={c} value={c} />
                ))}
              </datalist>
            </Field>
            <div className="flex min-w-0 flex-col gap-1 sm:col-span-2 wide:col-span-3">
              <span className="text-xs font-bold text-ink-2" id="unit-label">
                Costed and counted per
              </span>
              <Segmented
                label="Costed per"
                value={unit}
                onChange={setUnit}
                options={UNITS.map((u) => ({ value: u, label: unitWord(u) }))}
                className="self-start"
              />
              <span className="text-xs text-ink-2">Recipes and stock counts use this unit. It locks once anything records a quantity in it.</span>
            </div>
          </FormSection>

          <FormSection title="Buying & price" note="Leave the cost blank if you do not know it yet: the ingredient then shows “no price”, never £0.">
            <Field label="Supplier" hint={supplier === '' ? undefined : 'Linked as the preferred supplier'}>
              <Select value={supplier} onChange={(e) => setSupplier(e.target.value)}>
                <option value="">No supplier yet</option>
                {suppliers.map((s) => (
                  <option key={s.supplier_id} value={s.supplier_id}>
                    {s.name}
                  </option>
                ))}
              </Select>
            </Field>
            <Field label="Supplier code (SKU)" hint="Optional">
              <Input value={sku} maxLength={80} disabled={supplier === ''} onChange={(e) => setSku(e.target.value)} placeholder={supplier === '' ? 'Choose a supplier first' : ''} />
            </Field>
            <Field label="Pack size" error={show('size')}>
              <span className="flex gap-1.5">
                <Input numeric value={size} onChange={(e) => QTY_INPUT.test(e.target.value) && setSize(e.target.value)} />
                <span className="w-[84px] flex-none">
                  <Select id={packUnitId} aria-label="Pack unit" value={packUnit} onChange={(e) => setPackUnit(e.target.value as Unit)}>
                    {compatibleUnits(unit).map((u) => (
                      <option key={u} value={u}>
                        {unitWord(u)}
                      </option>
                    ))}
                  </Select>
                </span>
              </span>
            </Field>
            <Field label="Pack cost £" error={show('cost')}>
              <Input numeric value={cost} placeholder="unknown" onChange={(e) => MONEY_INPUT.test(e.target.value) && setCost(e.target.value)} />
            </Field>
            <div className="flex min-w-0 flex-col justify-end rounded-card bg-canvas-2 px-3.5 py-2">
              <span className="text-xs font-bold text-ink-2">Cost per {unitWord(unit)}</span>
              <span className={cx('fig text-2xl', source === 'ESTIMATE' && perUnit !== null && 'italic')}>
                {perUnit === null ? <span className="text-ink-2">no price</span> : unitPrice(perUnit)}
              </span>
              {perUnit !== null && source === 'ESTIMATE' && <span className="text-xs text-ink-2">flagged as an estimate</span>}
            </div>
            {pence !== null && (
              <div className="flex min-w-0 flex-col gap-1 sm:col-span-2 wide:col-span-3">
                <span className="text-xs font-bold text-ink-2">Where is this price from?</span>
                <Segmented
                  label="Price source"
                  value={source}
                  onChange={setSource}
                  className="max-w-full self-start overflow-x-auto"
                  options={[
                    { value: 'INVOICE', label: 'Invoice' },
                    { value: 'SUPPLIER_FEED', label: 'Price list' },
                    { value: 'ESTIMATE', label: 'Estimate' },
                  ]}
                />
                <span className="text-xs text-ink-2">Only an invoice or a supplier price list clears the estimate flag.</span>
              </div>
            )}
          </FormSection>

          <FormSection title="Storage & shelf life" note="Shelf life caps how much one order can buy. What you type here stays an estimate until confirmed on Stock.">
            <div className="flex min-w-0 flex-col gap-1 sm:col-span-2 wide:col-span-3">
              <span className="text-xs font-bold text-ink-2">Storage</span>
              <Segmented
                label="Storage"
                value={storage}
                onChange={setStorage}
                className="self-start"
                options={[
                  { value: 'AMBIENT', label: 'Ambient' },
                  { value: 'CHILLED', label: 'Chilled' },
                  { value: 'FROZEN', label: 'Frozen' },
                ]}
              />
            </div>
            <Field label="Shelf life, days" hint="Unopened. Blank = never expires" error={show('life')}>
              <Input numeric value={life} placeholder={storage === 'AMBIENT' ? 'never expires' : 'required'} onChange={(e) => DAYS_INPUT.test(e.target.value) && setLife(e.target.value)} />
            </Field>
            <Field label="Once opened, days" hint="Optional" error={show('open')}>
              <Input numeric value={openLife} onChange={(e) => DAYS_INPUT.test(e.target.value) && setOpenLife(e.target.value)} />
            </Field>
            <Field
              label="Transit buffer, days"
              hint={lifeN !== null && !errors.transit ? `One order covers at most ${lifeN - transitN} days` : 'Days lost before it arrives'}
              error={show('transit')}
            >
              <Input numeric value={transit} onChange={(e) => /^\d{0,2}$/.test(e.target.value) && setTransit(e.target.value)} />
            </Field>
          </FormSection>

          <FormSection title="Stock">
            <Field label="Waste factor %" hint="Lost in use (foam, residue). Affects stock only, never menu cost." error={show('waste')}>
              <Input numeric value={waste} placeholder="0" onChange={(e) => QTY_INPUT.test(e.target.value) && setWaste(e.target.value)} />
            </Field>
            <div className="flex min-w-0 items-start gap-2.5 rounded-card bg-canvas-2 px-3.5 py-2.5 sm:col-span-1 wide:col-span-2">
              <TierBadge tier="C" />
              <p className="text-sm text-ink-2">
                Starts on tier C: on the checklist, not stock-tracked. Tracking and tiers are set on Stock; auto-ordering is earned
                with two counts within 10%, never switched on by hand.
              </p>
            </div>
          </FormSection>

          <section className="border-t border-line pt-4">
            <Field label="Notes" hint="Optional">
              <Textarea rows={2} maxLength={400} value={note} onChange={(e) => setNote(e.target.value)} placeholder="Brand, where it lives, anything worth knowing" />
            </Field>
          </section>
        </div>

        <StatusLine
          className="mt-4"
          outcome={
            msg !== null
              ? { kind: 'error', text: msg }
              : tried && blocking
                ? { kind: 'error', text: 'Fix the highlighted fields to add it.' }
                : null
          }
        />
        <div className="mt-5 flex flex-wrap items-center gap-3 border-t border-line pt-4">
          <Button type="submit" variant="primary" pending={busy} pendingLabel="Adding…">
            Add ingredient
          </Button>
          <Button type="button" variant="ghost" onClick={onCancel}>
            Cancel
          </Button>
        </div>
      </div>
    </form>
  )
}
