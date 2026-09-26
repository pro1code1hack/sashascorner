/**
 * The composition editor. Spec §10.1.
 *
 * Three panes. Left: templates by category with the item count each drives —
 * "Flavoured Latte, 9 items" is the single fact that explains the whole model.
 * Centre: component slots by role, per-size quantities in an editable grid, with
 * prep seconds per size as a row of the same grid. Right: the live consequences,
 * one size at a time.
 *
 * Quantities are strings from the API, are edited as strings, and are sent back
 * as strings. Nothing here parses one into a float — `0.1 + 0.2` in a recipe
 * editor is unacceptable (spec §10.10).
 *
 * The one rule the whole screen exists to enforce: nothing is committed without
 * the impact preview being read first, and "apply from today" is the only option
 * the gate offers, because recipe edits are effective-dated and history is never
 * rewritten (invariant 3).
 *
 * Every outer track is `minmax(0,…)`. A bare `1fr` has a min-content floor, and
 * a floor set by a ten-digit exact figure is how the consequences pane came to
 * slice a number mid-digit.
 */
import { useMemo, useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import { api, preview, type PreviewResult } from '../lib/api'
import { cmp, parseDec } from '../lib/dec'
import { plural, stamp } from '../lib/format'
import { Fig, Label, Qty } from '../components/prim'
import { Button, Card, ErrorBox, Loading, Note, PageHeader, Panel } from '../components/ui'
import { Gate } from '../components/gate'
import { Consequences } from './composition/Consequences'
import { SlotGrid } from './composition/SlotGrid'
import { TemplateList } from './composition/TemplateList'
import { ROLE_ORDER, cellKey, type DirtyCell } from './composition/model'

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
  const [gate, setGate] = useState<{
    result: PreviewResult
    componentId: number
    qty: Record<string, string>
  } | null>(null)
  const [pending, setPending] = useState(false)

  const d = detail.data

  const dirty = useMemo(() => {
    if (!d) return []
    const out: DirtyCell[] = []
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
      <>
        <PageHeader title="Composition" />
        <Loading what="Reading composition" />
      </>
    )
  }
  if (!d || detail.isError) {
    return (
      <>
        <PageHeader title="Composition" />
        <ErrorBox
          error={
            new Error(
              `The template could not be read. ${
                (detail.error as Error | undefined)?.message ?? ''
              }`.trim(),
            )
          }
        />
      </>
    )
  }

  const components = [...d.components].sort(
    (a, b) => ROLE_ORDER.indexOf(a.role) - ROLE_ORDER.indexOf(b.role),
  )
  const unavailable = d.items.filter((i) => !i.is_available_today)

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

  return (
    <>
      <PageHeader
        title="Composition"
        lede={`Resolved ${stamp(d.as_of)} · labour at £14.50/hr loaded`}
      />

      <div className="grid min-w-0 gap-x-8 gap-y-8 xl:grid-cols-[minmax(0,13rem)_minmax(0,1fr)_minmax(0,16rem)]">
        {/* ----------------------------------------------------- left pane */}
        <TemplateList
          templates={templates.data ?? []}
          chosen={chosen}
          itemCount={d.template.item_count}
          onChoose={(id) => {
            setTemplateId(id)
            setDraft({})
            setGate(null)
          }}
        />

        {/* ---------------------------------------------------- centre pane */}
        <section className="flex min-w-0 flex-col gap-5">
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
              <Card
                title={d.template.name}
                subtitle={
                  <>
                    {d.template.category} · {d.template.sizes.length} sizes
                    {d.axes.map((a) => (
                      <span key={a.axis_id}>
                        {' '}
                        · {a.name} axis, {a.options.length} options
                      </span>
                    ))}
                    {d.modifiers.length > 0 && <> · {d.modifiers.length} modifiers</>}
                  </>
                }
              >
                <SlotGrid
                  detail={d}
                  components={components}
                  draft={draft}
                  setDraft={setDraft}
                  dirty={dirty}
                />
              </Card>

              {unavailable.length > 0 && (
                <Panel
                  tone="warn"
                  title={`${unavailable.length} of ${d.items.length} items should not be offered today`}
                >
                  {unavailable.map((i) => `${i.name} ${i.size_code}`).join(', ')}.{' '}
                  {unavailable[0]?.availability_reasons[0]} Their recipes still resolve and past
                  sales still depleted what they depleted; only today&rsquo;s menu is in question.
                </Panel>
              )}

              {/* the commit affordance */}
              <Card
                title={
                  dirty.length === 0
                    ? 'No pending changes'
                    : `${dirty.length} pending ${plural(dirty.length, 'change')}`
                }
              >
                {dirty.length === 0 ? (
                  <Note>
                    Change a quantity to see what it does. Nothing can be committed without
                    reading the impact preview first, and the preview never writes.
                  </Note>
                ) : (
                  <>
                    <ul className="grid gap-1">
                      {dirty.map((x) => {
                        const c = d.components.find((y) => y.component_id === x.componentId)
                        return (
                          <li
                            key={`${x.componentId}:${x.size}`}
                            className="flex flex-wrap items-baseline gap-x-2 gap-y-0.5 border-b border-line pb-1 last:border-b-0"
                          >
                            <span className="text-[0.875rem] text-ink">
                              {c?.ingredient_name ?? c?.role}
                            </span>
                            <Label>size {x.size}</Label>
                            <Qty value={x.from} unit={c?.unit} size="sub" />
                            <span className="text-ink-4">→</span>
                            {x.valid ? (
                              <Qty value={x.to} unit={c?.unit} size="sub" weight="medium" />
                            ) : (
                              <span className="text-bad-ink">
                                <Fig size="sub" tone="bad">
                                  {x.to === '' ? '(empty)' : x.to}
                                </Fig>{' '}
                                not a quantity
                              </span>
                            )}
                          </li>
                        )
                      })}
                    </ul>

                    {dirtyComponents.size > 1 && (
                      <div className="mt-3">
                        <Panel tone="warn">
                          The preview prices one slot at a time, so these cannot be previewed
                          together. Commit one, then the next — which is also the honest way to
                          read the consequences of each.
                        </Panel>
                      </div>
                    )}

                    <div className="mt-4 flex flex-wrap items-center gap-3">
                      <Button
                        variant="primary"
                        disabled={!allValid || dirtyComponents.size !== 1 || pending}
                        onClick={() => void runPreview()}
                      >
                        {pending ? 'Pricing…' : 'Preview the impact'}
                      </Button>
                      <Button variant="ghost" onClick={() => setDraft({})}>
                        Discard {dirty.length} {plural(dirty.length, 'change')}
                      </Button>
                    </div>

                    <p className="mt-3 max-w-[70ch] text-[0.75rem] leading-[20px] text-ink-4">
                      Edits take effect today. Yesterday&rsquo;s costs and margins stay as they
                      were — there is no retroactive edit.
                    </p>
                  </>
                )}
              </Card>
            </>
          )}
        </section>

        {/* ----------------------------------------------------- right pane */}
        <aside className={`min-w-0 ${gate ? 'opacity-40' : ''}`}>
          <Consequences detail={d} />
        </aside>
      </div>

      <div className="mt-8 max-w-[80ch]">
        <Panel tone="muted" title="Effective dating">
          Every slot in this template has been effective from{' '}
          <Fig size="sub">
            {new Date(d.components[0]?.effective_from ?? d.as_of).toLocaleDateString('en-GB', {
              day: '2-digit',
              month: 'short',
              year: 'numeric',
              timeZone: 'Europe/London',
            })}
          </Fig>
          . An edit opens a new row from today and closes the old one; the closed row is what last
          month&rsquo;s sales were costed against, and it stays exactly as it was.
        </Panel>
      </div>
    </>
  )
}
