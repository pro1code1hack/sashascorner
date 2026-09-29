/**
 * "Swaps a customer can ask for" (spec §V1.7, conflicts C-6 and C-7).
 *
 * Swaps (POS modifiers) target a ROLE, not a recipe, so the chips here are
 * read-only: a chip is on when this recipe has a slot of that role (and, for a
 * swap that replaces, the slot is swappable). Turning oat milk off for one
 * drink is the MILK slot's "Swappable" box above.
 *
 * The cards edit a swap globally. The design saved them straight away; here
 * each edit is previewed and applied from today as a new dated version
 * (`modifier_version`), so last month's sales keep the swap as it was.
 */
import { useMemo, useState } from 'react'
import { Button, Checkbox, Input, Pill, Select, StatusLine } from '../../components/ui'
import type { Outcome } from '../../components/ui'
import { recipeApi, useInvalidateMenu } from '../../lib/menu-api'
import { useOperator } from '../../lib/operator'
import type { ComponentRole, IngredientRow, RecipeEditor, Swap, SwapPreview } from '../../lib/types/menu'
import { MONEY_INPUT, QTY_INPUT, gbp, penceToPounds, poundsToPence, qtyOut, sameQty } from '../menu/common/figures'
import { usePreview } from '../menu/common/usePreview'
import { IngredientPicker } from '../menu/IngredientPicker'
import { ROLE_LABEL, ROLES } from './model'

const NONE = new Set<number>()

export function SwapsSection({ editor, ingOptions }: { editor: RecipeEditor; ingOptions: IngredientRow[] }) {
  return (
    <section className="mb-5.5" aria-labelledby="rec-swaps">
      <h2 id="rec-swaps" className="mb-1.5 text-lg font-extrabold tracking-[-.01em]">
        Swaps a customer can ask for
      </h2>
      <ul className="mb-2.5 flex flex-wrap gap-1.5" aria-label="Swaps and whether they apply here">
        {editor.swaps
          .filter((s) => s.is_active)
          .map((s) => (
            <li key={s.modifier_id}>
              <Pill tone={s.applies_here ? 'brand' : 'neutral'}>
                {s.name} +{s.price_pence === 0 ? '£0' : gbp(s.price_pence)}
                {s.applies_here && <span className="sr-only"> (applies to this recipe)</span>}
              </Pill>
            </li>
          ))}
      </ul>
      <p className="mb-2.5 text-sm text-ink-2">
        Highlighted swaps apply to this recipe because it has a slot they change. To stop a swap for this drink only, untick
        “Swappable” on that slot.
      </p>
      <div className="grid grid-cols-[repeat(auto-fill,minmax(250px,1fr))] gap-2.5">
        {editor.swaps.map((s) => (
          <SwapCard key={`${s.modifier_id}-${s.version_from}`} swap={s} ingOptions={ingOptions} />
        ))}
      </div>
      <p className="mb-5.5 mt-1.5 text-sm text-ink-2">
        Italic charges are guesses; the workbook only prices oat milk, coconut milk, syrup pumps and marshmallow. A swap
        change applies to every recipe, from today, after you check what it does.
      </p>
    </section>
  )
}

interface SwapDraft {
  action: Swap['action']
  role: ComponentRole
  ingredient_id: number | null
  qty: string
  charge: string
  guess: boolean
}

function fromSwap(s: Swap): SwapDraft {
  return {
    action: s.action,
    role: s.target_role,
    ingredient_id: s.ingredient_id,
    qty: s.qty_delta ?? '',
    charge: penceToPounds(s.price_pence),
    guess: s.price_is_estimate === true,
  }
}

