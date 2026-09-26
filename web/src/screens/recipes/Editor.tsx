/**
 * One recipe: what goes in per size, time and base price, flavours, swaps,
 * history, and the cost column with the mandatory impact preview.
 * recipes-menu-ingredients.md §V1.4–§V1.10, §A2.
 *
 * State machine (§V1.10): clean → any edit → dirty (local draft, nothing
 * saved, changed cells red) → the server previews the changeset →
 * "Apply from today" (only while that preview is for exactly this draft) →
 * clean. Discard drops the draft. A 409 (somebody else changed the recipe)
 * keeps the draft and offers a reload.
 */
import { useEffect, useMemo, useState } from 'react'
import { Button, IconButton, Input, Select, Toggle, cx } from '../../components/ui'
import { useOperator } from '../../lib/operator'
import { recipeApi, useIngredients, useInvalidateMenu, useSeasons } from '../../lib/menu-api'
import type {
  ChangeItem,
  ChangesetPreview,
  ComponentRole,
  Cost,
  EditorItem,
  IngredientRow,
  RecipeEditor,
  SizeCode,
} from '../../lib/types/menu'
import { fromInt, fromMoney, sub } from '../../lib/dec'
import {
  MONEY_INPUT,
  QTY_INPUT,
  costText,
  dayLabel,
  decStr,
  gbp,
  labourPence,
  marginPct,
  penceToPounds,
  perMinute,
  poundsToPence,
  ratioPct,
  sizeLabel,
  unitWord,
} from '../menu/common/figures'
import { ImpactPanel, impactFigures } from '../menu/common/Impact'
import { usePreview } from '../menu/common/usePreview'
import { ROLES, buildOps, cellChanged, draftFrom, groupRows } from './model'
import type { DComp, DOption, Draft, Row } from './model'
import { SwapsSection } from './Swaps'

const FLAVOUR_CATS = new Set(['Syrup', 'Specialty', 'Chocolate', 'Tea'])

