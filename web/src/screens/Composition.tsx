/**
 * The composition editor. Spec §10.1.
 *
 * Three panes. Left: templates by category with the item count each drives —
 * "Flavoured Latte, 9 items" is the single fact that explains the whole model.
 * Centre: component slots by role, per-size quantities in an editable grid, with
 * prep seconds per size as a row of the same grid. Right: the live consequences.
 *
 * Quantities are strings from the API, are edited as strings, and are sent back
 * as strings. Nothing here parses one into a float — `0.1 + 0.2` in a recipe
 * editor is unacceptable (spec §10.10).
 */
import { useEffect, useMemo, useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import { api, preview, type PreviewResult } from '../lib/api'
import { cmp, mustDec, parseDec } from '../lib/dec'
import { money, pct, pence, plural, stamp } from '../lib/format'
import type { ComponentRole, MenuItemRow, TemplateDetailResponse } from '../lib/types'
import { Body, CostFig, Fig, Label, Marg, Qty, Sheet } from '../components/prim'
import { Page, ScreenTitle } from '../components/shell'
import { Gate } from '../components/gate'

const ROLE_ORDER: ComponentRole[] = [
  'COFFEE',
  'MILK',
  'BASE',
  'FLAVOUR',
  'TOPPING',
  'PACKAGING',
  'SUNDRY',
]

const QTY_INPUT = /^\d*\.?\d*$/

/** A real responsive adaptation rather than a duplicated DOM: below 640px the
 *  per-size grid transposes so sizes become rows, which is the only way three
 *  editable figure columns fit a 375px screen without a horizontal scroll. */
function useWide(): boolean {
  const [wide, setWide] = useState(
    () => typeof window !== 'undefined' && window.matchMedia('(min-width: 640px)').matches,
  )
  useEffect(() => {
    const mq = window.matchMedia('(min-width: 640px)')
    const on = () => setWide(mq.matches)
    mq.addEventListener('change', on)
    return () => mq.removeEventListener('change', on)
  }, [])
  return wide
}

/* ------------------------------------------------- the consequences pane --- */

interface SizeStat {
  size: string
  itemCount: number
  /** Exact decimal strings of pence; lo === hi when every item of the size
   *  resolves to the same cost, which is the normal case. */
  costLo: string | null
  costHi: string | null
  costMissing: number
  costEstimated: number
  marginLo: number | null
  marginHi: number | null
  trueLo: number | null
  trueHi: number | null
  mpmLo: string | null
  mpmHi: string | null
  prepSeconds: number | null
  prepOverridden: boolean
}

function minMaxDec(values: string[]): [string | null, string | null] {
  let lo: string | null = null
  let hi: string | null = null
  for (const v of values) {
    if (lo === null || cmp(mustDec(v), mustDec(lo)) < 0) lo = v
    if (hi === null || cmp(mustDec(v), mustDec(hi)) > 0) hi = v
  }
  return [lo, hi]
}

function minMaxNum(values: (number | null)[]): [number | null, number | null] {
  const nums = values.filter((v): v is number => v !== null)
  if (nums.length === 0) return [null, null]
  return [Math.min(...nums), Math.max(...nums)]
}

function sizeStats(detail: TemplateDetailResponse): SizeStat[] {
  return detail.template.sizes.map((size) => {
    const items = detail.items.filter((i) => i.size_code === size)
    const costed = items.filter((i) => !i.cost.is_missing && i.cost.pence !== null)
    const [costLo, costHi] = minMaxDec(costed.map((i) => i.cost.pence as string))
    const [marginLo, marginHi] = minMaxNum(items.map((i) => i.margin_pct))
    const [trueLo, trueHi] = minMaxNum(items.map((i) => i.true_margin_pct))
    const [mpmLo, mpmHi] = minMaxDec(
      items.map((i) => i.margin_per_minute_pence).filter((v): v is string => v !== null),
    )
    const templatePrep = detail.template.prep_seconds_by_size[size] ?? null
    return {
      size,
      itemCount: items.length,
      costLo,
      costHi,
      costMissing: items.length - costed.length,
      costEstimated: items.filter((i) => i.cost.is_estimate).length,
      marginLo,
      marginHi,
      trueLo,
      trueHi,
      mpmLo,
      mpmHi,
      prepSeconds: templatePrep,
      prepOverridden: items.some((i) => i.prep_source === 'MENU_ITEM'),
    }
  })
}

/**
 * Two grid cells, not one: the head is right-aligned in its own column and the
 * exact tail hangs to its left edge in the next, so the decimal point lines up
 * down the whole pane instead of lining up by accident when every tail happens
 * to be the same length. DESIGN.md §1 R3.
 */
function CostRange({ lo, hi, estimated }: { lo: string | null; hi: string | null; estimated: boolean }) {
  if (lo === null || hi === null) {
    return (
      <dd className="col-span-2 text-right">
        <Fig className="text-flag">—</Fig>{' '}
        <Label className="text-flag">no price</Label>
      </dd>
    )
  }
  const same = cmp(mustDec(lo), mustDec(hi)) === 0
  const head = pence(lo).head
  const tail = pence(lo).tail
  if (!same) {
    return (
      <dd className={`col-span-2 text-right ${estimated ? 'est' : ''}`}>
        <Fig weight={500}>
          {money(lo)}
          <span className="text-muted">–</span>
          {money(hi)}
        </Fig>
      </dd>
    )
  }
  return (
    <>
      <dd className={`text-right ${estimated ? 'est' : ''}`}>
        <Fig weight={500}>{head}</Fig>
      </dd>
      <dd className={`text-left ${estimated ? 'est' : ''}`}>
        <Fig weight={500} className="text-muted">
          <span className="text-[0.85em]">{tail}</span>p
        </Fig>
      </dd>
    </>
  )
}

function Consequences({ detail }: { detail: TemplateDetailResponse }) {
  const stats = useMemo(() => sizeStats(detail), [detail])
  const worst = useMemo(() => {
    let w: MenuItemRow | null = null
    for (const i of detail.items) {
      if (i.true_margin_pct === null) continue
      if (w === null || i.true_margin_pct < (w.true_margin_pct ?? Infinity)) w = i
    }
    return w
  }, [detail])

  return (
    <div>
      <h3 className="border-rule-strong mb-2 border-b pb-1 text-[length:var(--text-prose)] font-[600]">
        Consequences
      </h3>

      {stats.map((s) => (
        <div key={s.size} className="rule-b py-2">
          <div className="flex items-baseline justify-between">
            <Fig weight={500}>size {s.size}</Fig>
            <Label>
              {s.itemCount} {plural(s.itemCount, 'item')}
            </Label>
          </div>
          <dl className="mt-1 grid grid-cols-[5.5rem_1fr_3.6rem] items-baseline gap-y-[2px]">
            <dt>
              <Label>cost</Label>
            </dt>
            <CostRange lo={s.costLo} hi={s.costHi} estimated={s.costEstimated > 0} />

            <dt>
              <Label>margin</Label>
            </dt>
            <dd className="col-span-2 text-right">
              <Fig>{s.marginLo === s.marginHi ? pct(s.marginLo) : `${pct(s.marginLo)}–${pct(s.marginHi)}`}</Fig>
            </dd>

            <dt>
              <Label>after labour</Label>
            </dt>
            <dd className="col-span-2 text-right">
              <Fig weight={500}>
                {s.trueLo === s.trueHi ? pct(s.trueLo) : `${pct(s.trueLo)}–${pct(s.trueHi)}`}
              </Fig>
            </dd>

            <dt>
              <Label>per minute</Label>
            </dt>
            <dd className="col-span-2 text-right">
              {s.mpmLo === null ? (
                <Label>—</Label>
              ) : (
                <Fig size="sub">
                  {money(s.mpmLo)}
                  {s.mpmHi !== null && cmp(mustDec(s.mpmLo), mustDec(s.mpmHi)) !== 0 && (
                    <>
                      <span className="text-muted">–</span>
                      {money(s.mpmHi)}
                    </>
                  )}
                </Fig>
              )}
            </dd>

            <dt>
              <Label>prep</Label>
            </dt>
            <dd className="col-span-2 text-right">
              <Fig size="sub">
                {s.prepSeconds === null ? '—' : `${s.prepSeconds}`}
                <span className="text-muted"> s</span>
              </Fig>
              {s.prepOverridden && (
                <div>
                  <Label>one item sets its own</Label>
                </div>
              )}
            </dd>
          </dl>
          {s.costMissing > 0 && (
            <p className="text-flag mt-1">
              {s.costMissing} of {s.itemCount} have no price and are excluded from the figures
              above.
            </p>
          )}
        </div>
      ))}

      {worst && (
        <div className="mt-3">
          <Label>lowest margin after labour</Label>
          <div className="mt-[2px]">
            <span className="font-[600]">
              {worst.name} {worst.size_code}
            </span>
          </div>
          <div>
            <Fig weight={500}>{pct(worst.true_margin_pct)}</Fig>{' '}
            <Label>
              on {pct(worst.margin_pct)} before labour · {worst.prep_seconds ?? '—'} s prep
              {worst.prep_source === 'MENU_ITEM' ? ', set on the item not the template' : ''}
            </Label>
          </div>
        </div>
      )}

      {detail.warnings.map((w) => (
        <p key={w} className="text-muted rule-t mt-3 max-w-[40ch] pt-2 leading-relaxed">
          {w}
        </p>
      ))}
    </div>
  )
}

/* ------------------------------------------------------------- the editor --- */

function cellKey(componentId: number, size: string): string {
  return `${componentId}:${size}`
}

export function Composition() {
  const templates = useQuery({ queryKey: ['templates'], queryFn: () => api.templates() })
  const [templateId, setTemplateId] = useState<number | null>(null)
  const chosen = templateId ?? templates.data?.[0]?.id ?? null
  const detail = useQuery({
    queryKey: ['template', chosen],
    queryFn: () => api.templateDetail(chosen as number),
    enabled: chosen !== null,
  })

  const [draft, setDraft] = useState<Record<string, string>>({})
  const [gate, setGate] = useState<{ result: PreviewResult; componentId: number; qty: Record<string, string> } | null>(
    null,
  )
  const [pending, setPending] = useState(false)
  const wide = useWide()

  const d = detail.data

  const dirty = useMemo(() => {
    if (!d) return []
    const out: { componentId: number; size: string; from: string; to: string; valid: boolean }[] = []
    for (const c of d.components) {
      for (const [size, original] of Object.entries(c.qty_by_size)) {
        const edited = draft[cellKey(c.component_id, size)]
        if (edited === undefined) continue
        const a = parseDec(original)
        const b = parseDec(edited)
        const valid = b !== null
        const changed = !valid || a === null || cmp(a, b) !== 0
        if (changed) out.push({ componentId: c.component_id, size, from: original, to: edited, valid })
      }
    }
    return out
  }, [d, draft])

  const dirtyComponents = useMemo(() => new Set(dirty.map((x) => x.componentId)), [dirty])
  const allValid = dirty.every((x) => x.valid)

  if (templates.isPending || detail.isPending) {
    return (
      <Page>
        <p className="text-muted mt-8">Reading composition&hellip;</p>
      </Page>
    )
  }
  if (!d || detail.isError) {
    return (
      <Page>
        <p className="text-flag mt-8">
          The template could not be read. {(detail.error as Error | undefined)?.message}
        </p>
      </Page>
    )
  }

  const sizes = d.template.sizes
  const components = [...d.components].sort(
    (a, b) => ROLE_ORDER.indexOf(a.role) - ROLE_ORDER.indexOf(b.role),
  )
  const unavailable = d.items.filter((i) => !i.is_available_today)
  const milkModifiers = d.modifiers.filter((m) => m.target_role === 'MILK')

  async function runPreview() {
    const componentId = [...dirtyComponents][0]
    if (componentId === undefined || !d) return
    const c = d.components.find((x) => x.component_id === componentId)
    if (!c) return
    const qty: Record<string, string> = {}
    for (const [size, original] of Object.entries(c.qty_by_size)) {
      qty[size] = draft[cellKey(componentId, size)] ?? original
    }
    setPending(true)
    const result = await preview(d.template.id, componentId, qty)
    setPending(false)
    setGate({ result, componentId, qty })
  }

  /* group the grid rows by role, in the API's own role order */
  const grouped: { role: ComponentRole; rows: typeof components }[] = []
  for (const c of components) {
    const last = grouped[grouped.length - 1]
    if (last && last.role === c.role) last.rows.push(c)
    else grouped.push({ role: c.role, rows: [c] })
  }

  // Built at runtime, so it must be an inline style: Tailwind can only
  // generate classes it can see as literal text in the source.
  const gridStyle = {
    gridTemplateColumns: `minmax(7rem,1fr) repeat(${sizes.length}, 4.75rem) 2.5rem`,
  }

  return (
    <Page>
      <ScreenTitle
        title="Composition"
        sub={<Label>resolved {stamp(d.as_of)} · labour at £14.50/hr loaded</Label>}
      />

      <div className="grid gap-x-8 gap-y-8 lg:grid-cols-[13rem_minmax(0,1fr)_16rem]">
        {/* ----------------------------------------------------- left pane */}
        <aside>
          <h3 className="border-rule-strong mb-2 border-b pb-1 text-[length:var(--text-prose)] font-[600]">
            Templates
          </h3>
          {(templates.data ?? []).map((t) => (
            <button
              key={t.id}
              type="button"
              onClick={() => {
                setTemplateId(t.id)
                setDraft({})
                setGate(null)
              }}
              className={`block w-full text-left ${
                t.id === chosen ? 'border-ink border-l-2 pl-2' : 'border-transparent border-l-2 pl-2'
              }`}
            >
              <Label>{t.category}</Label>
              <div className={t.id === chosen ? 'font-[600]' : ''}>{t.name}</div>
              <Label>
                {t.item_count} {plural(t.item_count, 'item')} · {t.component_count} components ·{' '}
                {t.sizes.join('/')}
              </Label>
            </button>
          ))}
          <p className="text-muted mt-4 max-w-[26ch] leading-relaxed">
            One template drives {d.template.item_count} sellable items. That is the whole model:
            the menu is a handful of templates crossed with an axis and a size ladder, not{' '}
            {d.template.item_count > 0 ? 'three hundred' : 'many'} separate recipes. The rest of
            the proposals are still in the importer.
          </p>
        </aside>

        {/* ---------------------------------------------------- centre pane */}
        <section>
          {gate ? (
            <Gate
              result={gate.result}
              templateId={d.template.id}
              componentId={gate.componentId}
              qtyBySize={gate.qty}
              onCancel={() => setGate(null)}
              onApplied={() => {
                setGate(null)
                setDraft({})
                void detail.refetch()
              }}
            />
          ) : (
            <>
              <div className="border-rule-strong border-b pb-1">
                <h3 className="text-[length:var(--text-title)] leading-tight font-[500]">
                  {d.template.name}
                </h3>
                <Label>
                  {d.template.category} · {sizes.length} sizes
                  {d.axes.map((a) => (
                    <span key={a.axis_id}>
                      {' '}
                      · {a.name} axis, {a.options.length} options
                    </span>
                  ))}
                  {d.modifiers.length > 0 && <> · {d.modifiers.length} modifiers</>}
                </Label>
              </div>

              {/* size header */}
              {wide && (
                <div className="mt-2 grid items-baseline gap-x-2" style={gridStyle}>
                  <Label>slot</Label>
                  {sizes.map((s) => (
                    <Label key={s} className="text-right">
                      {s}
                    </Label>
                  ))}
                  <Label>unit</Label>
                </div>
              )}

              {grouped.map((g) => (
                <div key={g.role} className="mt-3">
                  <Fig size="micro" weight={500} className="text-muted">
                    {g.role}
                  </Fig>
                  {g.rows.map((c) => {
                    const isAxisSlot = c.ingredient_id === null
                    return (
                      <div key={c.component_id} className="rule-b py-[5px]">
                        <div
                          className={wide ? 'grid items-baseline gap-x-2' : ''}
                          style={wide ? gridStyle : undefined}
                        >
                          <div className="min-w-0">
                            <div className={wide ? 'truncate' : 'font-[600]'}>
                              {c.ingredient_name ?? (
                                <span className="text-muted italic">
                                  set by the {d.axes[0]?.name ?? 'axis'}
                                </span>
                              )}

                            </div>
                          </div>
                          {sizes.map((size) => {
                            const original = c.qty_by_size[size]
                            if (original === undefined) {
                              return wide ? (
                                <div key={size} className="px-1 text-right">
                                  <Fig className="text-faint">·</Fig>
                                </div>
                              ) : null
                            }
                            const key = cellKey(c.component_id, size)
                            const value = draft[key] ?? original
                            const edited = dirty.some(
                              (x) => x.componentId === c.component_id && x.size === size,
                            )
                            const invalid = !QTY_INPUT.test(value) || value === ''
                            const input = (
                              <input
                                value={value}
                                inputMode="decimal"
                                aria-label={`${c.ingredient_name ?? c.role} quantity, size ${size}`}
                                onChange={(e) => {
                                  const next = e.target.value
                                  if (next !== '' && !QTY_INPUT.test(next)) return
                                  setDraft((p) => ({ ...p, [key]: next }))
                                }}
                                className={`fig bg-slab w-full px-1 py-[2px] text-right text-[length:var(--text-fig)] ${
                                  invalid
                                    ? 'border-flag border'
                                    : edited
                                      ? 'border-ink border-b-2'
                                      : 'border-rule border-b'
                                }`}
                              />
                            )
                            return wide ? (
                              <div key={size}>{input}</div>
                            ) : (
                              <div
                                key={size}
                                className="grid grid-cols-[2.5rem_5.5rem_1fr] items-baseline gap-x-2 py-[2px]"
                              >
                                <Label>{size}</Label>
                                {input}
                                <Label>{c.unit ?? ''}</Label>
                              </div>
                            )
                          })}
                          {wide && <Label>{c.unit ?? '—'}</Label>}
                        </div>

                        {/* the per-unit price that the cost above is built from */}
                        <div className="mt-[1px] flex flex-wrap items-baseline gap-x-3">
                          {c.ingredient_cost ? (
                            <CostFig
                              cost={c.ingredient_cost}
                              suffix={`/${c.unit ?? ''}`}
                              size="sub"
                              weight={300}
                            />
                          ) : isAxisSlot ? (
                            <Label>
                              priced per option:{' '}
                              {d.axes[0]?.options
                                .map((o) => o.name + (o.season_name ? ' (seasonal)' : ''))
                                .join(' · ')}
                            </Label>
                          ) : (
                            <Label className="text-flag">no price on this ingredient</Label>
                          )}
                          {!c.is_required && <Label>optional</Label>}
                          {c.is_substitutable && <Label>substitutable</Label>}
                          {c.role === 'MILK' && milkModifiers.length > 0 && (
                            <Label>
                              {milkModifiers.length} modifiers substitute here (+
                              {milkModifiers[0]?.price_pence}p)
                            </Label>
                          )}
                        </div>
                      </div>
                    )
                  })}
                </div>
              ))}

              {/* prep seconds: a row of the same grid, because labour has a
                  recipe too and putting it elsewhere is what makes people
                  forget that. */}
              <div className="border-rule-strong mt-4 border-t-2 pt-2">
                <div
                  className={wide ? 'grid items-baseline gap-x-2' : 'flex flex-wrap gap-x-4'}
                  style={wide ? gridStyle : undefined}
                >
                  <div>Prep seconds</div>
                  {sizes.map((s) => (
                    <div key={s} className={wide ? 'text-right' : ''}>
                      {!wide && <Label>{s} </Label>}
                      <Fig weight={500}>{d.template.prep_seconds_by_size[s] ?? '—'}</Fig>
                      {!wide && <Label> s</Label>}
                    </div>
                  ))}
                  {wide && <Label>s</Label>}
                </div>
                <Label>
                  {d.template.prep_seconds_is_estimate ? '⟨estimate⟩ · ' : ''}£14.50/hr loaded ·
                  read-only: the preview endpoint prices a component quantity, so a prep-time
                  change cannot be previewed and is therefore not offered here
                </Label>
              </div>

              {wide ? (
                <p className="text-muted mt-3 max-w-[74ch] leading-relaxed">
                  A <Fig>·</Fig> means the slot is not used at that size — the 8oz cup exists
                  only at S. That is not a zero, and it is not editable here: adding a slot to a
                  size is a different change from altering a quantity, and this editor previews
                  quantities.
                </p>
              ) : (
                <p className="text-muted mt-3 max-w-[74ch] leading-relaxed">
                  A slot only lists the sizes it is actually used at — the 8oz cup exists only at
                  S. Adding a slot to a size is a different change from altering a quantity, and
                  this editor previews quantities.
                </p>
              )}

              {unavailable.length > 0 && (
                <p className="text-muted mt-3 max-w-[74ch] leading-relaxed">
                  <span className="text-ink font-[600]">
                    {unavailable.length} of {d.items.length} items should not be offered today:
                  </span>{' '}
                  {unavailable.map((i) => `${i.name} ${i.size_code}`).join(', ')}.{' '}
                  {unavailable[0]?.availability_reasons[0]} Their recipes still resolve and past
                  sales still depleted what they depleted; only today&rsquo;s menu is in question.
                </p>
              )}

              {/* the commit affordance */}
              <div className="border-rule-strong mt-5 border-t pt-3">
                {dirty.length === 0 ? (
                  <p className="text-muted leading-relaxed">
                    Change a quantity to see what it does. Nothing can be committed without
                    reading the impact preview first, and the preview never writes.
                  </p>
                ) : (
                  <>
                    <ul className="mb-2">
                      {dirty.map((x) => {
                        const c = d.components.find((y) => y.component_id === x.componentId)
                        return (
                          <li key={`${x.componentId}:${x.size}`}>
                            {c?.ingredient_name ?? c?.role}{' '}
                            <Label>size {x.size}</Label> <Qty value={x.from} unit={c?.unit} size="sub" />
                            <span className="text-muted"> → </span>
                            {x.valid ? (
                              <Qty value={x.to} unit={c?.unit} size="sub" weight={500} />
                            ) : (
                              <span className="text-flag">
                                <Fig size="sub">{x.to === '' ? '(empty)' : x.to}</Fig> not a
                                quantity
                              </span>
                            )}
                          </li>
                        )
                      })}
                    </ul>
                    {dirtyComponents.size > 1 && (
                      <p className="text-flag mb-2 max-w-[70ch] leading-relaxed">
                        The preview prices one slot at a time, so these cannot be previewed
                        together. Commit one, then the next — which is also the honest way to
                        read the consequences of each.
                      </p>
                    )}
                    <div className="flex flex-wrap items-center gap-3">
                      <button
                        type="button"
                        disabled={!allValid || dirtyComponents.size !== 1 || pending}
                        onClick={() => void runPreview()}
                        className="border-ink bg-ink text-paper hover:bg-ink-soft border px-4 py-[6px] font-[600] disabled:opacity-40"
                      >
                        {pending ? 'Pricing…' : 'Preview the impact'}
                      </button>
                      <button
                        type="button"
                        className="text-muted hover:text-ink underline underline-offset-4"
                        onClick={() => setDraft({})}
                      >
                        Discard {dirty.length} {plural(dirty.length, 'change')}
                      </button>
                    </div>
                    <p className="text-muted mt-2 max-w-[70ch] leading-relaxed">
                      Edits take effect today. Yesterday&rsquo;s costs and margins stay as they
                      were — there is no retroactive edit.
                    </p>
                  </>
                )}
              </div>
            </>
          )}
        </section>

        {/* ----------------------------------------------------- right pane */}
        <aside className={gate ? 'opacity-40' : ''}>
          <Consequences detail={d} />
        </aside>
      </div>

      <Sheet className="mt-10">
        <Marg>effective dating</Marg>
        <Body>
          <p className="text-muted max-w-[74ch] leading-relaxed">
            Every slot in this template has been effective from{' '}
            <Fig size="sub">
              {new Date(d.components[0]?.effective_from ?? d.as_of).toLocaleDateString('en-GB', {
                day: '2-digit',
                month: 'short',
                year: 'numeric',
                timeZone: 'Europe/London',
              })}
            </Fig>
            . An edit opens a new row from today and closes the old one; the closed row is what
            last month&rsquo;s sales were costed against, and it stays exactly as it was.
          </p>
        </Body>
      </Sheet>
    </Page>
  )
}
