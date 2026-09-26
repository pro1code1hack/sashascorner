/**
 * One menu item as a full page, `#/menu/<menu item id>` (owner, 2026-09-26:
 * "like Deliveroo's edit item, straight to the point").
 *
 *   Details        name, category, note, on/off, photo (labels: save directly)
 *   Sizes          per size: sell price (previewed, applied from today),
 *                  what the ingredients cost, margin, time to make (seconds),
 *                  labour and £ per minute
 *   Recipe         the ingredients for the chosen size, with a cost donut.
 *                  One-off items edit their lines here (previewed, applied
 *                  from today, invariant 3); recipe items point at their
 *                  recipe in Menu › Recipes, where every item made from it
 *                  follows
 *   Order history  till lines matched to this item (read-only)
 *
 * The path id is the size being looked at; the other sizes are one tap away.
 * Nothing is deleted (sizes and items are taken off, never removed), and
 * who-changed-what is not shown here (owner's instruction).
 */
import { useEffect, useMemo, useState } from 'react'
import { Button, ErrorBox, IconButton, InfoPanel, Input, Loading, Select, SizeTile, Toggle, cx } from '../../components/ui'
import { fromInt, fromMoney, mul, parseDec, sub } from '../../lib/dec'
import { menuApi, useFetchMenuItem, useIngredients, useInvalidateMenu, useMenuItem, useMenuItems } from '../../lib/menu-api'
import { useOperator } from '../../lib/operator'
import { href, navigate } from '../../lib/router'
import type {
  Cost,
  IngredientRow,
  LineIn,
  MenuCategory,
  MenuGroup,
  MenuItemDetail,
  MenuSize,
  PricesPreview,
  Unit,
} from '../../lib/types/menu'
import {
  MONEY_INPUT,
  QTY_INPUT,
  SIZE_ORDER,
  compatibleUnits,
  costText,
  decStr,
  gbp,
  marginPct,
  pctText,
  penceToPounds,
  poundsToPence,
  qtyOut,
  qtyText,
  sameQty,
  sizeLabel,
  unitPrice,
  unitWord,
} from './common/figures'
import { ImpactPanel, impactFigures } from './common/Impact'
import { usePreview } from './common/usePreview'
import { CostDonut } from './CostDonut'
import { IngredientPicker } from './IngredientPicker'
import { ItemSales } from './ItemSales'
import { lastMenuListQuery } from './listMemory'
import { PhotoSlot } from './Photo'

const KIND_LABEL = { DRINKS: 'Drinks', FOOD: 'Food', OTHER: 'Other' } as const
const catLabel = (c: string) => c.replace(' (May 2026)', '')

export function ItemPage({ menuItemId }: { menuItemId: number }) {
  const detail = useMenuItem(menuItemId)
  const list = useMenuItems()
  const d = detail.data
  const g = d?.group
  const back = href('/menu', lastMenuListQuery())

  return (
    <>
      <header className="flex flex-none flex-wrap items-center gap-x-3 gap-y-1 border-b border-line px-4 py-3 sm:px-5">
        <a href={back} className="text-base font-bold text-brand-ink no-underline hover:underline">
          ‹ Menu
        </a>
        <span aria-hidden="true" className="text-ink-3">
          /
        </span>
        <h1 className="min-w-0 flex-1 truncate text-2xl font-extrabold tracking-[-.01em]">{g?.name ?? 'Menu item'}</h1>
        {g && (
          <span className="text-base text-ink-2 max-sm:hidden">
            {KIND_LABEL[g.kind]} · {g.category ? catLabel(g.category) : 'No category'}
          </span>
        )}
      </header>
      <div className="min-h-0 flex-1 overflow-y-auto bg-canvas">
        <div className="flex flex-col gap-4 px-4 pb-10 pt-4 sm:px-6 2xl:px-8">
          {detail.isLoading && <Loading what="Loading the item" />}
          {detail.error && <ErrorBox error={detail.error} what="this item" />}
          {d && g && (
            <div className="grid items-start gap-4 xl:grid-cols-[minmax(0,1fr)_360px] 2xl:grid-cols-[minmax(0,1fr)_420px]">
              <aside className="flex flex-col gap-4 xl:sticky xl:top-4 xl:order-last">
                <Panel>
                  <div className="grid gap-4 sm:grid-cols-[220px_minmax(0,1fr)] xl:grid-cols-1">
                    <PhotoSlot menuItemId={d.size.menu_item_id} url={g.photo_url} name={g.name} tall />
                    <Actions detail={d} />
                  </div>
                </Panel>
              </aside>
              <div className="flex min-w-0 flex-col gap-4">
                <Panel>
                  <Details key={g.anchor_id} detail={d} categories={list.data?.categories ?? []} />
                </Panel>
                <Panel>
                  <RecipeSection detail={d} />
                </Panel>
                <Panel>
                  <SizesSection key={g.anchor_id} group={g} current={d.size} />
                </Panel>
                <Panel>
                  <ItemSales
                    key={d.size.menu_item_id}
                    menuItemId={d.size.menu_item_id}
                    sizeCount={g.sizes.length}
                    sizeName={sizeLabel(d.size.size_code)}
                    onTill={g.on_till}
                  />
                </Panel>
              </div>
            </div>
          )}
        </div>
      </div>
    </>
  )
}