export function RecipeEditorView({
  editor,
  onDirtyChange,
  onReload,
}: {
  editor: RecipeEditor
  onDirtyChange: (dirty: boolean) => void
  onReload: () => void
}) {
  const saved = useMemo(() => draftFrom(editor), [editor])
  const [draft, setDraft] = useState<Draft>(saved)
  const [result, setResult] = useState<{ tone: 'ok' | 'bad'; text: string; pos: string[] } | null>(null)
  const [applying, setApplying] = useState(false)
  const [operator] = useOperator()
  const invalidate = useInvalidateMenu()
  const ingredients = useIngredients()
  const seasons = useSeasons()
  const sizes = editor.sizes

  useEffect(() => setDraft(saved), [saved])

  const built = useMemo(() => buildOps(saved, draft, sizes), [saved, draft, sizes])
  const dirty = built.ops.length > 0 || built.errors.length > 0
  useEffect(() => onDirtyChange(dirty), [dirty, onDirtyChange])

  const body = { base_version: editor.version, ops: built.ops }
  const key = built.ops.length > 0 && built.errors.length === 0 ? JSON.stringify(body) : null
  const preview = usePreview<ChangesetPreview>(key, () => recipeApi.preview(editor.template_id, body))
  const current = preview.key === key ? preview : null
  const pv = current?.data ?? null
  const ready = current?.status.kind === 'ready' && pv !== null && pv.refusals.length === 0

  const ingById = useMemo(() => {
    const m = new Map<number, IngredientRow>()
    for (const r of ingredients.data?.rows ?? []) m.set(r.ingredient_id, r)
    return m
  }, [ingredients.data])
  const ingOptions = useMemo(
    () => [...(ingredients.data?.rows ?? [])].sort((a, b) => a.name.localeCompare(b.name)),
    [ingredients.data],
  )
  const flavourOptions = ingOptions.filter((r) => FLAVOUR_CATS.has(r.category ?? '') || r.name === 'Honey')

  const set = (fn: (d: Draft) => Draft) => {
    setResult(null)
    setDraft((d) => fn(d))
  }
  const setComp = (key: string, fn: (c: DComp) => DComp) =>
    set((d) => ({ ...d, comps: d.comps.map((c) => (c.key === key ? fn(c) : c)) }))
  const setOpt = (key: string, fn: (o: DOption) => DOption) =>
    set((d) => ({ ...d, options: d.options.map((o) => (o.key === key ? fn(o) : o)) }))

  const apply = async () => {
    if (!operator || !ready) return
    setApplying(true)
    const r = await recipeApi.apply(editor.template_id, { ...body, actor: operator })
    setApplying(false)
    if (r.kind === 'ok') {
      setResult({
        tone: 'ok',
        text: `Applied from today: ${r.data.summary}. ${r.data.rollup_items_recosted} item cost(s) recalculated.`,
        pos: r.data.pos_actions,
      })
      await invalidate()
      return
    }
    setResult({ tone: 'bad', text: r.message, pos: [] })
  }

  const activeOptions = draft.options.filter((o) => !o.removed)
  const flavourCount = activeOptions.filter((o) => o.active).length
  const rows = groupRows(draft.comps, sizes)
  const hasFlavourAxis = editor.axes.length > 0

  return (
    <div className="min-h-0 flex-1 overflow-y-auto compact:flex compact:overflow-hidden">
      {/* ------------------------------------------------ editor column --- */}
      <div className="min-w-0 px-4 pb-8 pt-5 sm:px-[22px] compact:flex-1 compact:overflow-y-auto">
        <div className="flex flex-wrap items-baseline gap-x-2.5 gap-y-1">
          <input
            aria-label="Recipe name"
            value={draft.name}
            onChange={(e) => set((d) => ({ ...d, name: e.target.value }))}
            className="min-w-0 flex-1 border-b border-line bg-transparent py-0.5 text-3xl font-extrabold tracking-[-.01em] outline-none focus-visible:border-brand"
          />
          {editor.category && <span className="whitespace-nowrap text-base text-ink-2">{editor.category}</span>}
        </div>
        <p className="mb-4 mt-1 text-base text-ink-2">
          Makes {flavourCount * sizes.length} menu items: {flavourCount} flavour{flavourCount === 1 ? '' : 's'} ×{' '}
          {sizes.length} size{sizes.length === 1 ? '' : 's'} ({sizes.map(sizeLabel).join(', ')})
        </p>

        {/* What goes in, per size */}
        <section className="mb-[26px] flex flex-col gap-2.5" aria-labelledby="rec-in">
          <h2 id="rec-in" className="text-lg font-extrabold tracking-[-.01em]">
            What goes in, per size
          </h2>
          {rows.map((row) => (
            <ComponentCard
              key={row.key}
              row={row}
              sizes={sizes}
              saved={saved}
              ingOptions={ingOptions}
              ingById={ingById}
              onRole={(role) => row.comps.forEach((c) => setComp(c.key, (x) => ({ ...x, role })))}
              onIngredient={(key, id) => setComp(key, (x) => ({ ...x, ingredient_id: id }))}
              onQty={(key, size, v) => setComp(key, (x) => ({ ...x, qty: { ...x.qty, [size]: v } }))}
              onSubst={(v) => row.comps.forEach((c) => setComp(c.key, (x) => ({ ...x, subst: v })))}
              onRemove={() =>
                set((d) => ({ ...d, comps: d.comps.filter((c) => !row.comps.some((r) => r.key === c.key)) }))
              }
            />
          ))}
          {hasFlavourAxis && !draft.comps.some((c) => c.role === 'FLAVOUR' && c.ingredient_id === null) && (
            <div className="flex gap-2.5 rounded-card bg-canvas px-3.5 py-3 text-base text-ink-2">
              <span className="text-xs font-bold tracking-[.04em] text-ink">FLAVOUR</span>
              Set by the flavour chosen below, per size
            </div>
          )}
          <div className="grid grid-cols-1 gap-2.5 sm:grid-cols-2">
            <div className="flex flex-col gap-2.5 rounded-card border border-line-soft bg-surface px-3.5 py-3">
              <div className="flex items-baseline gap-2">
                <span className="text-base font-extrabold">Time to make</span>
                <button
                  type="button"
                  onClick={() => set((d) => ({ ...d, prepEst: !d.prepEst }))}
                  className={cx('text-xs font-bold', draft.prepEst ? 'text-bad-ink' : 'text-ink-2')}
                  title={draft.prepEst ? 'Mark as timed (after timing it)' : 'Mark as an estimate'}
                >
                  {draft.prepEst ? 'estimate, not timed yet' : 'timed'}
                </button>
              </div>
              <SizeGrid sizes={sizes}>
                {(s) => (
                  <label className="flex min-w-0 flex-col gap-1 text-label font-bold uppercase tracking-[.05em] text-ink-2">
                    {sizeLabel(s)}
                    <span className="flex items-center gap-1 normal-case tracking-normal">
                      <Input
                        size="sm"
                        numeric
                        est={draft.prepEst}
                        changed={(draft.prep[s] ?? '') !== (saved.prep[s] ?? '')}
                        value={draft.prep[s] ?? ''}
                        inputMode="numeric"
                        onChange={(e) => {
                          const v = e.target.value
                          if (/^\d*$/.test(v)) set((d) => ({ ...d, prep: { ...d.prep, [s]: v } }))
                        }}
                      />
                      <span className="text-xs font-normal text-ink-2">s</span>
                    </span>
                  </label>
                )}
              </SizeGrid>
            </div>
            <div className="flex flex-col gap-2.5 rounded-card border border-line-soft bg-surface px-3.5 py-3">
              <div className="flex items-baseline gap-1">
                <span className="text-base font-extrabold">Base price</span>
                <span className="text-xs text-ink-2">before flavour extra</span>
              </div>
              <SizeGrid sizes={sizes}>
                {(s) => (
                  <label className="flex min-w-0 flex-col gap-1 text-label font-bold uppercase tracking-[.05em] text-ink-2">
                    {sizeLabel(s)}
                    <span className="flex items-center gap-1 normal-case tracking-normal">
                      <span className="text-sm font-normal text-ink-2" aria-hidden="true">
                        £
                      </span>
                      <Input
                        size="sm"
                        numeric
                        changed={(draft.base[s] ?? '') !== (saved.base[s] ?? '')}
                        missing={editor.base_price_disagrees.includes(s) && (draft.base[s] ?? '') === (saved.base[s] ?? '')}
                        value={draft.base[s] ?? ''}
                        onChange={(e) => {
                          const v = e.target.value
                          if (MONEY_INPUT.test(v)) set((d) => ({ ...d, base: { ...d.base, [s]: v } }))
                        }}
                      />
                    </span>
                  </label>
                )}
              </SizeGrid>
              {editor.base_price_disagrees.length > 0 && (
                <p className="text-sm text-bad-ink">
                  Items disagree at {editor.base_price_disagrees.map(sizeLabel).join(', ')}; the most common price is shown.
                </p>
              )}
            </div>
          </div>
          <Button
            variant="add"
            className="self-start"
            onClick={() =>
              set((d) => ({
                ...d,
                comps: [
                  ...d.comps,
                  {
                    key: `n${Date.now()}`,
                    id: null,
                    role: 'SUNDRY',
                    ingredient_id: null,
                    qty: Object.fromEntries(sizes.map((s) => [s, '1'])),
                    subst: false,
                    required: true,
                  },
                ],
              }))
            }
          >
            + Add component
          </Button>
        </section>

        {/* Flavours */}
        {editor.axes.map((axis) => {
          const opts = draft.options.filter((o) => o.axis_id === axis.axis_id && !o.removed)
          const newCount = opts.filter((o) => o.id === null).length
          return (
            <section key={axis.axis_id} className="mb-[22px] mt-2.5" aria-labelledby={`rec-ax-${axis.axis_id}`}>
              <div className="mb-1.5 flex flex-wrap items-baseline gap-x-2.5">
                <h2 id={`rec-ax-${axis.axis_id}`} className="text-lg font-extrabold tracking-[-.01em]">
                  {axis.name === 'Flavour' ? 'Flavours' : axis.name} · {opts.length}
                </h2>
                <span className="text-sm text-ink-2">each flavour makes one menu item per size</span>
              </div>
              <div className="grid grid-cols-[repeat(auto-fill,minmax(250px,1fr))] gap-2.5">
                {opts.map((o) => (
                  <FlavourCard
                    key={o.key}
                    option={o}
                    saved={saved.options.find((x) => x.key === o.key) ?? null}
                    sizes={sizes}
                    flavourOptions={flavourOptions}
                    seasons={seasons.data ?? []}
                    onChange={(fn) => setOpt(o.key, fn)}
                    onRemove={() =>
                      o.id === null
                        ? set((d) => ({ ...d, options: d.options.filter((x) => x.key !== o.key) }))
                        : setOpt(o.key, (x) => ({ ...x, removed: true }))
                    }
                  />
                ))}
              </div>
              <div className="mt-2.5 flex flex-wrap items-center gap-3">
                <Button
                  variant="add"
                  size="sm"
                  className="rounded-card"
                  onClick={() =>
                    set((d) => ({
                      ...d,
                      options: [
                        ...d.options,
                        {
                          key: `n${Date.now()}`,
                          id: null,
                          axis_id: axis.axis_id,
                          name: 'New flavour',
                          ingredient_id: null,
                          qty: null,
                          extra: '0.00',
                          season_id: null,
                          active: true,
                          removed: false,
                        },
                      ],
                    }))
                  }
                >
                  + Add flavour
                </Button>
                <span className="text-sm text-ink-2">
                  Adding a flavour creates {sizes.length} sellable item{sizes.length === 1 ? '' : 's'} when you apply.
                  {newCount > 0 && ' They are not on the till until someone adds them in Lightspeed.'}
                </span>
              </div>
            </section>
          )
        })}

        <SwapsSection editor={editor} ingOptions={ingOptions} />

        {/* History */}
        <section aria-labelledby="rec-hist">
          <h2 id="rec-hist" className="mb-1 text-lg font-extrabold tracking-[-.01em]">
            Recipe history
          </h2>
          {editor.history.entries.map((h, i) => (
            <div key={i} className="flex gap-3 border-b border-line py-1 text-base">
              <span className="w-[110px] flex-none text-ink-2">from {dayLabel(h.effective_from)}</span>
              <span className="min-w-0">
                {h.summary}
                <span className="text-ink-2"> · {h.actor}</span>
              </span>
            </div>
          ))}
          <p className="mt-1 text-base text-ink-2">Imported from the workbook. Every change after this keeps its own date.</p>
        </section>
      </div>

      {/* --------------------------------------------------- cost column --- */}
      <CostColumn
        editor={editor}
        draft={draft}
        preview={pv}
        dirty={dirty}
        errors={built.errors}
        status={current?.status ?? { kind: key ? 'loading' : 'idle' }}
        ready={ready}
        applying={applying}
        onDiscard={() => {
          setDraft(saved)
          setResult(null)
        }}
        onApply={apply}
        onReload={onReload}
        result={result}
      />
    </div>
  )
}

