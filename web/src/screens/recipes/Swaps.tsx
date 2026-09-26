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
import { Button, Checkbox, Input, Select, cx } from '../../components/ui'
import { OperatorNeeded } from '../../components/shell/Operator'
import { recipeApi, useInvalidateMenu } from '../../lib/menu-api'
import { useOperator } from '../../lib/operator'
import type { ComponentRole, IngredientRow, RecipeEditor, Swap, SwapPreview } from '../../lib/types/menu'
import { MONEY_INPUT, QTY_INPUT, gbp, penceToPounds, poundsToPence, qtyOut, sameQty } from '../menu/common/figures'
import { usePreview } from '../menu/common/usePreview'
import { ROLES } from './model'

export function SwapsSection({ editor, ingOptions }: { editor: RecipeEditor; ingOptions: IngredientRow[] }) {
  return (
    <section className="mb-[22px]" aria-labelledby="rec-swaps">
      <h2 id="rec-swaps" className="mb-1.5 text-lg font-extrabold tracking-[-.01em]">
        Swaps a customer can ask for
      </h2>
      <div className="mb-2.5 flex flex-wrap gap-1.5">
        {editor.swaps
          .filter((s) => s.is_active)
          .map((s) => (
            <span
              key={s.modifier_id}
              className={cx(
                'rounded-card border px-3.5 py-1.5 text-base',
                s.applies_here ? 'border-brand-line bg-brand-wash font-bold text-brand-ink' : 'border-line text-ink',
              )}
            >
              {s.name} +{s.price_pence === 0 ? '£0' : gbp(s.price_pence)}
            </span>
          ))}
      </div>
      <p className="mb-2.5 text-sm text-ink-2">
        Highlighted swaps apply to this recipe because it has a slot they change. To stop a swap for this drink only, untick
        “Swappable” on that slot.
      </p>
      <div className="grid grid-cols-[repeat(auto-fill,minmax(250px,1fr))] gap-2.5">
        {editor.swaps.map((s) => (
          <SwapCard key={`${s.modifier_id}-${s.version_from}`} swap={s} ingOptions={ingOptions} />
        ))}
      </div>
      <p className="mb-[22px] mt-1.5 text-sm text-ink-2">
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
  const [message, setMessage] = useState<string | null>(null)
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
  return (
    <div className={cx('flex flex-col gap-2 rounded-card border border-line-soft bg-surface px-3.5 py-3', !swap.is_active && 'opacity-50')}>
      <div className="px-1 text-base font-bold">{swap.name}</div>
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
                {r.toLowerCase()}
              </option>
            ))}
          </Select>
        </label>
      </div>
      <label className={label}>
        Ingredient
        <Select
          size="sm"
          value={d.ingredient_id === null ? '' : String(d.ingredient_id)}
          onChange={(e) => setD({ ...d, ingredient_id: e.target.value === '' ? null : Number(e.target.value) })}
          className={inner}
        >
          <option value="">— none —</option>
          {ingOptions.map((o) => (
            <option key={o.ingredient_id} value={o.ingredient_id}>
              {o.name}
            </option>
          ))}
        </Select>
      </label>
      <div className="grid grid-cols-2 gap-2">
        <label className={label}>
          Qty
          <Input
            size="sm"
            numeric
            placeholder="same"
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
      <Checkbox checked={d.guess} onChange={(v) => setD({ ...d, guess: v })} label={<span className="text-sm">Charge is a guess</span>} />
      {key !== null && (
        <div className="border-t border-line pt-2 text-sm" aria-live="polite">
          {current?.status.kind === 'loading' && <p className="text-ink-2">Checking…</p>}
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
          {current && current.status.kind !== 'ready' && current.status.kind !== 'loading' && 'message' in current.status && (
            <p className="text-bad-ink">{current.status.message}</p>
          )}
          {message && <p role="status">{message}</p>}
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
                  setMessage('Applied from today.')
                  await invalidate()
                } else setMessage(r.message)
              }}
            >
              Apply from today
            </Button>
          </div>
          <OperatorNeeded what="change a swap" />
        </div>
      )}
    </div>
  )
}