function Panel({ children }: { children: React.ReactNode }) {
  return <div className="rounded-card-lg bg-surface p-4 shadow-raised sm:p-5">{children}</div>
}

const openSize = (id: number) => navigate(`/menu/${id}`, { replace: true })

/* ------------------------------------------------------------- details --- */

function Details({ detail, categories }: { detail: MenuItemDetail; categories: MenuCategory[] }) {
  const group = detail.group
  const [name, setName] = useState(group.name)
  const [cat, setCat] = useState(group.category ?? '')
  const [note, setNote] = useState(group.note ?? '')
  const [operator] = useOperator()
  const [busy, setBusy] = useState(false)
  const [msg, setMsg] = useState<string | null>(null)
  const invalidate = useInvalidateMenu()
  useEffect(() => {
    setName(group.name)
    setCat(group.category ?? '')
    setNote(group.note ?? '')
  }, [group.name, group.category, group.note])
  const dirty = name.trim() !== group.name || cat !== (group.category ?? '') || note !== (group.note ?? '')
  const save = async (body: { name?: string; category?: string | null; note?: string | null; active?: boolean }) => {
    if (!operator) return
    setBusy(true)
    const r = await menuApi.group(group.anchor_id, { actor: operator, ...body })
    setBusy(false)
    if (r.kind === 'ok') {
      setMsg(null)
      await invalidate()
    } else setMsg(r.message)
  }
  const names = [...new Set([...categories.map((c) => c.name).filter(Boolean), group.category ?? ''])]
    .filter(Boolean)
    .sort((a, b) => a.localeCompare(b))
  return (
    <div className="flex min-w-0 flex-col gap-3">
        <label className="flex flex-col gap-1 text-xs font-bold text-ink-2">
          Name
          <input
            value={name}
            onChange={(e) => setName(e.target.value)}
            className="w-full border-b border-line bg-transparent py-0.5 text-2xl font-extrabold tracking-[-.01em] text-ink outline-none focus-visible:border-brand"
          />
        </label>
        <div className="grid grid-cols-1 gap-2 sm:grid-cols-2">
          <label className="flex min-w-0 flex-col gap-1 text-xs font-bold text-ink-2">
            Category
            <Select value={cat} onChange={(e) => setCat(e.target.value)} className="font-normal text-ink">
              <option value="">No category</option>
              {names.map((c) => (
                <option key={c} value={c}>
                  {catLabel(c)}
                </option>
              ))}
            </Select>
          </label>
          <label className="flex min-w-0 flex-col gap-1 text-xs font-bold text-ink-2">
            Note
            <Input value={note} placeholder="e.g. from Cakesmiths" onChange={(e) => setNote(e.target.value)} className="font-normal" />
          </label>
        </div>
        <div className="flex flex-wrap items-center gap-x-4 gap-y-1">
          <Toggle
            checked={group.is_active}
            onChange={(v) => save({ active: v })}
            disabled={operator === null || busy}
            label={group.is_active ? 'On the menu' : 'Off the menu'}
          />
          {group.season_name && <span className="text-sm text-ink-2">Seasonal: {group.season_name}</span>}
          {!group.on_till && (
            <span className="text-sm text-ink-2">Not on the till yet: sales are not counted until it is matched in Lightspeed.</span>
          )}
        </div>
        {dirty && (
          <div className="flex flex-wrap items-center gap-2">
            <Button
              variant="primary"
              size="sm"
              pending={busy}
              pendingLabel="Saving…"
              disabled={operator === null || name.trim() === ''}
              onClick={() =>
                save({
                  ...(name.trim() !== group.name ? { name: name.trim() } : {}),
                  ...(cat !== (group.category ?? '') ? { category: cat || null } : {}),
                  ...(note !== (group.note ?? '') ? { note: note || null } : {}),
                })
              }
            >
              Save details
            </Button>
            <Button
              variant="ghost"
              size="sm"
              onClick={() => {
                setName(group.name)
                setCat(group.category ?? '')
                setNote(group.note ?? '')
              }}
            >
              Undo
            </Button>
            {name.trim() !== group.name && <span className="text-sm text-ink-2">Renaming changes it here only; the till keeps its own name.</span>}
          </div>
        )}
        {msg && (
          <p role="alert" className="text-sm text-bad-ink">
            {msg}
          </p>
        )}
    </div>
  )
}