function SizeGrid({ sizes, children }: { sizes: SizeCode[]; children: (s: SizeCode) => React.ReactNode }) {
  return (
    <div className="grid gap-2" style={{ gridTemplateColumns: `repeat(${sizes.length}, minmax(0, 1fr))` }}>
      {sizes.map((s) => (
        <div key={s} className="min-w-0">
          {children(s)}
        </div>
      ))}
    </div>
  )
}

function ComponentCard({
  row,
  sizes,
  saved,
  ingOptions,
  ingById,
  onRole,
  onIngredient,
  onQty,
  onSubst,
  onRemove,
}: {
  row: Row
  sizes: SizeCode[]
  saved: Draft
  ingOptions: IngredientRow[]
  ingById: Map<number, IngredientRow>
  onRole: (r: ComponentRole) => void
  onIngredient: (key: string, id: number | null) => void
  onQty: (key: string, size: SizeCode, v: string) => void
  onSubst: (v: boolean) => void
  onRemove: () => void
}) {
  const first = row.comps[0] as DComp
  const grouped = row.bySize !== null && row.comps.length > 1
  const subst = row.comps.every((c) => c.subst)
  const compFor = (s: SizeCode): DComp | undefined =>
    row.bySize ? row.comps.find((c) => c.key === row.bySize?.[s]) : first
  const label = first.ingredient_id !== null ? ingById.get(first.ingredient_id)?.name : first.role.toLowerCase()
  return (
    <div className="flex flex-col gap-2.5 rounded-card border border-line-soft bg-surface px-3.5 py-3">
      <div className="flex flex-wrap items-center gap-2 sm:flex-nowrap">
        <Select
          variant="role"
          aria-label="Role"
          value={first.role}
          onChange={(e) => onRole(e.target.value as ComponentRole)}
          className="flex-none"
        >
          {ROLES.map((r) => (
            <option key={r} value={r}>
              {r}
            </option>
          ))}
        </Select>
        <div className="order-last w-full min-w-0 sm:order-none sm:w-auto sm:flex-1">
          {grouped ? (
            <span className="text-base text-ink-2">Different item for each size</span>
          ) : (
            <IngredientSelect
              value={first.ingredient_id}
              options={ingOptions}
              onChange={(id) => onIngredient(first.key, id)}
              allowNone={first.role === 'FLAVOUR'}
            />
          )}
        </div>
        <button
          type="button"
          role="checkbox"
          aria-checked={subst}
          title="Customer can swap this"
          onClick={() => onSubst(!subst)}
          className="ml-auto flex h-[38px] flex-none items-center gap-1.5 rounded-control px-2.5 text-sm text-ink-2 hover:bg-canvas sm:ml-0"
        >
          <span
            className="grid size-[18px] place-items-center rounded-[5px] border-[1.5px] border-line-strong text-xs font-extrabold text-brand-ink"
            aria-hidden="true"
          >
            {subst ? '✓' : ''}
          </span>
          <span className="hidden sm:inline">Swappable</span>
        </button>
        <IconButton label={`Remove ${label ?? 'component'}`} onClick={onRemove} />
      </div>
      <SizeGrid sizes={sizes}>
        {(s) => {
          const c = compFor(s)
          const unit = c?.ingredient_id != null ? ingById.get(c.ingredient_id)?.unit : null
          return (
            <div className="flex flex-col gap-1.5 rounded-control bg-canvas p-1.5 sm:p-2">
              <span className="text-label font-extrabold text-ink-2">
                {sizeLabel(s)}
                {unit && <span className="font-bold sm:hidden"> · {unitWord(unit)}</span>}
              </span>
              {row.bySize !== null && grouped && c && (
                <IngredientSelect value={c.ingredient_id} options={ingOptions} onChange={(id) => onIngredient(c.key, id)} small />
              )}
              {c ? (
                <div className="flex items-center gap-1.5">
                  <Input
                    size="sm"
                    numeric
                    aria-label={`${label ?? 'component'} ${sizeLabel(s)} quantity`}
                    changed={cellChanged(saved, c, s)}
                    value={c.qty[s] ?? ''}
                    placeholder="0"
                    onChange={(e) => {
                      if (QTY_INPUT.test(e.target.value)) onQty(c.key, s, e.target.value)
                    }}
                  />
                  <span className="hidden w-7 flex-none text-sm text-ink-2 sm:inline">{unitWord(unit)}</span>
                </div>
              ) : (
                <span className="py-2 text-sm text-ink-2">—</span>
              )}
            </div>
          )
        }}
      </SizeGrid>
    </div>
  )
}

