/**
 * One product in the Menu items drawer (spec §V2.3).
 *
 * - Name, category, note and on/off apply to every size of the product; they
 *   are labels, not recipe or price, so they save directly.
 * - Sell price: dated (`menu_item_price`), previewed, applied from today.
 * - Recipe lines: editable only for a one-off item (spec C-2); previewed and
 *   applied from today. A recipe item shows its resolved lines read-only and
 *   points at its recipe.
 * - Nothing is deleted (spec C-5): "Take size off" and "Take off the menu".
 */
import { useEffect, useMemo, useState } from 'react'
import {
  Button,
  Drawer,
  ErrorBox,
  IconButton,
  InfoPanel,
  Input,
  Loading,
  Select,
  SizeTile,
  Toggle,
  cx,
} from '../../components/ui'
import { OperatorNeeded } from '../../components/shell/Operator'
import { fromInt, fromMoney, mul, parseDec, sub } from '../../lib/dec'
import { menuApi, useIngredients, useInvalidateMenu, useMenuItem } from '../../lib/menu-api'
import { useOperator } from '../../lib/operator'
import { href } from '../../lib/router'
import type {
  Cost,
  IngredientRow,
  LineIn,
  LinesPreview,
  MenuCategory,
  MenuGroup,
  MenuItemDetail,
  PricesPreview,
  SizeCode,
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
import { PhotoSlot } from './Photo'

const KIND_LABEL = { DRINKS: 'Drinks', FOOD: 'Food', OTHER: 'Other' } as const

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
  const value = mul(mul(q, f), fromMoney(ing.unit_cost.pence))
  return { ...ing.unit_cost, pence: decStr(value) }
}

export function ItemDrawer({
  menuItemId,
  categories,
  onClose,
  onOpen,
}: {
  menuItemId: number
  categories: MenuCategory[]
  onClose: () => void
  onOpen: (id: number) => void
}) {
  const detail = useMenuItem(menuItemId)
  const g = detail.data?.group
  const title = g ? `${KIND_LABEL[g.kind]} · ${g.category || 'No category'}` : 'Menu item'
  return (
    <Drawer
      open
      onClose={onClose}
      title={<span className="text-sm font-normal text-ink-2">{title}</span>}
      width={420}
      compactWidth={380}
    >
      {detail.isLoading && <Loading what="Loading the item" />}
      {detail.error && <ErrorBox error={detail.error} what="this item" />}
      {detail.data && (
        <DrawerBody key={detail.data.size.menu_item_id} detail={detail.data} categories={categories} onOpen={onOpen} />
      )}
    </Drawer>
  )
}