/* --------------------------------------------------------------- sizes --- */

const SIZE_GRID = 'sm:grid-cols-[52px_minmax(88px,1fr)_repeat(3,minmax(0,1fr))_minmax(84px,1fr)_minmax(0,1fr)]'

function SizesSection({ group, current }: { group: MenuGroup; current: MenuSize }) {
  const [operator] = useOperator()
  const invalidate = useInvalidateMenu()
  // Keyed by content, so a refetch that changes only prices resets only the
  // price drafts and leaves an unsaved time alone (and vice versa).
  const priceSig = JSON.stringify(group.sizes.map((s) => [s.menu_item_id, s.price_pence]))
  const timeSig = JSON.stringify(group.sizes.map((s) => [s.menu_item_id, s.prep_seconds ?? null]))
  const initialPrices = useMemo<Record<number, string>>(
    () => Object.fromEntries((JSON.parse(priceSig) as [number, number][]).map(([id, p]) => [id, penceToPounds(p)])),
    [priceSig],
  )
  const initialTimes = useMemo<Record<number, string>>(
    () => Object.fromEntries((JSON.parse(timeSig) as [number, number | null][]).map(([id, t]) => [id, t === null ? '' : String(t)])),
    [timeSig],
  )
  const [prices, setPrices] = useState<Record<number, string>>(initialPrices)
  const [times, setTimes] = useState<Record<number, string>>(initialTimes)
  useEffect(() => setPrices(initialPrices), [initialPrices])
  useEffect(() => setTimes(initialTimes), [initialTimes])
  const [timesEst, setTimesEst] = useState(group.sizes.some((s) => s.prep_is_estimate))
  const [msg, setMsg] = useState<string | null>(null)
  const [applying, setApplying] = useState(false)
  const [savingTimes, setSavingTimes] = useState(false)

  const changedPrices = group.sizes
    .map((s) => ({ menu_item_id: s.menu_item_id, price_pence: poundsToPence(prices[s.menu_item_id] ?? '') }))
    .filter((p): p is { menu_item_id: number; price_pence: number } => {
      const s = group.sizes.find((x) => x.menu_item_id === p.menu_item_id)
      return p.price_pence !== null && s !== undefined && p.price_pence !== s.price_pence
    })
  const key = changedPrices.length ? JSON.stringify(changedPrices) : null
  const pv = usePreview<PricesPreview>(key, () => menuApi.pricesPreview(changedPrices))
  const cur = pv.key === key ? pv : null

  const changedTimes: Record<number, number | null> = {}
  let timeError: string | null = null
  for (const s of group.sizes) {
    const t = (times[s.menu_item_id] ?? '').trim()
    if (t === (initialTimes[s.menu_item_id] ?? '')) continue
    if (t === '') {
      if (s.prep_is_override) changedTimes[s.menu_item_id] = null
      continue
    }
    const n = Number(t)
    if (!Number.isInteger(n) || n < 1 || n > 3600) timeError = 'A time to make is whole seconds, 1 to 3600.'
    else changedTimes[s.menu_item_id] = n
  }
  const timesDirty = Object.keys(changedTimes).length > 0

  const present = new Set(group.sizes.map((s) => s.size_code ?? 'ONE'))
  const missing = group.template_id === null ? SIZE_ORDER.filter((s) => !present.has(s)) : []

  return (
    <section aria-labelledby="mi-sizes" className="flex flex-col gap-3">
      <div className="flex flex-wrap items-baseline justify-between gap-2">
        <h2 id="mi-sizes" className="text-xl font-extrabold tracking-[-.01em]">
          Sizes, prices and time to make
        </h2>
        <span className="text-sm text-ink-2">Labour at the loaded hourly rate. Margins under 60% are marked.</span>
      </div>
      <div className={cx('hidden gap-3 border-b border-line pb-1.5 text-label font-bold uppercase tracking-[.06em] text-ink-3 sm:grid', SIZE_GRID)}>
        <span>Size</span>
        <span>Price</span>
        <span className="text-right">Ingredients</span>
        <span className="text-right">Margin</span>
        <span className="text-right">Labour</span>
        <span>Time (s)</span>
        <span className="text-right">£ / min</span>
      </div>
      {group.sizes.map((s) => {
        const m = marginPct(s.price_pence, s.cost)
        const priceChanged = (prices[s.menu_item_id] ?? '') !== (initialPrices[s.menu_item_id] ?? '')
        const timeChanged = (times[s.menu_item_id] ?? '') !== (initialTimes[s.menu_item_id] ?? '')
        return (
          <div
            key={s.menu_item_id}
            className={cx(
              'grid grid-cols-2 items-center gap-x-3 gap-y-1.5 border-b border-line-row pb-3 text-base sm:pb-2',
              SIZE_GRID,
              !s.active && 'text-ink-2',
              s.menu_item_id === current.menu_item_id && 'sm:bg-brand-wash/40',
            )}
          >
            <span className="col-span-2 flex items-center gap-2 font-extrabold sm:col-span-1">
              {sizeLabel(s.size_code)}
              {!s.active && <span className="text-xs font-bold text-ink-2">off</span>}
            </span>
            <label className="flex items-center gap-1">
              <span className="text-xs font-bold text-ink-2 sm:hidden">Price</span>
              <span className="text-ink-2" aria-hidden="true">
                £
              </span>
              <Input
                size="sm"
                numeric
                aria-label={`Sell price ${sizeLabel(s.size_code)}`}
                changed={priceChanged}
                value={prices[s.menu_item_id] ?? ''}
                onChange={(e) =>
                  MONEY_INPUT.test(e.target.value) && setPrices((p) => ({ ...p, [s.menu_item_id]: e.target.value }))
                }
              />
            </label>
            <Fig label="Ingredients" est={s.cost.is_estimate} title={s.cost.is_missing ? (s.cost.note ?? 'cost unknown') : undefined}>
              {s.cost.is_missing || s.cost.pence === null ? 'unknown' : costText(s.cost)}
            </Fig>
            <Fig label="Margin" est={s.cost.is_estimate} alert={m !== null && m < 60}>
              {s.price_pence <= 0 ? 'no price' : m === null ? 'unknown' : pctText(m)}
            </Fig>
            <Fig label="Labour" est={Boolean(s.prep_is_estimate)}>
              {s.labour_cost_pence == null ? '—' : gbp(s.labour_cost_pence)}
            </Fig>
            <label className="flex items-center gap-1">
              <span className="text-xs font-bold text-ink-2 sm:hidden">Time</span>
              <Input
                size="sm"
                numeric
                inputMode="numeric"
                aria-label={`Time to make ${sizeLabel(s.size_code)}, seconds`}
                placeholder="untimed"
                est={Boolean(s.prep_is_estimate) && !timeChanged}
                changed={timeChanged}
                value={times[s.menu_item_id] ?? ''}
                onChange={(e) => /^\d*$/.test(e.target.value) && setTimes((t) => ({ ...t, [s.menu_item_id]: e.target.value }))}
              />
            </label>
            <Fig label="£ / min" est={s.cost.is_estimate || Boolean(s.prep_is_estimate)}>
              {s.margin_per_minute_pence == null ? '—' : gbp(s.margin_per_minute_pence)}
            </Fig>
          </div>
        )
      })}
      {missing.length > 0 && (
        <div className="flex flex-wrap items-center gap-1.5">
          <span className="text-sm text-ink-2">Add a size:</span>
          {missing.map((sz) => (
            <button
              key={sz}
              type="button"
              disabled={operator === null}
              onClick={async () => {
                if (!operator) return
                const r = await menuApi.addSize(current.menu_item_id, {
                  actor: operator,
                  size_code: sz,
                  price_pence: current.price_pence,
                  copy_from_menu_item_id: current.menu_item_id,
                })
                if (r.kind === 'ok') {
                  await invalidate()
                  if (r.data.menu_item_ids[0] !== undefined) openSize(r.data.menu_item_ids[0])
                } else setMsg(r.message)
              }}
              className="h-8 rounded-button border-[1.5px] border-dashed border-line-strong px-3 text-sm text-ink-2 hover:bg-canvas disabled:opacity-50"
            >
              + {sizeLabel(sz)}
            </button>
          ))}
          <span className="text-xs text-ink-2">copies this size&rsquo;s recipe and price</span>
        </div>
      )}
      <p className="text-xs text-ink-2">
        <em>Italic</em> figures rest on estimated prices or a time nobody has measured yet.
        {group.template_id !== null && ' An empty time uses the recipe’s time for that size; typing one here overrides it for this item only.'}
      </p>
      {timesDirty || timeError ? (
        <div className="flex flex-wrap items-center gap-2 rounded-card border border-line bg-canvas-2 px-3 py-2.5">
          {timeError ? (
            <span className="text-sm text-bad-ink">{timeError}</span>
          ) : (
            <span className="text-sm">
              New times change labour cost and £ per minute only; ingredient costs and prices stay as they are.
            </span>
          )}
          <Toggle checked={timesEst} onChange={setTimesEst} label="Still an estimate" />
          <span className="flex-1" />
          <Button variant="ghost" size="sm" onClick={() => setTimes(initialTimes)}>
            Discard
          </Button>
          <Button
            variant="primary"
            size="sm"
            pending={savingTimes}
            pendingLabel="Saving…"
            disabled={operator === null || timeError !== null}
            onClick={async () => {
              if (!operator) return
              setSavingTimes(true)
              const r = await menuApi.prep(current.menu_item_id, changedTimes, timesEst, operator)
              setSavingTimes(false)
              if (r.kind === 'ok') {
                setMsg(`Saved. ${r.data.summary}`)
                await invalidate()
              } else setMsg(r.message)
            }}
          >
            Save times
          </Button>
        </div>
      ) : null}
      {changedPrices.length > 0 && (
        <ImpactPanel
          title="What these prices do"
          status={cur?.status ?? { kind: 'loading' }}
          diff={cur?.data?.diff ?? []}
          figures={cur?.data ? impactFigures(cur.data.impact) : []}
          warnings={cur?.data?.impact.warnings ?? []}
          posActions={cur?.data?.pos_actions ?? []}
          note="New prices apply from today. Past sales keep the price they were rung at."
          onDiscard={() => setPrices(initialPrices)}
          applying={applying}
          ready={cur?.status.kind === 'ready'}
          onApply={async () => {
            if (!operator) return
            setApplying(true)
            const r = await menuApi.pricesApply(changedPrices, operator)
            setApplying(false)
            if (r.kind === 'ok') {
              setMsg(`Prices set from today. ${r.data.pos_actions.join(' ')}`)
              await invalidate()
            } else setMsg(r.message)
          }}
        />
      )}
      {msg && (
        <p role="status" className="text-sm">
          {msg}
        </p>
      )}
    </section>
  )
}

