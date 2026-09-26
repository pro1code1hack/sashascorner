/**
 * The component grid: slots by role, per-size quantities in an editable cell,
 * and prep seconds as a row of the same grid — because labour has a recipe too
 * and putting it on another screen is what makes people forget that.
 *
 * Below the width at which three editable figure columns fit, the grid transposes
 * so sizes become rows. That is a real adaptation rather than a duplicated DOM,
 * and it is decided by the pane's measured width, not the window's (see
 * `useContainerWide`).
 *
 * Quantities are strings in and strings out. A dot in a cell that is not used at
 * that size is not a zero and is not editable here: adding a slot to a size is a
 * different change from altering a quantity, and this editor previews quantities.
 */
import type { ComponentRole, TemplateDetailResponse } from '../../lib/types'
import { CostFig, EST, Fig, Label } from '../../components/prim'
import { Note } from '../../components/ui'
import { QTY_INPUT, cellKey, roleLabel, type DirtyCell } from './model'
import { useContainerWide } from './useContainerWide'

/** What the wide grid needs, in px: name floor + one column per size + unit. */
function needed(sizeCount: number): number {
  return 16 * (7 + 4.75 * sizeCount + 2.5 + 0.5 * (sizeCount + 1))
}

export function SlotGrid({
  detail,
  components,
  draft,
  setDraft,
  dirty,
}: {
  detail: TemplateDetailResponse
  /** Already ordered by role. */
  components: TemplateDetailResponse['components']
  draft: Record<string, string>
  setDraft: (f: (prev: Record<string, string>) => Record<string, string>) => void
  dirty: DirtyCell[]
}) {
  const sizes = detail.template.sizes
  const [ref, wide] = useContainerWide(needed(sizes.length))
  const milkModifiers = detail.modifiers.filter((m) => m.target_role === 'MILK')

  /* Built at runtime, so it must be an inline style: Tailwind can only generate
     classes it can see as literal text in the source. */
  const gridStyle = {
    gridTemplateColumns: `minmax(7rem,1fr) repeat(${sizes.length}, 4.75rem) 2.5rem`,
  }

  /* group the grid rows by role, in the API's own role order */
  const grouped: { role: ComponentRole; rows: typeof components }[] = []
  for (const c of components) {
    const last = grouped[grouped.length - 1]
    if (last && last.role === c.role) last.rows.push(c)
    else grouped.push({ role: c.role, rows: [c] })
  }

  return (
    <div ref={ref} className="min-w-0">
      {wide && (
        <div className="grid min-w-0 items-baseline gap-x-2 pb-1.5" style={gridStyle}>
          <span className="text-[0.6875rem] font-medium text-ink-4">Slot</span>
          {sizes.map((s) => (
            <span key={s} className="text-right text-[0.6875rem] font-medium text-ink-4">
              {s}
            </span>
          ))}
          <span className="text-[0.6875rem] font-medium text-ink-4">Unit</span>
        </div>
      )}

      {grouped.map((g) => (
        <div key={g.role} className="mt-3 first:mt-0">
          <div className="mb-1 text-[0.6875rem] font-medium text-ink-3">{roleLabel(g.role)}</div>

          {g.rows.map((c) => {
            const isAxisSlot = c.ingredient_id === null
            return (
              <div key={c.component_id} className="border-b border-line py-2 last:border-b-0">
                <div
                  className={wide ? 'grid min-w-0 items-baseline gap-x-2' : ''}
                  style={wide ? gridStyle : undefined}
                >
                  <div className="min-w-0">
                    <div className={wide ? 'truncate text-ink-2' : 'font-medium text-ink'}>
                      {c.ingredient_name ?? (
                        <span className="text-ink-4 italic">
                          set by the {detail.axes[0]?.name ?? 'axis'}
                        </span>
                      )}
                    </div>
                  </div>

                  {sizes.map((size) => {
                    const original = c.qty_by_size[size]
                    if (original === undefined) {
                      return wide ? (
                        <div key={size} className="px-1 text-right">
                          <Fig className="text-ink-5">·</Fig>
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
                        className={`fig w-full min-w-0 rounded-control border bg-raised px-2 py-1 text-right text-[0.875rem] text-ink outline-none transition-colors ${
                          invalid
                            ? 'border-bad'
                            : edited
                              ? 'border-brand'
                              : 'border-line hover:border-line-2 focus:border-brand'
                        }`}
                      />
                    )
                    return wide ? (
                      <div key={size}>{input}</div>
                    ) : (
                      <div
                        key={size}
                        className="grid max-w-[15rem] grid-cols-[2.25rem_minmax(0,1fr)_auto] items-baseline gap-x-2 py-[3px]"
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
                <div className="mt-1 flex flex-wrap items-baseline gap-x-3 gap-y-0.5">
                  {c.ingredient_cost ? (
                    <CostFig
                      cost={c.ingredient_cost}
                      suffix={`/${c.unit ?? ''}`}
                      size="sub"
                      weight="light"
                    />
                  ) : isAxisSlot ? (
                    <Label>
                      priced per option:{' '}
                      {detail.axes[0]?.options
                        .map((o) => o.name + (o.season_name ? ' (seasonal)' : ''))
                        .join(' · ')}
                    </Label>
                  ) : (
                    <Label tone="bad">no price on this ingredient</Label>
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

      {/* prep seconds: a row of the same grid */}
      <div className="mt-4 border-t border-line-2 pt-3">
        <div
          className={wide ? 'grid min-w-0 items-baseline gap-x-2' : 'flex flex-wrap gap-x-4'}
          style={wide ? gridStyle : undefined}
        >
          <div className="text-ink-2">Prep seconds</div>
          {sizes.map((s) => {
            const secs = detail.template.prep_seconds_by_size[s]
            return (
              <div key={s} className={wide ? 'text-right' : ''}>
                {!wide && <Label>{s} </Label>}
                <span className={detail.template.prep_seconds_is_estimate ? EST : undefined}>
                  <Fig weight="medium">{secs ?? '—'}</Fig>
                </span>
                {!wide && <Label> s</Label>}
              </div>
            )
          })}
          {wide && <Label>s</Label>}
        </div>
        <div className="mt-2">
          <Note>
            {detail.template.prep_seconds_is_estimate ? 'Estimated · ' : ''}£14.50/hr loaded ·
            read-only: the preview endpoint prices a component quantity, so a prep-time change
            cannot be previewed and is therefore not offered here
          </Note>
        </div>
      </div>

      <div className="mt-3 max-w-[74ch]">
        {wide ? (
          <Note>
            A <span className="fig text-ink-3">·</span> means the slot is not used at that size —
            the 8oz cup exists only at S. That is not a zero, and it is not editable here: adding
            a slot to a size is a different change from altering a quantity, and this editor
            previews quantities.
          </Note>
        ) : (
          <Note>
            A slot only lists the sizes it is actually used at — the 8oz cup exists only at S.
            Adding a slot to a size is a different change from altering a quantity, and this
            editor previews quantities.
          </Note>
        )}
      </div>
    </div>
  )
}