function DrawerBody({
  detail,
  categories,
  onOpen,
}: {
  detail: MenuItemDetail
  categories: MenuCategory[]
  onOpen: (id: number) => void
}) {
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

  const present = new Set(g.sizes.map((s) => s.size_code ?? 'ONE'))
  const missing = g.template_id === null ? SIZE_ORDER.filter((s) => !present.has(s)) : []

  return (
    <>
      <PhotoSlot menuItemId={size.menu_item_id} url={g.photo_url} name={g.name} />
      <MetaSection group={g} categories={categories} />
      {g.template_id !== null && (
        <InfoPanel
          action={
            <a
              href={href('/recipes', { t: g.template_id })}
              className="inline-flex h-8 items-center rounded-control bg-surface px-3 text-base font-bold text-brand-ink no-underline"
            >
              Open recipe
            </a>
          }
        >
          Made from the {g.template_name} recipe. Change it there.
        </InfoPanel>
      )}

      <section className="flex flex-col gap-2.5" aria-labelledby="mi-sizes">
        <h3 id="mi-sizes" className="text-lg font-extrabold">
          Sizes and price
        </h3>
        <div className="flex flex-wrap gap-1.5">
          {g.sizes.map((s) => (
            <SizeTile
              key={s.menu_item_id}
              label={sizeLabel(s.size_code)}
              sub={s.active ? gbp(s.price_pence) : 'off'}
              active={s.menu_item_id === size.menu_item_id}
              onClick={() => onOpen(s.menu_item_id)}
            />
          ))}
          {missing.map((s) => (
            <button
              key={s}
              type="button"
              disabled={operator === null}
              onClick={() =>
                act(
                  menuApi.addSize(size.menu_item_id, {
                    actor: operator ?? '',
                    size_code: s,
                    price_pence: size.price_pence,
                    copy_from_menu_item_id: size.menu_item_id,
                  }),
                  (ids) => ids[0] !== undefined && onOpen(ids[0]),
                )
              }
              className="h-[52px] min-w-[52px] rounded-button border-[1.5px] border-dashed border-line-strong px-2.5 text-sm text-ink-2 hover:bg-canvas disabled:opacity-50"
            >
              + {sizeLabel(s)}
            </button>
          ))}
        </div>
        <PriceTiles detail={detail} />
      </section>

      <LinesSection detail={detail} />

      <section className="flex flex-col gap-1" aria-labelledby="mi-all">
        <h3 id="mi-all" className="mb-1 text-lg font-extrabold">
          All sizes
        </h3>
        <div className="grid grid-cols-[48px_repeat(4,minmax(0,1fr))] gap-2 border-b border-line-soft py-1.5 text-label font-bold uppercase tracking-[.06em] text-ink-3">
          <span>Size</span>
          <span className="text-right">Price</span>
          <span className="text-right">Cost</span>
          <span className="text-right">Profit</span>
          <span className="text-right">Margin</span>
        </div>
        {g.sizes.map((s) => {
          const m = marginPct(s.price_pence, s.cost)
          const profit = s.cost.pence !== null ? decStr(sub(fromInt(s.price_pence), fromMoney(s.cost.pence))) : null
          return (
            <div
              key={s.menu_item_id}
              className={cx('grid grid-cols-[48px_repeat(4,minmax(0,1fr))] gap-2 border-b border-line-row py-2 text-base', !s.active && 'text-ink-2')}
            >
              <span className="font-bold">{sizeLabel(s.size_code)}</span>
              <span className="fig text-right">{gbp(s.price_pence)}</span>
              <span className={cx('fig text-right', s.cost.is_estimate && 'italic')}>{costText(s.cost)}</span>
              <span className={cx('fig text-right', s.cost.is_estimate && 'italic')}>{profit ? gbp(profit) : '—'}</span>
              <span className={cx('fig text-right font-bold', m !== null && m < 60 && 'text-alert', s.cost.is_estimate && 'italic')}>
                {s.price_pence <= 0 ? 'no price' : m === null ? '—' : pctText(m)}
              </span>
            </div>
          )
        })}
        <p className="mt-1 text-xs text-ink-2">Margins under 60% are marked. Italic figures rest on estimated prices.</p>
      </section>

      <div className="flex gap-2 border-t border-line-soft pt-3.5">
        <Button
          className="flex-1"
          disabled={operator === null}
          onClick={() => act(menuApi.duplicate(size.menu_item_id, operator ?? ''), (ids) => ids[0] !== undefined && onOpen(ids[0]))}
        >
          Duplicate
        </Button>
        <Button
          className="flex-1"
          disabled={operator === null || !size.active}
          onClick={() => act(menuApi.removeSize(size.menu_item_id, operator ?? ''))}
        >
          Take size off
        </Button>
        <Button
          variant="danger-soft"
          className="flex-1"
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
      <OperatorNeeded what="change this item" />
      <p className="text-xs text-ink-2">
        Nothing here is deleted: sales and prices keep pointing at every size that ever sold.
      </p>
    </>
  )
}