function Fig({
  label,
  children,
  est,
  alert,
  title,
}: {
  label: string
  children: React.ReactNode
  est?: boolean
  alert?: boolean
  title?: string
}) {
  return (
    <span className="flex items-baseline justify-between gap-2 sm:block sm:text-right" title={title}>
      <span className="text-xs font-bold text-ink-2 sm:hidden">{label}</span>
      <span className={cx('fig', est && 'italic', alert && 'font-bold text-alert')}>{children}</span>
    </span>
  )
}

/* -------------------------------------------------------------- recipe --- */

interface DLine {
  key: string
  ingredient_id: number | null
  qty: string
  unit: Unit | null
}

function factor(from: Unit, to: Unit): string {
  if (from === to) return '1'
  if ((from === 'ML' && to === 'L') || (from === 'G' && to === 'KG')) return '0.001'
  if ((from === 'L' && to === 'ML') || (from === 'KG' && to === 'G')) return '1000'
  return '0'
}

function lineCost(line: DLine, ing: IngredientRow | undefined): Cost | null {
  if (!ing || line.unit === null) return null
  const q = parseDec(line.qty || '0')
  if (q === null || ing.unit_cost.pence === null) return null
  const f = parseDec(factor(line.unit, ing.unit))
  if (f === null) return null
  return { ...ing.unit_cost, pence: decStr(mul(mul(q, f), fromMoney(ing.unit_cost.pence))) }
}