function IngredientSelect({
  value,
  options,
  onChange,
  small = false,
  allowNone = false,
}: {
  value: number | null
  options: IngredientRow[]
  onChange: (id: number | null) => void
  small?: boolean
  allowNone?: boolean
}) {
  return (
    <Select
      size="sm"
      aria-label="Ingredient"
      value={value === null ? '' : String(value)}
      onChange={(e) => onChange(e.target.value === '' ? null : Number(e.target.value))}
      className={small ? 'text-sm' : undefined}
    >
      <option value="">{allowNone ? '— filled by the flavour —' : '— pick ingredient —'}</option>
      {options.map((o) => (
        <option key={o.ingredient_id} value={o.ingredient_id}>
          {o.name}
        </option>
      ))}
    </Select>
  )
}

function FlavourCard({
  option,
  saved,
  sizes,
  flavourOptions,
  seasons,
  onChange,
  onRemove,
}: {
  option: DOption
  saved: DOption | null
  sizes: SizeCode[]
  flavourOptions: IngredientRow[]
  seasons: { season_id: number; name: string }[]
  onChange: (fn: (o: DOption) => DOption) => void
  onRemove: () => void
}) {
  const missing = option.ingredient_id === null
  const values = option.qty ? sizes.map((s) => option.qty?.[s] ?? '') : []
  const uniform = option.qty === null || values.every((v) => v === values[0])
  const single = option.qty === null ? '' : (values[0] ?? '')
  return (
    <div
      className={cx(
        'flex flex-col gap-2.5 rounded-card border border-line-soft px-3.5 py-3',
        missing ? 'bg-alert-wash' : 'bg-surface',
        !option.active && 'opacity-50',
      )}
    >
      <div className="flex items-center gap-2">
        <Input
          size="sm"
          aria-label="Flavour name"
          value={option.name}
          changed={saved !== null && saved.name !== option.name}
          onChange={(e) => onChange((o) => ({ ...o, name: e.target.value }))}
          className="flex-1 border-transparent px-1 font-bold"
        />
        <Toggle checked={option.active} onChange={(v) => onChange((o) => ({ ...o, active: v }))} label={<span className="sr-only">On menu</span>} />
        <IconButton label={`Remove ${option.name}`} onClick={onRemove} className="size-8" />
      </div>
      <label className="flex min-w-0 flex-col gap-1 text-label font-bold uppercase tracking-[.05em] text-ink-2">
        Ingredient
        <Select
          size="sm"
          value={option.ingredient_id === null ? '' : String(option.ingredient_id)}
          onChange={(e) => onChange((o) => ({ ...o, ingredient_id: e.target.value === '' ? null : Number(e.target.value) }))}
          className={cx('font-normal normal-case tracking-normal text-ink', missing && 'border-alert')}
        >
          <option value="">— none —</option>
          {flavourOptions.map((r) => (
            <option key={r.ingredient_id} value={r.ingredient_id}>
              {r.name}
            </option>
          ))}
        </Select>
      </label>
      <div className="grid grid-cols-[70px_80px_minmax(0,1fr)] gap-2">
        <label className="flex min-w-0 flex-col gap-1 text-label font-bold uppercase tracking-[.05em] text-ink-2">
          Qty
          {uniform ? (
            <Input
              size="sm"
              numeric
              placeholder="recipe"
              value={single}
              onChange={(e) => {
                const v = e.target.value
                if (!QTY_INPUT.test(v)) return
                onChange((o) => ({ ...o, qty: v === '' ? null : Object.fromEntries(sizes.map((s) => [s, v])) }))
              }}
              className="font-normal normal-case tracking-normal"
            />
          ) : (
            <span className="py-2 text-xs font-normal normal-case tracking-normal text-ink-2" title={values.join(' / ')}>
              per size
            </span>
          )}
        </label>
        <label className="flex min-w-0 flex-col gap-1 text-label font-bold uppercase tracking-[.05em] text-ink-2">
          Extra £
          <Input
            size="sm"
            numeric
            value={option.extra}
            changed={saved !== null && saved.extra !== option.extra}
            onChange={(e) => {
              if (/^-?\d*(\.\d{0,2})?$/.test(e.target.value)) onChange((o) => ({ ...o, extra: e.target.value }))
            }}
            className="font-normal normal-case tracking-normal"
          />
        </label>
        <label className="flex min-w-0 flex-col gap-1 text-label font-bold uppercase tracking-[.05em] text-ink-2">
          Season
          <Select
            size="sm"
            value={option.season_id === null ? '' : String(option.season_id)}
            onChange={(e) => onChange((o) => ({ ...o, season_id: e.target.value === '' ? null : Number(e.target.value) }))}
            className="font-normal normal-case tracking-normal"
          >
            <option value="">all year</option>
            {seasons.map((s) => (
              <option key={s.season_id} value={s.season_id}>
                {s.name}
              </option>
            ))}
          </Select>
        </label>
      </div>
    </div>
  )
}

