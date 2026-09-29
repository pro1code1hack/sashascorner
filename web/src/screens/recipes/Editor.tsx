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
import { useEffect, useMemo, useRef, useState } from 'react'
import { Button, Checkbox, IconButton, Input, Pill, Select, StatusLine, TBody, Td, Th, THead, Table, TitleInput, Toggle, Tr, cx } from '../../components/ui'
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
import { ROLES, ROLE_LABEL, buildOps, cellChanged, draftFrom, groupRows } from './model'
import { IngredientPicker } from '../menu/IngredientPicker'
import type { DComp, DOption, Draft, Row } from './model'
import { SwapsSection } from './Swaps'

const FLAVOUR_CATS = new Set(['Syrup', 'Specialty', 'Chocolate', 'Tea'])
const NONE = new Set<number>()

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
  // Focus after a remove: the next (else previous) card's remove button, else the add button.
  const compRemove = useRef(new Map<string, HTMLButtonElement>())
  const addComp = useRef<HTMLButtonElement>(null)
  const optRemove = useRef(new Map<string, HTMLButtonElement>())
  const addOpt = useRef(new Map<number, HTMLButtonElement>())
  const costHead = useRef<HTMLHeadingElement>(null)
  const focusAfter = (keys: string[], i: number, refs: Map<string, HTMLButtonElement>, fallback: HTMLButtonElement | null | undefined) => {
    const next = keys[i + 1] ?? keys[i - 1]
    ;(next !== undefined ? refs.get(next) : undefined)?.focus() ?? fallback?.focus()
  }
  const refSetter = (refs: Map<string, HTMLButtonElement>, k: string) => (el: HTMLButtonElement | null) => {
    if (el) refs.set(k, el)
    else refs.delete(k)
  }

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
  const usedIngredients = (except: Row) =>
    new Set(
      draft.comps
        .filter((c) => !except.comps.some((r) => r.key === c.key))
        .flatMap((c) => (c.ingredient_id === null ? [] : [c.ingredient_id])),
    )

  return (
    <div className="min-h-0 flex-1 overflow-y-auto compact:flex compact:overflow-hidden">
      {/* ------------------------------------------------ editor column --- */}
      <div className="min-w-0 px-4 pb-8 pt-5 sm:px-5.5 compact:flex-1 compact:overflow-y-auto">
        <div className="flex flex-wrap items-baseline gap-x-2.5 gap-y-1">
          <TitleInput
            aria-label="Recipe name"
            value={draft.name}
            onChange={(e) => set((d) => ({ ...d, name: e.target.value }))}
            className="flex-1"
          />
          {editor.category && <span className="whitespace-nowrap text-base text-ink-2">{editor.category}</span>}
        </div>
        <p className="mb-4 mt-1 text-base text-ink-2">
          Makes {flavourCount * sizes.length} menu items: {flavourCount} flavour{flavourCount === 1 ? '' : 's'} ×{' '}
          {sizes.length} size{sizes.length === 1 ? '' : 's'} ({sizes.map(sizeLabel).join(', ')})
        </p>

        {/* What goes in, per size */}
        <section className="mb-6.5 flex flex-col gap-2.5" aria-labelledby="rec-in">
          <h2 id="rec-in" className="text-lg font-extrabold tracking-[-.01em]">
            What goes in, per size
          </h2>
          {rows.map((row, i) => (
            <ComponentCard
              key={row.key}
              row={row}
              sizes={sizes}
              saved={saved}
              ingOptions={ingOptions}
              ingById={ingById}
              inRecipe={usedIngredients(row)}
              removeRef={refSetter(compRemove.current, row.key)}
              onRole={(role) => row.comps.forEach((c) => setComp(c.key, (x) => ({ ...x, role })))}
              onIngredient={(key, id) => setComp(key, (x) => ({ ...x, ingredient_id: id }))}
              onQty={(key, size, v) => setComp(key, (x) => ({ ...x, qty: { ...x.qty, [size]: v } }))}
              onSubst={(v) => row.comps.forEach((c) => setComp(c.key, (x) => ({ ...x, subst: v })))}
              onRemove={() => {
                focusAfter(
                  rows.map((r) => r.key),
                  i,
                  compRemove.current,
                  addComp.current,
                )
                set((d) => ({ ...d, comps: d.comps.filter((c) => !row.comps.some((r) => r.key === c.key)) }))
              }}
            />
          ))}
          {hasFlavourAxis && !draft.comps.some((c) => c.role === 'FLAVOUR' && c.ingredient_id === null) && (
            <div className="flex gap-2.5 rounded-card bg-canvas px-3.5 py-3 text-base text-ink-2">
              <span className="text-xs font-bold uppercase tracking-[.04em] text-ink">{ROLE_LABEL.FLAVOUR}</span>
              Set by the flavour chosen below, per size
            </div>
          )}
          <div className="grid grid-cols-1 gap-2.5 sm:grid-cols-2">
            <div className="flex flex-col gap-2.5 rounded-card border border-line-soft bg-surface px-3.5 py-3">
              <div className="flex flex-wrap items-center gap-x-3 gap-y-1">
                <span className="text-base font-extrabold">Time to make</span>
                {/* An estimate is italic, not red: it is a guess, not a crossed threshold. */}
                <Checkbox
                  className="min-h-9"
                  checked={draft.prepEst}
                  onChange={(v) => set((d) => ({ ...d, prepEst: v }))}
                  label={<span className="text-sm text-ink-2">Estimate, not timed yet</span>}
                />
              </div>
              <SizeGrid sizes={sizes}>
                {(s) => (
                  <label className="flex min-w-0 flex-col gap-1 text-label font-bold uppercase tracking-[.05em] text-ink-2">
                    <span>
                      {sizeLabel(s)}
                      <span className="sr-only"> time to make, seconds</span>
                    </span>
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
                    <span>
                      {sizeLabel(s)}
                      <span className="sr-only"> base price, pounds</span>
                    </span>
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
            ref={addComp}
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
            <section key={axis.axis_id} className="mb-5.5 mt-2.5" aria-labelledby={`rec-ax-${axis.axis_id}`}>
              <div className="mb-1.5 flex flex-wrap items-baseline gap-x-2.5">
                <h2 id={`rec-ax-${axis.axis_id}`} className="text-lg font-extrabold tracking-[-.01em]">
                  {axis.name === 'Flavour' ? 'Flavours' : axis.name} · {opts.length}
                </h2>
                <span className="text-sm text-ink-2">each flavour makes one menu item per size</span>
              </div>
              <div className="grid grid-cols-[repeat(auto-fill,minmax(250px,1fr))] gap-2.5">
                {opts.map((o, i) => (
                  <FlavourCard
                    key={o.key}
                    index={i}
                    option={o}
                    removeRef={refSetter(optRemove.current, o.key)}
                    saved={saved.options.find((x) => x.key === o.key) ?? null}
                    sizes={sizes}
                    flavourOptions={flavourOptions}
                    seasons={seasons.data ?? []}
                    onChange={(fn) => setOpt(o.key, fn)}
                    onRemove={() => {
                      focusAfter(
                        opts.map((x) => x.key),
                        i,
                        optRemove.current,
                        addOpt.current.get(axis.axis_id),
                      )
                      if (o.id === null) set((d) => ({ ...d, options: d.options.filter((x) => x.key !== o.key) }))
                      else setOpt(o.key, (x) => ({ ...x, removed: true }))
                    }}
                  />
                ))}
              </div>
              <div className="mt-2.5 flex flex-wrap items-center gap-3">
                <Button
                  ref={(el) => {
                    if (el) addOpt.current.set(axis.axis_id, el)
                    else addOpt.current.delete(axis.axis_id)
                  }}
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
              </span>
            </div>
          ))}
          <p className="mt-1 text-base text-ink-2">Imported from the workbook. Every change after this keeps its own date.</p>
        </section>

        {/* Phone: the cost column (with Apply) stacks under everything above, so
            keep Discard and the way to the mandatory preview in reach. */}
        {dirty && (
          <div className="sticky bottom-0 z-10 -mx-1 mt-4 flex flex-wrap items-center gap-2 rounded-card border border-line bg-surface px-3 py-2.5 shadow-login compact:hidden">
            <span className={cx('min-w-0 flex-1 text-sm', built.errors.length ? 'text-bad-ink' : 'text-ink-2')}>
              {built.errors.length ? built.errors[0] : 'Unsaved changes. Nothing is saved until you apply.'}
            </span>
            <Button
              variant="outline"
              disabled={applying}
              onClick={() => {
                setDraft(saved)
                setResult(null)
              }}
            >
              Discard
            </Button>
            <Button
              variant="primary"
              onClick={() => {
                costHead.current?.scrollIntoView({ block: 'start' })
                costHead.current?.focus()
              }}
            >
              See the effect and apply
            </Button>
          </div>
        )}
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
        headRef={costHead}
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
  inRecipe,
  removeRef,
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
  /** Ingredients on the recipe's other components, marked in the picker. */
  inRecipe: Set<number>
  removeRef: (el: HTMLButtonElement | null) => void
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
  const roleWord = ROLE_LABEL[first.role] ?? first.role
  const label = first.ingredient_id !== null ? ingById.get(first.ingredient_id)?.name : roleWord.toLowerCase()
  return (
    <div className="flex flex-col gap-2.5 rounded-card border border-line-soft bg-surface px-3.5 py-3">
      <div className="flex flex-wrap items-center gap-2 sm:flex-nowrap">
        <Select
          variant="role"
          aria-label={`Role of ${label ?? 'this component'}`}
          value={first.role}
          onChange={(e) => onRole(e.target.value as ComponentRole)}
          className="flex-none uppercase"
        >
          {ROLES.map((r) => (
            <option key={r} value={r}>
              {ROLE_LABEL[r]}
            </option>
          ))}
        </Select>
        <div className="order-last w-full min-w-0 sm:order-none sm:w-auto sm:flex-1">
          {grouped ? (
            <span className="text-base text-ink-2">Different item for each size</span>
          ) : (
            <IngredientPicker
              size="sm"
              label={`${roleWord} ingredient`}
              placeholder={first.role === 'FLAVOUR' ? 'Filled by the flavour' : 'Pick an ingredient…'}
              value={first.ingredient_id}
              options={ingOptions}
              inRecipe={inRecipe}
              onPick={(id) => onIngredient(first.key, id)}
              onClear={first.role === 'FLAVOUR' ? () => onIngredient(first.key, null) : undefined}
            />
          )}
        </div>
        <Checkbox
          className="ml-auto h-[38px] flex-none rounded-control px-2.5 hover:bg-canvas sm:ml-0"
          checked={subst}
          onChange={onSubst}
          label={
            <span className="text-sm text-ink-2">
              <span className="max-sm:sr-only">Swappable</span>
              <span className="sr-only">: a customer can swap the {label ?? 'component'}</span>
            </span>
          }
        />
        <IconButton ref={removeRef} label={`Remove ${label ?? 'component'}`} onClick={onRemove} />
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
                <IngredientPicker
                  size="sm"
                  label={`${roleWord} ingredient, size ${sizeLabel(s)}`}
                  value={c.ingredient_id}
                  options={ingOptions}
                  inRecipe={inRecipe}
                  onPick={(id) => onIngredient(c.key, id)}
                />
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

function FlavourCard({
  index,
  removeRef,
  option,
  saved,
  sizes,
  flavourOptions,
  seasons,
  onChange,
  onRemove,
}: {
  index: number
  removeRef: (el: HTMLButtonElement | null) => void
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
  const who = option.name.trim() || `flavour ${index + 1}`
  // Off the menu is said with a word, never by fading the card (text stays at full contrast).
  return (
    <div
      className={cx(
        'flex flex-col gap-2.5 rounded-card border border-line-soft px-3.5 py-3',
        missing ? 'bg-alert-wash' : 'bg-surface',
      )}
    >
      <div className="flex items-center gap-2">
        <Input
          size="sm"
          aria-label={`Name of flavour ${index + 1}`}
          value={option.name}
          changed={saved !== null && saved.name !== option.name}
          onChange={(e) => onChange((o) => ({ ...o, name: e.target.value }))}
          className="flex-1 border-transparent px-1 font-bold"
        />
        <Toggle
          checked={option.active}
          onChange={(v) => onChange((o) => ({ ...o, active: v }))}
          label={<span className="sr-only">{who} on the menu</span>}
        />
        <IconButton ref={removeRef} label={`Remove ${who}`} onClick={onRemove} />
      </div>
      {!option.active && (
        <div>
          <Pill tone="muted">Off the menu</Pill>
        </div>
      )}
      <div className="flex min-w-0 flex-col gap-1 text-label font-bold uppercase tracking-[.05em] text-ink-2">
        <span aria-hidden="true">Ingredient</span>
        <div className={cx('font-normal normal-case tracking-normal text-ink', missing && '[&>button:first-child]:border-alert')}>
          <IngredientPicker
            size="sm"
            label={`Ingredient for ${who}`}
            placeholder="None yet"
            value={option.ingredient_id}
            options={flavourOptions}
            inRecipe={NONE}
            onPick={(id) => onChange((o) => ({ ...o, ingredient_id: id }))}
            onClear={() => onChange((o) => ({ ...o, ingredient_id: null }))}
          />
        </div>
      </div>
      <div className="grid grid-cols-[70px_80px_minmax(0,1fr)] gap-2">
        <label className="flex min-w-0 flex-col gap-1 text-label font-bold uppercase tracking-[.05em] text-ink-2">
          <span>
            Qty<span className="sr-only"> of {who}</span>
          </span>
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
            <span className="fig py-2 text-xs font-normal normal-case tracking-normal text-ink-2">
              {sizes.map((s, i) => `${sizeLabel(s)} ${values[i] || '—'}`).join(' · ')}
            </span>
          )}
        </label>
        <label className="flex min-w-0 flex-col gap-1 text-label font-bold uppercase tracking-[.05em] text-ink-2">
          <span>
            Extra £<span className="sr-only"> for {who}</span>
          </span>
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
          <span>
            Season<span className="sr-only"> for {who}</span>
          </span>
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
  headRef,
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
  /** The heading the phone's sticky bar jumps to. */
  headRef: React.RefObject<HTMLHeadingElement>
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
    <aside
      aria-labelledby="rec-cost"
      className="border-t border-line-soft bg-canvas px-4 py-4.5 compact:w-[320px] compact:flex-none compact:overflow-y-auto compact:border-l compact:border-t-0 wide:w-[380px]"
    >
      <div className="mb-2 flex items-center gap-2">
        <h2 id="rec-cost" ref={headRef} tabIndex={-1} className="flex-1 text-lg font-extrabold tracking-[-.01em]">
          What it costs
        </h2>
        {options.length > 0 && (
          <Select
            size="sm"
            aria-label="Flavour to cost"
            value={chosen?.key ?? ''}
            onChange={(e) => setPick(e.target.value)}
            className="w-auto max-w-[170px]"
          >
            {options.map((o) => (
              <option key={o.key} value={o.key}>
                {o.name}
              </option>
            ))}
          </Select>
        )}
      </div>
      <div className="rounded-card bg-surface px-3.5 py-1.5">
        <Table label={`Cost per size${chosen ? `, ${chosen.name}` : ''}`}>
          <THead>
            <tr>
              <Th>
                <span className="sr-only">Figure</span>
              </Th>
              {editor.sizes.map((s) => (
                <Th key={s} numeric>
                  {sizeLabel(s)}
                </Th>
              ))}
            </tr>
          </THead>
          <TBody>
            {rowsDef.map((r) => (
              <Tr key={r.label}>
                <th scope="row" className="whitespace-nowrap py-2 pr-2 text-left font-normal text-ink-2">
                  {r.label}
                </th>
                {cells.map((c, i) => {
                  const v = c ? r.value(c) : { text: '—' }
                  return (
                    <Td key={i} numeric strong={r.strong} alert={v.alert} est={v.est} className="py-2">
                      {v.text}
                    </Td>
                  )
                })}
              </Tr>
            ))}
          </TBody>
        </Table>
      </div>
      <div className="mb-4.5 mt-2.5 flex flex-wrap items-center gap-x-1.5 gap-y-1 text-base text-ink-2">
        <label className="flex items-center gap-1.5">
          Staff time at £
          <span className="w-[76px]">
            <Input
              size="sm"
              numeric
              aria-label="Hourly staff rate, pounds (what-if, not saved)"
              value={rate}
              onChange={(e) => {
                if (MONEY_INPUT.test(e.target.value)) setRate(e.target.value)
              }}
            />
          </span>
          an hour
        </label>
        <span className="text-sm">(what-if, not saved)</span>
      </div>

      <StatusLine
        className="mb-2"
        outcome={
          result === null
            ? null
            : {
                kind: result.tone === 'ok' ? 'ok' : 'error',
                text: result.text,
                action: result.tone === 'bad' ? { label: 'Reload recipe', onClick: onReload } : undefined,
              }
        }
      />
      {result && result.pos.length > 0 && (
        <div className="mb-3 rounded-card border border-line bg-surface px-3 py-2.5">
          {result.pos.map((p) => (
            <p key={p} className="text-sm font-semibold">
              {p}
            </p>
          ))}
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