function RecipeSection({ detail }: { detail: MenuItemDetail }) {
  const g = detail.group
  const size = detail.size
  const profit = size.cost.pence !== null ? gbp(decStr(sub(fromInt(size.price_pence), fromMoney(size.cost.pence)))) : null
  return (
    <section aria-labelledby="mi-recipe" className="flex flex-col gap-3">
      <div className="flex flex-wrap items-baseline justify-between gap-2">
        <h2 id="mi-recipe" className="text-xl font-extrabold tracking-[-.01em]">
          What goes in
        </h2>
        <span className={cx('fig text-base text-ink-2', size.cost.is_estimate && 'italic')}>
          {sizeLabel(size.size_code)}: costs {costText(size.cost)}
          {profit ? ` · leaves ${profit}` : ''}
        </span>
      </div>
      {g.sizes.length > 1 && (
        <div className="flex flex-wrap gap-1.5" role="group" aria-label="Show the recipe for size">
          {g.sizes.map((s) => (
            <SizeTile
              key={s.menu_item_id}
              label={sizeLabel(s.size_code)}
              sub={s.active ? gbp(s.price_pence) : 'off'}
              active={s.menu_item_id === size.menu_item_id}
              onClick={() => openSize(s.menu_item_id)}
            />
          ))}
        </div>
      )}
      {g.template_id !== null && (
        <InfoPanel
          action={
            <a
              href={href('/menu/recipes', { t: g.template_id, from: size.menu_item_id })}
              className="inline-flex h-8 items-center rounded-control bg-surface px-3 text-base font-bold text-brand-ink no-underline"
            >
              Edit the {g.template_name} recipe ›
            </a>
          }
        >
          Made from the <strong>{g.template_name}</strong> recipe. Its ingredients per size, flavours, swaps, time to
          make and base price are set there, and every item made from it follows. Changes are previewed and apply from
          today.
        </InfoPanel>
      )}
      <div className="grid gap-5 md:grid-cols-[minmax(0,1fr)_300px]">
        <div className="min-w-0">
          {detail.editable_lines ? <LinesEditor key={size.menu_item_id} detail={detail} /> : <LinesReadOnly detail={detail} />}
        </div>
        <div className="min-w-0">
          <h3 className="mb-2 text-sm font-bold text-ink-2">Where the cost goes</h3>
          {detail.lines.length ? <CostDonut lines={detail.lines} /> : <p className="text-sm text-ink-2">No ingredients yet.</p>}
        </div>
      </div>
      {detail.estimate_names.length > 0 && <EstLine names={detail.estimate_names} total={detail.lines.length} />}
    </section>
  )
}