function MetaSection({ group, categories }: { group: MenuGroup; categories: MenuCategory[] }) {
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
  }, [group])
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
  const names = [...new Set([...categories.map((c) => c.name).filter(Boolean), group.category ?? ''])].sort((a, b) =>
    a.localeCompare(b),
  )
  return (
    <div className="flex flex-col gap-2.5">
      <input
        aria-label="Item name"
        value={name}
        onChange={(e) => setName(e.target.value)}
        className="w-full border-b border-transparent bg-transparent p-0 text-2xl font-extrabold tracking-[-.01em] outline-none focus-visible:border-brand"
      />
      <Toggle
        checked={group.is_active}
        onChange={(v) => save({ active: v })}
        disabled={operator === null || busy}
        label={group.is_active ? 'On the menu' : 'Off the menu'}
      />
      <div className="grid grid-cols-2 gap-2">
        <label className="flex min-w-0 flex-col gap-1 text-xs font-bold text-ink-2">
          Category
          <Select value={cat} onChange={(e) => setCat(e.target.value)} className="font-normal text-ink">
            <option value="">No category</option>
            {names
              .filter(Boolean)
              .map((c) => (
                <option key={c} value={c}>
                  {c.replace(' (May 2026)', '')}
                </option>
              ))}
          </Select>
        </label>
        <label className="flex min-w-0 flex-col gap-1 text-xs font-bold text-ink-2">
          Note
          <Input value={note} placeholder="e.g. CakeSmiths" onChange={(e) => setNote(e.target.value)} className="font-normal" />
        </label>
      </div>
      {!group.on_till && (
        <p className="text-sm text-ink-2">Not on the till yet: its sales are not counted until it is matched in Lightspeed.</p>
      )}
      {dirty && (
        <div className="flex items-center gap-2">
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
            Save name, category and note
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
        </div>
      )}
      {name.trim() !== group.name && (
        <p className="text-sm text-ink-2">Renaming changes the name here only; the till keeps its own name.</p>
      )}
      {msg && (
        <p role="alert" className="text-sm text-bad-ink">
          {msg}
        </p>
      )}
    </div>
  )
}

function PriceTiles({ detail }: { detail: MenuItemDetail }) {
  const size = detail.size
  const [price, setPrice] = useState(penceToPounds(size.price_pence))
  const [operator] = useOperator()
  const [applying, setApplying] = useState(false)
  const [done, setDone] = useState<string | null>(null)
  const invalidate = useInvalidateMenu()
  useEffect(() => setPrice(penceToPounds(size.price_pence)), [size.price_pence])
  const pence = poundsToPence(price)
  const changed = pence !== null && pence !== size.price_pence
  const key = changed ? JSON.stringify([size.menu_item_id, pence]) : null
  const pv = usePreview<PricesPreview>(key, () => menuApi.pricesPreview([{ menu_item_id: size.menu_item_id, price_pence: pence ?? 0 }]))
  const cur = pv.key === key ? pv : null
  const m = marginPct(size.price_pence, size.cost)
  const tone = size.price_pence <= 0 || m === null ? 'plain' : m < 60 ? 'bad' : 'ok'
  return (
    <>
      <div className="grid grid-cols-3 gap-2">
        <label className="flex min-w-0 flex-col gap-1 rounded-button bg-canvas px-3 py-2.5 text-xs font-bold text-ink-2 focus-within:ring-2 focus-within:ring-brand">
          Sell price
          <span className="flex items-center gap-0.5 text-xl font-extrabold text-ink">
            £
            <input
              aria-label={`Sell price ${sizeLabel(size.size_code)}`}
              value={price}
              inputMode="decimal"
              onChange={(e) => MONEY_INPUT.test(e.target.value) && setPrice(e.target.value)}
              className={cx('fig w-full min-w-0 bg-transparent p-0 outline-none', changed && 'text-alert')}
            />
          </span>
        </label>
        <div className="flex min-w-0 flex-col gap-1 rounded-button bg-canvas px-3 py-2.5 text-xs font-bold text-ink-2">
          Costs us
          <span className={cx('fig truncate text-xl font-extrabold text-ink', size.cost.is_estimate && 'italic')}>
            {costText(size.cost)}
          </span>
        </div>
        <div
          className={cx(
            'flex min-w-0 flex-col gap-1 rounded-button px-3 py-2.5 text-xs font-bold',
            tone === 'plain' && 'bg-canvas text-ink-2',
            tone === 'bad' && 'bg-bad-wash text-bad-ink',
            tone === 'ok' && 'bg-ok-wash text-ok-ink',
          )}
        >
          Margin
          <span className={cx('fig truncate text-xl font-extrabold', size.cost.is_estimate && 'italic')}>
            {size.price_pence <= 0 ? 'no price' : m === null ? 'cost unknown' : pctText(m)}
          </span>
        </div>
      </div>
      {size.cost.is_missing && size.cost.note && <p className="text-sm text-ink-2">Cost unknown: {size.cost.note}</p>}
      {done && <p role="status" className="text-sm">{done}</p>}
      {changed && (
        <ImpactPanel
          title="What this price does"
          status={cur?.status ?? { kind: 'loading' }}
          diff={cur?.data?.diff ?? []}
          figures={cur?.data ? impactFigures(cur.data.impact) : []}
          warnings={cur?.data?.impact.warnings ?? []}
          posActions={cur?.data?.pos_actions ?? []}
          note="The new price applies from today. Past sales keep the price they were rung at."
          onDiscard={() => setPrice(penceToPounds(size.price_pence))}
          applying={applying}
          ready={cur?.status.kind === 'ready'}
          operatorWhat="change a price"
          onApply={async () => {
            if (!operator || pence === null) return
            setApplying(true)
            const r = await menuApi.pricesApply([{ menu_item_id: size.menu_item_id, price_pence: pence }], operator)
            setApplying(false)
            if (r.kind === 'ok') {
              setDone(`Price set from today. ${r.data.pos_actions.join(' ')}`)
              await invalidate()
            } else setDone(r.message)
          }}
        />
      )}
    </>
  )
}