function SwapCard({ swap, ingOptions }: { swap: Swap; ingOptions: IngredientRow[] }) {
  const saved = useMemo(() => fromSwap(swap), [swap])
  const [d, setD] = useState<SwapDraft>(saved)
  const [operator] = useOperator()
  const [applying, setApplying] = useState(false)
  const [message, setMessage] = useState<Outcome | null>(null)
  const invalidate = useInvalidateMenu()

  const body: Record<string, unknown> = {}
  if (d.action !== saved.action) body.action = d.action
  if (d.role !== saved.role) body.target_role = d.role
  if (d.ingredient_id !== saved.ingredient_id) body.ingredient_id = d.ingredient_id
  const q = d.qty.trim() === '' ? null : qtyOut(d.qty)
  if (!(q === null ? saved.qty === '' : sameQty(q, saved.qty))) body.qty_delta = q
  const charge = poundsToPence(d.charge)
  if (charge !== null && charge !== swap.price_pence) body.price_pence = charge
  if (d.guess !== saved.guess) body.price_is_estimate = d.guess
  const key = Object.keys(body).length ? JSON.stringify(body) : null
  const pv = usePreview<SwapPreview>(key, () => recipeApi.swapPreview(swap.modifier_id, body))
  const current = pv.key === key ? pv : null
  const ready = current?.status.kind === 'ready' && current.data !== null && current.data.refusals.length === 0

  const label = 'flex min-w-0 flex-col gap-1 text-label font-bold uppercase tracking-[.05em] text-ink-2'
  const inner = 'font-normal normal-case tracking-normal text-ink'
  const checking = current?.status.kind === 'loading'
  const previewOutcome: Outcome | null = !current
    ? null
    : checking
      ? { kind: 'info', text: 'Checking…' }
      : 'message' in current.status
        ? { kind: 'error', text: current.status.message }
        : (message ?? null)
  return (
    <div className="flex flex-col gap-2 rounded-card border border-line-soft bg-surface px-3.5 py-3">
      <div className="flex items-center gap-2 px-1">
        <span className="min-w-0 flex-1 truncate text-base font-bold">{swap.name}</span>
        {!swap.is_active && <Pill tone="muted">Not offered</Pill>}
      </div>
      <div className="grid grid-cols-2 gap-2">
        <label className={label}>
          Does
          <Select size="sm" value={d.action} onChange={(e) => setD({ ...d, action: e.target.value as Swap['action'] })} className={inner}>
            <option value="SUBSTITUTE">replaces</option>
            <option value="ADD">adds</option>
            {d.action === 'SCALE' && <option value="SCALE">scales</option>}
          </Select>
        </label>
        <label className={label}>
          To
          <Select size="sm" value={d.role} onChange={(e) => setD({ ...d, role: e.target.value as ComponentRole })} className={inner}>
            {ROLES.map((r) => (
              <option key={r} value={r}>
                {ROLE_LABEL[r]}
              </option>
            ))}
          </Select>
        </label>
      </div>
      <div className={label}>
        <span>Ingredient</span>
        <IngredientPicker
          size="sm"
          label={`Ingredient for the ${swap.name} swap`}
          placeholder="none"
          value={d.ingredient_id}
          options={ingOptions}
          inRecipe={NONE}
          onPick={(id) => setD({ ...d, ingredient_id: id })}
          onClear={() => setD({ ...d, ingredient_id: null })}
        />
      </div>
      <div className="grid grid-cols-2 gap-2">
        <label className={label}>
          Qty
          <Input
            size="sm"
            numeric
            placeholder="same as recipe"
            value={d.qty}
            onChange={(e) => QTY_INPUT.test(e.target.value) && setD({ ...d, qty: e.target.value })}
            className={inner}
          />
        </label>
        <label className={label}>
          Charge £
          <Input
            size="sm"
            numeric
            est={d.guess}
            value={d.charge}
            onChange={(e) => MONEY_INPUT.test(e.target.value) && setD({ ...d, charge: e.target.value })}
            className={inner}
          />
        </label>
      </div>
      <Checkbox className="min-h-9" checked={d.guess} onChange={(v) => setD({ ...d, guess: v })} label="Charge is a guess" />
      {key !== null && (
        <div className="border-t border-line pt-2 text-sm" aria-busy={checking || undefined}>
          <StatusLine outcome={previewOutcome} />
          {current?.data?.diff.map((l) => <p key={l}>{l}</p>)}
          {current?.data && (
            <p className="text-ink-2">
              {current.data.sales_in_window} sale{current.data.sales_in_window === 1 ? '' : 's'} carried it in the last{' '}
              {current.data.window_days} days
              {current.data.revenue_delta_pence !== null && current.data.revenue_delta_pence !== 0
                ? `; takings ${current.data.revenue_delta_pence > 0 ? '+' : '−'}${gbp(Math.abs(current.data.revenue_delta_pence))} over that time`
                : ''}
              .
            </p>
          )}
          {current?.data?.refusals.map((r) => (
            <p key={r} className="font-bold text-bad-ink">
              {r}
            </p>
          ))}
          {current?.data?.warnings.map((w) => (
            <p key={w} className="text-bad-ink">
              {w}
            </p>
          ))}
          <div className="mt-2 flex gap-2">
            <Button variant="outline" size="sm" className="flex-1" onClick={() => setD(saved)} disabled={applying}>
              Discard
            </Button>
            <Button
              variant="primary"
              size="sm"
              className="flex-[2]"
              disabled={!ready || operator === null}
              pending={applying}
              pendingLabel="Applying…"
              onClick={async () => {
                if (!operator) return
                setApplying(true)
                const r = await recipeApi.swapApply(swap.modifier_id, { ...body, actor: operator })
                setApplying(false)
                if (r.kind === 'ok') {
                  setMessage({ kind: 'ok', text: 'Applied from today.' })
                  await invalidate()
                } else setMessage({ kind: 'error', text: r.message })
              }}
            >
              Apply from today
            </Button>
          </div>
        </div>
      )}
    </div>
  )
}