function LinesReadOnly({ detail }: { detail: MenuItemDetail }) {
  if (!detail.lines.length) return <p className="text-base text-ink-2">No ingredients on this size yet.</p>
  return (
    <ul className="flex flex-col">
      {detail.lines.map((l) => (
        <li key={l.ingredient_id} className="flex items-center gap-2 border-b border-line-row py-2 text-base last:border-b-0">
          <a href={href('/ingredients', { id: l.ingredient_id })} className="min-w-0 flex-1 truncate text-ink no-underline hover:underline">
            {l.ingredient_name}
          </a>
          <span className="fig text-ink-2">
            {qtyText(l.qty)} {unitWord(l.unit)}
          </span>
          <span className={cx('fig w-16 text-right font-bold', l.line_cost.is_estimate && 'italic')}>{costText(l.line_cost)}</span>
        </li>
      ))}
    </ul>
  )
}

function LinesEditor({ detail }: { detail: MenuItemDetail }) {
  const size = detail.size
  const ingredients = useIngredients()
  const fetchItem = useFetchMenuItem()
  const byId = useMemo(() => new Map((ingredients.data?.rows ?? []).map((r) => [r.ingredient_id, r])), [ingredients.data])
  const options = ingredients.data?.rows ?? []
  const saved = useMemo<DLine[]>(
    () => detail.lines.map((l, i) => ({ key: `s${i}`, ingredient_id: l.ingredient_id, qty: l.qty, unit: l.unit })),
    [detail.lines],
  )
  const [lines, setLines] = useState<DLine[]>(saved)
  const [copyAll, setCopyAll] = useState(false)
  const [fresh, setFresh] = useState<string | null>(null)
  const [operator] = useOperator()
  const [applying, setApplying] = useState(false)
  const [done, setDone] = useState<string | null>(null)
  const invalidate = useInvalidateMenu()
  useEffect(() => {
    setLines(saved)
    setCopyAll(false)
  }, [saved])

  const others = detail.group.sizes.filter((s) => s.menu_item_id !== size.menu_item_id && s.manual_recipe && s.active)
  const sources = detail.group.sizes.filter((s) => s.menu_item_id !== size.menu_item_id)
  const body: LineIn[] = []
  const errors: string[] = []
  for (const l of lines) {
    if (l.ingredient_id === null) {
      errors.push('pick an ingredient for every line')
      continue
    }
    const q = qtyOut(l.qty)
    if (q === null || q === '0') errors.push(`${byId.get(l.ingredient_id)?.name ?? 'a line'}: enter a quantity`)
    else body.push({ ingredient_id: l.ingredient_id, qty: q, unit: l.unit })
  }
  const same =
    !copyAll &&
    lines.length === saved.length &&
    lines.every((l, i) => {
      const s = saved[i]
      return s && s.ingredient_id === l.ingredient_id && s.unit === l.unit && sameQty(s.qty, l.qty)
    })
  const also = copyAll ? others.map((o) => o.menu_item_id) : []
  const set = (i: number, patch: Partial<DLine>) => setLines((ls) => ls.map((x, j) => (j === i ? { ...x, ...patch } : x)))

  const copyFrom = async (s: MenuSize) => {
    setDone(null)
    try {
      const other = await fetchItem(s.menu_item_id)
      if (!other.lines.length) {
        setDone(`Size ${sizeLabel(s.size_code)} has no ingredients to copy.`)
        return
      }
      const stamp = Date.now()
      setLines(other.lines.map((l, i) => ({ key: `c${stamp}-${i}`, ingredient_id: l.ingredient_id, qty: l.qty, unit: l.unit })))
      setDone(`Copied ${other.lines.length} ingredients from ${sizeLabel(s.size_code)}. Adjust the amounts, then save.`)
    } catch {
      setDone(`Could not load size ${sizeLabel(s.size_code)}.`)
    }
  }

  return (
    <div className="flex flex-col gap-2">
      {sources.length > 0 && (
        <div className="flex flex-wrap items-center gap-1.5 pb-1">
          <span className="text-sm text-ink-2">Copy ingredients from</span>
          {sources.map((s) => (
            <button
              key={s.menu_item_id}
              type="button"
              onClick={() => void copyFrom(s)}
              className="h-8 rounded-button border border-line-control px-3 text-sm font-bold text-brand-ink hover:bg-brand-wash"
            >
              {sizeLabel(s.size_code)}
            </button>
          ))}
        </div>
      )}
      {lines.length === 0 && <p className="text-base text-ink-2">No ingredients yet. Add what goes into one {sizeLabel(size.size_code)}.</p>}
      {lines.length > 0 && (
        <div className="hidden gap-2 border-b border-line pb-1.5 text-label font-bold uppercase tracking-[.06em] text-ink-3 sm:grid sm:grid-cols-[minmax(0,1fr)_96px_84px_80px_40px]">
          <span>Ingredient</span>
          <span className="text-right">Amount</span>
          <span>Unit</span>
          <span className="text-right">Cost</span>
          <span />
        </div>
      )}
      {lines.map((l, i) => {
        const ing = l.ingredient_id !== null ? byId.get(l.ingredient_id) : undefined
        const cost = lineCost(l, ing)
        const units = ing ? compatibleUnits(ing.unit) : (['EACH'] as Unit[])
        const inRecipe = new Set(lines.filter((_, j) => j !== i).flatMap((x) => (x.ingredient_id === null ? [] : [x.ingredient_id])))
        return (
          <div
            key={l.key}
            className="grid grid-cols-[minmax(0,1fr)_auto] items-center gap-2 border-b border-line-row pb-2 sm:grid-cols-[minmax(0,1fr)_96px_84px_80px_40px]"
          >
            <IngredientPicker
              value={l.ingredient_id}
              options={options}
              inRecipe={inRecipe}
              autoOpen={l.key === fresh}
              onPick={(id) => set(i, { ingredient_id: id, unit: byId.get(id)?.unit ?? null })}
            />
            <IconButton label="Remove line" onClick={() => setLines((ls) => ls.filter((_, j) => j !== i))} className="sm:order-last" />
            <div className="col-span-2 flex items-center gap-2 sm:contents">
              <Input
                numeric
                size="sm"
                aria-label="Quantity"
                value={l.qty}
                onChange={(e) => QTY_INPUT.test(e.target.value) && set(i, { qty: e.target.value })}
                className="w-[96px]"
              />
              <Select aria-label="Unit" value={l.unit ?? ''} onChange={(e) => set(i, { unit: e.target.value as Unit })} className="w-[84px]">
                {units.map((u) => (
                  <option key={u} value={u}>
                    {unitWord(u)}
                  </option>
                ))}
              </Select>
              <span className="flex-1 sm:hidden" />
              <span
                className={cx('fig text-right text-base font-bold', cost?.is_estimate && 'italic')}
                title={ing && ing.unit_cost.pence !== null ? `${unitPrice(ing.unit_cost.pence)} per ${unitWord(ing.unit)}` : undefined}
              >
                {cost ? costText(cost) : ing ? 'no price' : '—'}
              </span>
            </div>
          </div>
        )
      })}
      <div className="flex flex-wrap gap-2">
        <Button
          variant="add"
          onClick={() => {
            const k = `n${Date.now()}`
            setFresh(k)
            setLines((ls) => [...ls, { key: k, ingredient_id: null, qty: '1', unit: null }])
          }}
        >
          + Add ingredient
        </Button>
        {others.length > 0 && (
          <Button variant="ghost" aria-pressed={copyAll} onClick={() => setCopyAll((v) => !v)} className={cx(copyAll && 'text-brand-ink')}>
            {copyAll ? `Also saving to ${others.map((o) => sizeLabel(o.size_code)).join(', ')}` : 'Same for other sizes'}
          </Button>
        )}
      </div>
      {done && (
        <p role="status" className="text-sm">
          {done}
        </p>
      )}
      {!same && (
        <div className="sticky bottom-0 z-10 -mx-1 mt-1 flex flex-wrap items-center gap-2 rounded-card border border-line bg-surface px-3 py-2.5 shadow-login">
          <span className={cx('min-w-0 flex-1 text-sm', errors.length ? 'text-bad-ink' : 'text-ink-2')}>
            {errors.length ? [...new Set(errors)].join('; ') : 'Unsaved changes. They apply from today; past sales keep their recipe.'}
          </span>
          <Button
            variant="ghost"
            size="sm"
            onClick={() => {
              setLines(saved)
              setCopyAll(false)
              setDone(null)
            }}
          >
            Discard
          </Button>
          <Button
            variant="primary"
            size="sm"
            pending={applying}
            pendingLabel="Saving…"
            disabled={operator === null || errors.length > 0}
            onClick={async () => {
              if (!operator) return
              setApplying(true)
              const r = await menuApi.linesApply(size.menu_item_id, body, also, operator)
              setApplying(false)
              if (r.kind === 'ok') {
                setDone('Recipe saved.')
                await invalidate()
              } else setDone(r.message)
            }}
          >
            Save recipe
          </Button>
        </div>
      )}
    </div>
  )
}