function LinesSection({ detail }: { detail: MenuItemDetail }) {
  const size = detail.size
  const ingredients = useIngredients()
  const byId = useMemo(() => new Map((ingredients.data?.rows ?? []).map((r) => [r.ingredient_id, r])), [ingredients.data])
  const options = useMemo(
    () => [...(ingredients.data?.rows ?? [])].sort((a, b) => a.name.localeCompare(b.name)),
    [ingredients.data],
  )
  const saved = useMemo<DLine[]>(
    () => detail.lines.map((l, i) => ({ key: `s${i}`, ingredient_id: l.ingredient_id, qty: l.qty, unit: l.unit })),
    [detail.lines],
  )
  const [lines, setLines] = useState<DLine[]>(saved)
  const [copyAll, setCopyAll] = useState(false)
  const [operator] = useOperator()
  const [applying, setApplying] = useState(false)
  const [done, setDone] = useState<string | null>(null)
  const invalidate = useInvalidateMenu()
  useEffect(() => {
    setLines(saved)
    setCopyAll(false)
  }, [saved])

  const others = detail.group.sizes.filter((s) => s.menu_item_id !== size.menu_item_id && s.manual_recipe && s.active)
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
  const key = !same && errors.length === 0 ? JSON.stringify({ id: size.menu_item_id, body, also }) : null
  const pv = usePreview<LinesPreview>(key, () => menuApi.linesPreview(size.menu_item_id, body, also))
  const cur = pv.key === key ? pv : null

  const total = detail.size.cost
  const profit = total.pence !== null ? gbp(decStr(sub(fromInt(size.price_pence), fromMoney(total.pence)))) : '—'
  const estNames = detail.estimate_names

  if (!detail.editable_lines) {
    return (
      <section className="flex flex-col gap-2" aria-labelledby="mi-recipe">
        <div className="flex items-baseline gap-2">
          <h3 id="mi-recipe" className="flex-1 text-lg font-extrabold">
            Recipe · {sizeLabel(size.size_code)}
          </h3>
          <span className="text-sm text-ink-2">profit {profit}</span>
        </div>
        {detail.lines.map((l) => (
          <div key={l.ingredient_id} className="flex items-center gap-2 rounded-button border border-line-soft p-2.5 text-base">
            <span className="min-w-0 flex-1 truncate">{l.ingredient_name}</span>
            <span className="fig text-ink-2">
              {qtyText(l.qty)} {unitWord(l.unit)}
            </span>
            <span className={cx('fig w-16 text-right font-bold', l.line_cost.is_estimate && 'italic')}>{costText(l.line_cost)}</span>
          </div>
        ))}
        {estNames.length > 0 && <EstLine names={estNames} total={detail.lines.length} />}
      </section>
    )
  }

  return (
    <section className="flex flex-col gap-2" aria-labelledby="mi-recipe">
      <div className="flex items-baseline gap-2">
        <h3 id="mi-recipe" className="flex-1 text-lg font-extrabold">
          Recipe · {sizeLabel(size.size_code)}
        </h3>
        <span className="text-sm text-ink-2">profit {profit}</span>
      </div>
      {lines.map((l, i) => {
        const ing = l.ingredient_id !== null ? byId.get(l.ingredient_id) : undefined
        const cost = lineCost(l, ing)
        const units = ing ? compatibleUnits(ing.unit) : (['EACH'] as Unit[])
        return (
          <div key={l.key} className="flex flex-col gap-2 rounded-button border border-line-soft p-2.5">
            <div className="flex items-center gap-2">
              <Select
                aria-label="Ingredient"
                value={l.ingredient_id === null ? '' : String(l.ingredient_id)}
                onChange={(e) => {
                  const id = e.target.value === '' ? null : Number(e.target.value)
                  const next = id !== null ? byId.get(id) : undefined
                  setLines((ls) => ls.map((x, j) => (j === i ? { ...x, ingredient_id: id, unit: next?.unit ?? null } : x)))
                }}
              >
                <option value="">— pick ingredient —</option>
                {options.map((o) => (
                  <option key={o.ingredient_id} value={o.ingredient_id}>
                    {o.name}
                  </option>
                ))}
              </Select>
              <IconButton label="Remove line" onClick={() => setLines((ls) => ls.filter((_, j) => j !== i))} />
            </div>
            <div className="flex items-center gap-2">
              <div className="w-[84px] flex-none">
                <Input
                  numeric
                  aria-label="Quantity"
                  value={l.qty}
                  onChange={(e) =>
                    QTY_INPUT.test(e.target.value) && setLines((ls) => ls.map((x, j) => (j === i ? { ...x, qty: e.target.value } : x)))
                  }
                />
              </div>
              <div className="w-[76px] flex-none">
                <Select
                  aria-label="Unit"
                  value={l.unit ?? ''}
                  onChange={(e) => setLines((ls) => ls.map((x, j) => (j === i ? { ...x, unit: e.target.value as Unit } : x)))}
                >
                  {units.map((u) => (
                    <option key={u} value={u}>
                      {unitWord(u)}
                    </option>
                  ))}
                </Select>
              </div>
              <span className={cx('min-w-0 flex-1 truncate text-xs text-ink-2', ing?.unit_cost.is_estimate && 'italic')}>
                {ing && ing.unit_cost.pence !== null ? `${unitPrice(ing.unit_cost.pence)}/${unitWord(ing.unit)}` : ing ? 'no price' : ''}
              </span>
              <span className={cx('fig text-md font-bold', cost?.is_estimate && 'italic')}>{cost ? costText(cost) : '—'}</span>
            </div>
          </div>
        )
      })}
      <div className="flex flex-wrap gap-2">
        <Button
          variant="add"
          onClick={() => setLines((ls) => [...ls, { key: `n${Date.now()}`, ingredient_id: null, qty: '1', unit: null }])}
        >
          + Add ingredient
        </Button>
        {others.length > 0 && (
          <Button variant="ghost" aria-pressed={copyAll} onClick={() => setCopyAll((v) => !v)} className={cx(copyAll && 'text-brand-ink')}>
            {copyAll ? `Copying to ${others.map((o) => sizeLabel(o.size_code)).join(', ')}` : 'Copy to other sizes'}
          </Button>
        )}
      </div>
      {estNames.length > 0 && <EstLine names={estNames} total={detail.lines.length} />}
      {done && <p role="status" className="text-sm">{done}</p>}
      {(!same || errors.length > 0) && (
        <ImpactPanel
          status={errors.length ? { kind: 'idle' } : (cur?.status ?? { kind: 'loading' })}
          diff={cur?.data?.diff ?? []}
          figures={cur?.data ? impactFigures(cur.data.impact) : []}
          warnings={[...errors, ...(cur?.data?.impact.warnings ?? [])]}
          onDiscard={() => {
            setLines(saved)
            setCopyAll(false)
          }}
          applying={applying}
          ready={errors.length === 0 && cur?.status.kind === 'ready'}
          operatorWhat="change this recipe"
          onApply={async () => {
            if (!operator) return
            setApplying(true)
            const r = await menuApi.linesApply(size.menu_item_id, body, also, operator)
            setApplying(false)
            if (r.kind === 'ok') {
              setDone('Recipe applied from today.')
              await invalidate()
            } else setDone(r.message)
          }}
        />
      )}
    </section>
  )
}

function EstLine({ names, total }: { names: string[]; total: number }) {
  return (
    <div className="rounded-control bg-est-wash px-3 py-2 text-sm text-ink-2">
      <em>Italic</em> costs are estimates. {names.length} of {total} ingredients here: {names.join(', ')}.
    </div>
  )
}

export function sizeSort(a: SizeCode | null, b: SizeCode | null): number {
  return SIZE_ORDER.indexOf(a ?? 'ONE') - SIZE_ORDER.indexOf(b ?? 'ONE')
}