/* ------------------------------------------------------------ cost column -- */

interface Cell {
  price: number
  cost: Cost
  prep: number | null
}

function itemCell(i: EditorItem): Cell {
  return { price: i.price_pence, cost: i.cost, prep: i.prep_seconds }
}
function changeCell(c: ChangeItem): Cell | null {
  if (c.price_after === null) return null
  return { price: c.price_after, cost: c.cost_after, prep: c.prep_seconds }
}

function CostColumn({
  editor,
  draft,
  preview,
  dirty,
  errors,
  status,
  ready,
  applying,
  onDiscard,
  onApply,
  onReload,
  result,
}: {
  editor: RecipeEditor
  draft: Draft
  preview: ChangesetPreview | null
  dirty: boolean
  errors: string[]
  status: import('../menu/common/Impact').PreviewStatus
  ready: boolean
  applying: boolean
  onDiscard: () => void
  onApply: () => void
  onReload: () => void
  result: { tone: 'ok' | 'bad'; text: string; pos: string[] } | null
}) {
  const options = draft.options.filter((o) => !o.removed)
  const defaultKey =
    options.find((o) => o.active && o.ingredient_id !== null)?.key ?? options.find((o) => o.active)?.key ?? options[0]?.key ?? ''
  const [pick, setPick] = useState(defaultKey)
  const chosen = options.find((o) => o.key === pick) ?? options.find((o) => o.key === defaultKey) ?? null
  const [rate, setRate] = useState(penceToPounds(editor.loaded_hourly_rate_pence ?? 1450))
  const ratePence = poundsToPence(rate)

  const cells = editor.sizes.map((s): Cell | null => {
    if (!chosen) return null
    const changes = preview && dirty ? preview.impact.items : []
    if (chosen.id !== null) {
      const item = editor.items.find((i) => i.option_id === chosen.id && i.size_code === s)
      if (!item) return null
      const changed = changes.find((c) => c.menu_item_id === item.menu_item_id)
      return (changed && changeCell(changed)) ?? itemCell(item)
    }
    const name = editor.item_name_pattern.replace('{flavour}', chosen.name.trim())
    const created = preview?.items_created.find((c) => c.name === name && c.size_code === s)
    return created ? changeCell(created) : null
  })

  const rowsDef: { label: string; strong?: boolean; value: (c: Cell) => { text: string; alert?: boolean; est?: boolean } }[] = [
    { label: 'Price', value: (c) => ({ text: gbp(c.price) }) },
    { label: 'Ingredients', value: (c) => ({ text: costText(c.cost), est: c.cost.is_estimate }) },
    {
      label: 'Staff time',
      value: (c) => {
        const l = labourPence(c.prep, ratePence)
        return { text: l ? gbp(decStr(l)) : '—' }
      },
    },
    {
      label: 'Margin',
      strong: true,
      value: (c) => {
        const m = marginPct(c.price, c.cost)
        return { text: m === null ? '—' : `${Math.round(m)}%`, alert: m !== null && m < 60, est: c.cost.is_estimate }
      },
    },
    {
      label: 'After staff',
      value: (c) => {
        const l = labourPence(c.prep, ratePence)
        if (!l || c.cost.pence === null || c.price <= 0) return { text: '—' }
        const p = fromInt(c.price)
        const m = ratioPct(sub(sub(p, fromMoney(c.cost.pence)), l), p)
        return { text: m === null ? '—' : `${Math.round(m)}%`, alert: m !== null && m < 40, est: c.cost.is_estimate }
      },
    },
    {
      label: 'Per minute',
      strong: true,
      value: (c) => {
        const pm = perMinute(c.price, c.cost, c.prep)
        return { text: pm ? gbp(decStr(pm)) : '—', est: c.cost.is_estimate }
      },
    },
  ]

  const figs = preview ? impactFigures(preview.impact) : []
  return (
    <aside className="border-t border-line-soft bg-canvas px-4 py-[18px] compact:w-[320px] compact:flex-none compact:overflow-y-auto compact:border-l compact:border-t-0 wide:w-[380px]">
      <div className="mb-2 flex items-center gap-2">
        <h2 className="flex-1 text-lg font-extrabold tracking-[-.01em]">What it costs</h2>
        {options.length > 0 && (
          <select
            aria-label="Flavour to cost"
            value={chosen?.key ?? ''}
            onChange={(e) => setPick(e.target.value)}
            className="max-w-[170px] rounded-control border border-line-strong bg-surface px-1 py-0.5 text-sm"
          >
            {options.map((o) => (
              <option key={o.key} value={o.key}>
                {o.name}
              </option>
            ))}
          </select>
        )}
      </div>
      <div
        className="grid gap-x-2 rounded-card bg-surface px-3.5 py-1.5 text-base"
        style={{ gridTemplateColumns: `96px repeat(${editor.sizes.length}, minmax(0, 1fr))` }}
        role="table"
        aria-label="Cost per size"
      >
        <span className="border-b border-line-row py-2" />
        {editor.sizes.map((s) => (
          <span key={s} className="border-b border-line-row py-2 text-right text-ink-2">
            {sizeLabel(s)}
          </span>
        ))}
        {rowsDef.map((r) => (
          <div key={r.label} className="contents" role="row">
            <span className="whitespace-nowrap border-b border-line-row py-2 text-ink-2">{r.label}</span>
            {cells.map((c, i) => {
              const v = c ? r.value(c) : { text: '—' }
              return (
                <span
                  key={i}
                  className={cx(
                    'fig whitespace-nowrap border-b border-line-row py-2 text-right',
                    r.strong && 'font-bold',
                    v.alert && 'text-alert',
                    v.est && 'italic',
                  )}
                >
                  {v.text}
                </span>
              )
            })}
          </div>
        ))}
      </div>
      <p className="mb-[18px] mt-2.5 text-base text-ink-2">
        Staff time at £
        <input
          aria-label="Hourly staff rate, pounds (what-if, not saved)"
          value={rate}
          onChange={(e) => {
            if (MONEY_INPUT.test(e.target.value)) setRate(e.target.value)
          }}
          className="fig mx-1 w-[52px] rounded-control border border-line-strong bg-surface px-1 text-right"
        />
        an hour <span className="text-sm">(what-if, not saved)</span>
      </p>

      {result && (
        <div
          role="status"
          className={cx(
            'mb-3 rounded-card border px-3 py-2.5 text-base',
            result.tone === 'ok' ? 'border-line bg-surface' : 'border-alert bg-alert-wash',
          )}
        >
          <p>{result.text}</p>
          {result.pos.map((p) => (
            <p key={p} className="mt-1 text-sm font-semibold">
              {p}
            </p>
          ))}
          {result.tone === 'bad' && (
            <Button variant="outline" size="sm" className="mt-2" onClick={onReload}>
              Reload recipe
            </Button>
          )}
        </div>
      )}

      {!dirty ? (
        <div className="rounded-card border border-line p-3 text-base text-ink-2">
          No unsaved changes. Edit any quantity, price or flavour and the effect shows up here before anything is saved.
        </div>
      ) : (
        <ImpactPanel
          status={errors.length ? { kind: 'idle' } : status}
          diff={preview?.diff ?? []}
          figures={figs}
          warnings={[...errors, ...(preview?.warnings ?? []), ...(preview?.impact.warnings ?? [])]}
          refusals={preview?.refusals.map((r) => r.message) ?? []}
          posActions={preview?.pos_actions ?? []}
          onDiscard={onDiscard}
          onApply={onApply}
          applying={applying}
          ready={ready && errors.length === 0}
          operatorWhat="apply a recipe change"
        >
          {status.kind === 'conflict' && (
            <Button variant="outline" size="sm" className="mb-1" onClick={onReload}>
              Reload recipe
            </Button>
          )}
        </ImpactPanel>
      )}
    </aside>
  )
}