function EstLine({ names, total }: { names: string[]; total: number }) {
  return (
    <div className="rounded-control bg-est-wash px-3 py-2 text-sm text-ink-2">
      <em>Italic</em> costs are estimates: {names.length} of {total} ingredients here ({names.join(', ')}).
    </div>
  )
}

/* ------------------------------------------------------------- actions --- */

function Actions({ detail }: { detail: MenuItemDetail }) {
  const g = detail.group
  const size = detail.size
  const [operator] = useOperator()
  const invalidate = useInvalidateMenu()
  const [note, setNote] = useState<string | null>(null)
  const act = async (p: Promise<{ kind: string; message?: string; data?: { menu_item_ids: number[] } }>, then?: (ids: number[]) => void) => {
    const r = await p
    if (r.kind === 'ok') {
      await invalidate()
      if (then && r.data) then(r.data.menu_item_ids)
      setNote(null)
    } else setNote(r.message ?? 'That did not work.')
  }
  return (
    <div className="flex flex-col gap-2">
      <div className="flex flex-col gap-2 [&>button]:w-full">
        <Button
          disabled={operator === null}
          onClick={() => act(menuApi.duplicate(size.menu_item_id, operator ?? ''), (ids) => ids[0] !== undefined && navigate(`/menu/${ids[0]}`))}
        >
          Duplicate as a new item
        </Button>
        {g.sizes.length > 1 && (
          <Button disabled={operator === null || !size.active} onClick={() => act(menuApi.removeSize(size.menu_item_id, operator ?? ''))}>
            Take size {sizeLabel(size.size_code)} off
          </Button>
        )}
        <Button
          variant="danger-soft"
          disabled={operator === null}
          onClick={() => act(menuApi.group(size.menu_item_id, { actor: operator ?? '', active: !g.is_active }))}
        >
          {g.is_active ? 'Take off the menu' : 'Back on the menu'}
        </Button>
      </div>
      {note && (
        <p role="alert" className="text-sm text-bad-ink">
          {note}
        </p>
      )}
      <p className="text-xs text-ink-2">Nothing is deleted: sales and prices keep pointing at every size that ever sold.</p>
    </div>
  )
}
