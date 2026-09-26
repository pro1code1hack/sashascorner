/**
 * The ingredient drawer (§1.2): 400px, canvas-toned, beside the list.
 *
 * Reads: the row already in hand, plus `GET /api/stock/{id}` for the count
 * history. Writes, each signed with the operator's name (DECISIONS 6):
 * tier (C1: up to A only on earned drift history), checklist answer, walk-in
 * delivery, write-off (C11), shelf life (C4: a checked value, never an
 * estimate), reorder level. Every refusal is shown verbatim.
 */
import { useEffect, useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import { OperatorNeeded } from '../../components/shell/Operator'
import { Button, Drawer, Input, Meter, Select, cx } from '../../components/ui'
import { confirmShelfLife } from '../../lib/api'
import { parseDec } from '../../lib/dec'
import { gbp } from '../../lib/format'
import { useOperator } from '../../lib/operator'
import { KEYS, stockApi, stockWrites } from '../../lib/stock-api'
import type { StockRow, Tier, WriteOffReason } from '../../lib/types/stock'
import { addDays, dayMonth, driftShown, fmtD, fmtQ, todayIso, unitWord } from './fmt'
import { TIER_NOTE, trustOf } from './model'
import { OutcomeLine, useWrite } from './writes'

const AFTER_STOCK_WRITE = (id: number) => [KEYS.stock, KEYS.stockDetail(id), KEYS.draft]

export function StockDrawer({
  row,
  onClose,
  onCountNow,
}: {
  row: StockRow
  onClose: () => void
  onCountNow: () => void
}) {
  const [operator] = useOperator()
  return (
    <Drawer
      open
      onClose={onClose}
      title={row.name}
      context={row.category ?? undefined}
      width={400}
      compactWidth={400}
      tone="canvas"
      titleSize="lg"
    >
      {operator === null && <OperatorNeeded what="record anything here" />}
      <TierRow row={row} />
      {row.tier === 'C' ? (
        <ChecklistCard row={row} />
      ) : (
        <>
          <EstimateCard row={row} />
          <LastCountCard row={row} />
          <Batches row={row} />
          <DeliveryRow row={row} />
          <WriteOffRow row={row} />
        </>
      )}
      <Keeps row={row} />
      {row.tier !== 'C' && <CountHistory row={row} onCountNow={onCountNow} />}
    </Drawer>
  )
}

/* --------------------------------------------------------------- tier (C1) -- */

function TierRow({ row }: { row: StockRow }) {
  const [operator] = useOperator()
  const [target, setTarget] = useState<Tier | null>(null)
  const [reason, setReason] = useState('')
  const w = useWrite()
  useEffect(() => {
    setTarget(null)
    setReason('')
    w.setOutcome(null)
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [row.ingredient_id])

  const earned = row.drift.clean_streak >= row.drift.required_streak && row.drift.has_observation
  const submit = async () => {
    if (target === null || operator === null) return
    await w.run(() => stockWrites.tier(row.ingredient_id, { tier: target, changed_by: operator, reason }), {
      invalidate: AFTER_STOCK_WRITE(row.ingredient_id),
      ok: (d) => d.note,
      after: () => {
        setTarget(null)
        setReason('')
      },
    })
  }
  return (
    <section aria-label="Tier" className="flex flex-col gap-2">
      <div className="flex flex-wrap items-center gap-1.5 text-base text-ink-2">
        <span>Tier</span>
        {(['A', 'B', 'C'] as const).map((t) => {
          const on = row.tier === t
          const picked = target === t
          return (
            <button
              key={t}
              type="button"
              aria-pressed={on}
              onClick={() => setTarget(on ? null : t)}
              className={cx(
                'h-6 rounded-sm border px-[7px] text-xs font-bold tracking-[.04em]',
                on || picked
                  ? 'border-brand-line bg-brand-wash text-brand-ink'
                  : 'border-line-strong bg-surface text-ink hover:bg-canvas',
              )}
            >
              {t}
            </button>
          )
        })}
        <span className="text-ink-2">{TIER_NOTE[row.tier]}</span>
      </div>
      {target !== null && target !== row.tier && (
        <div className="flex flex-col gap-2 rounded-card border border-line bg-surface px-3.5 py-3 text-base">
          {target === 'A' ? (
            <p>
              <b>Promote to A.</b>{' '}
              {row.tier === 'C'
                ? 'A checklist item has no counts to earn tier A with: move it to B and count it first.'
                : earned
                  ? `Earned: ${row.drift.clean_streak} counts in a row under 10% (needs ${row.drift.required_streak}). Auto-ordering still waits for the next count, and every order is confirmed in Telegram.`
                  : `Not earned yet: ${row.drift.clean_streak} of ${row.drift.required_streak} counts in a row under 10%. Tier A is earned through counts, never assigned.`}
            </p>
          ) : (
            <p>
              Move to tier {target}: {TIER_NOTE[target]}.
              {row.tier === 'A' && row.drift.auto_order_enabled
                ? ' Auto-ordering stays recorded as on until the gate re-runs at the next count.'
                : ''}
            </p>
          )}
          <label className="flex flex-col gap-1 text-xs font-bold text-ink-2">
            Why
            <Input
              size="sm"
              value={reason}
              onChange={(e) => setReason(e.target.value)}
              placeholder="What the next person should know"
            />
          </label>
          <div className="flex gap-2">
            <Button
              variant="primary"
              size="sm"
              disabled={operator === null || reason.trim() === ''}
              pending={w.pending}
              pendingLabel="Saving…"
              onClick={submit}
            >
              {target === 'A' ? 'Promote to A' : `Move to ${target}`}
            </Button>
            <Button variant="ghost" size="sm" onClick={() => setTarget(null)}>
              Keep {row.tier}
            </Button>
          </div>
        </div>
      )}
      <OutcomeLine outcome={w.outcome} />
    </section>
  )
}

/* ------------------------------------------------------------- tier C card -- */

function ChecklistCard({ row }: { row: StockRow }) {
  const [operator] = useOperator()
  const w = useWrite()
  const answer = async (status: 'OK' | 'LOW') => {
    if (operator === null) return
    await w.run(() => stockWrites.checklist(row.ingredient_id, { status, responded_by: operator }), {
      invalidate: AFTER_STOCK_WRITE(row.ingredient_id),
      ok: (d) => `Marked ${d.status === 'LOW' ? 'running low' : 'plenty'} by ${d.responded_by}.`,
    })
  }
  const current = row.checklist?.status ?? null
  return (
    <section className="flex flex-col gap-2.5 rounded-card border border-line px-3 py-3 text-base">
      <p>Checklist item: not worked out from sales. Staff mark it OK or low.</p>
      <div className="flex gap-2">
        {(
          [
            ['OK', 'Plenty'],
            ['LOW', 'Running low'],
          ] as const
        ).map(([status, label]) => (
          <button
            key={status}
            type="button"
            aria-pressed={current === status}
            disabled={operator === null || w.pending}
            onClick={() => answer(status)}
            className={cx(
              'flex-1 rounded-card-lg border px-3 py-1.5 text-base disabled:opacity-50',
              current === status
                ? 'border-brand-line bg-brand-wash font-bold text-brand-ink'
                : 'border-line bg-surface hover:bg-canvas',
            )}
          >
            {label}
          </button>
        ))}
      </div>
      {row.checklist && (
        <p className="text-sm text-ink-2">
          Last answer: {row.checklist.status === 'LOW' ? 'running low' : 'plenty'}, {fmtD(row.checklist.responded_at)}, by{' '}
          {row.checklist.responded_by}.
        </p>
      )}
      <OutcomeLine outcome={w.outcome} />
    </section>
  )
}

/* ------------------------------------------------------------ the estimate -- */

function neg(q: string): string {
  return q.startsWith('-') ? q.slice(1) : q
}

function EstimateCard({ row }: { row: StockRow }) {
  const oh = row.on_hand
  const sc = row.since_count
  let how: string
  if (!oh.has_count_basis || oh.basis_count_qty === null) {
    how = 'Count it once and the app keeps track from there.'
  } else if (!sc) {
    how = `Counted ${fmtQ(oh.basis_count_qty, row.unit)} on ${fmtD(oh.basis_counted_at)}, then worked out from sales and deliveries since.`
  } else {
    const parts = [
      `Counted ${fmtQ(oh.basis_count_qty, row.unit)} on ${fmtD(oh.basis_counted_at)}`,
      `+ ${fmtQ(sc.delivered, row.unit)} delivered`,
      `− ${fmtQ(neg(sc.sold), row.unit)} sold over ${sc.days} ${sc.days === 1 ? 'day' : 'days'}`,
    ]
    const wasted = parseDec(sc.wasted)
    const expired = parseDec(sc.expired)
    if (wasted && wasted.u !== 0n) parts.push(`− ${fmtQ(neg(sc.wasted), row.unit)} written off`)
    if (expired && expired.u !== 0n) parts.push(`− ${fmtQ(neg(sc.expired), row.unit)} gone out of date`)
    const adj = parseDec(sc.adjusted)
    if (adj && adj.u !== 0n) parts.push(`${adj.u < 0n ? '−' : '+'} ${fmtQ(neg(sc.adjusted), row.unit)} adjusted`)
    how = `${parts.join(', ')}.`
  }
  return (
    <section className="rounded-card border border-line px-3.5 py-3">
      <div className="text-sm text-ink-2">We think you have</div>
      <div className="fig text-4xl italic">
        {oh.has_count_basis ? fmtQ(oh.qty, row.unit) : 'not counted yet'}
      </div>
      <div className="mb-2 text-sm text-ink-2">worked out, not counted</div>
      <p className="text-base text-ink-2">{how}</p>
      {oh.is_negative && (
        <p className="mt-1 text-sm text-bad-ink">Below zero: more has been used than the last count had. Count it.</p>
      )}
    </section>
  )
}

/* ------------------------------------------------------------- last count -- */

const TRUST_WORD: Record<string, string> = {
  trusted: 'Trusted',
  drifting: 'Drifting',
  excluded: 'Excluded',
  never_counted: 'Never counted',
  not_yet_judged: 'Not yet judged',
}

function LastCountCard({ row }: { row: StockRow }) {
  const d = row.drift
  const t = trustOf(row)
  const oh = row.on_hand
  const attribution = d.attribution
  const share = attribution?.expiry_share ?? null
  const pctAbs = d.drift_pct === null ? null : Math.abs(d.drift_pct)
  let driftLine: string
  if (!oh.has_count_basis) driftLine = ''
  else if (!d.has_observation || d.drift_pct === null || d.theoretical_qty === null)
    driftLine = 'First count, nothing to compare yet.'
  else
    driftLine = `The app expected ${fmtQ(d.theoretical_qty, row.unit)}: out by ${driftShown(d.drift_pct).replace(/^[+−]/, '')} (${
      d.drift_pct > 0 ? 'less' : 'more'
    } than expected).`
  let autoLine: string
  if (row.tier !== 'A') autoLine = 'Not tier A, so never auto-ordered.'
  else if (t === 'trusted')
    autoLine = 'Two counts in a row under 10%: can be auto-ordered (still confirmed in Telegram).'
  else autoLine = 'Needs two counts in a row under 10% before it can be auto-ordered.'

  return (
    <section className="rounded-card border border-line px-3.5 py-3">
      <div className="flex items-center gap-2">
        <h3 className="text-md font-extrabold">Last count</h3>
        <span
          className={cx(
            'rounded-button border px-2 text-xs',
            t === 'excluded' ? 'border-alert text-bad-ink' : t === 'trusted' ? 'border-ink text-ink' : 'border-ink-3 text-ink-2',
          )}
        >
          {TRUST_WORD[t]}
        </span>
      </div>
      <div className="fig text-[19px] leading-7">
        {oh.has_count_basis && oh.basis_count_qty !== null
          ? `${fmtQ(oh.basis_count_qty, row.unit)} on ${fmtD(oh.basis_counted_at)}`
          : 'never'}
      </div>
      {driftLine && <p className="my-1 text-base text-ink-2">{driftLine}</p>}
      {attribution && pctAbs !== null && pctAbs >= 5 && share !== null && (
        <>
          <div className="my-2 grid grid-cols-[130px_minmax(0,1fr)_44px] items-center gap-x-2 gap-y-1.5 text-sm">
            <span>Went out of date</span>
            <Meter
              label={`Went out of date: ${Math.round(share * 100)}%`}
              segments={[
                { key: 'e', value: share, tone: 'brand' },
                { key: 'r', value: 1 - share, tone: 'muted' },
              ]}
            />
            <span className="fig text-right">{Math.round(share * 100)}%</span>
            <span>Measuring / recipe</span>
            <Meter
              label={`Measuring or recipe: ${Math.round((1 - share) * 100)}%`}
              segments={[
                { key: 'm', value: 1 - share, tone: 'brand' },
                { key: 'r', value: share, tone: 'muted' },
              ]}
            />
            <span className="fig text-right">{Math.round((1 - share) * 100)}%</span>
          </div>
          <p className="border-t border-line pt-1.5 text-base">
            {attribution.cause === 'EXPIRY' ? (
              <>
                <b>Order less; don’t change the recipe.</b> Most of the gap is stock that went out of date. Changing the
                recipe would hide a buying problem.
              </>
            ) : attribution.cause === 'MEASUREMENT' ? (
              <>
                <b>Check the recipe or how it’s measured.</b> Most of the gap isn’t explained by waste: quantities per
                drink, spills or a missed delivery.
              </>
            ) : (
              <>
                <b>{attribution.headline.charAt(0) + attribution.headline.slice(1).toLowerCase()}.</b> {attribution.action}
              </>
            )}
          </p>
        </>
      )}
      <p className="mt-1.5 text-sm text-ink-2">{autoLine}</p>
    </section>
  )
}

/* ---------------------------------------------------------------- batches -- */

function Batches({ row }: { row: StockRow }) {
  const gap = parseDec(row.batch_coverage_gap)
  const open = row.batches.filter((b) => {
    const q = parseDec(b.qty_remaining)
    return q !== null && q.u > 0n
  })
  return (
    <section className="flex flex-col gap-1.5">
      <div className="flex items-baseline gap-2">
        <h3 className="text-md font-extrabold">Batches</h3>
        <span className="text-xs text-ink-2">used oldest-first by use-by date</span>
      </div>
      {open.length === 0 ? (
        <p className="text-sm text-ink-2">No batches with stock left.</p>
      ) : (
        <div className="grid grid-cols-[1fr_60px_1fr_64px] gap-x-2 text-base">
          <span className="border-b border-line pb-1 text-sm text-ink-2">Came in</span>
          <span className="border-b border-line pb-1 text-right text-sm text-ink-2">Qty</span>
          <span className="border-b border-line pb-1 text-sm text-ink-2">Use by</span>
          <span className="border-b border-line pb-1 text-right text-sm text-ink-2">Left</span>
          {open.map((b) => {
            const soon = b.days_left !== null && b.days_left <= 3
            return (
              <div key={b.batch_id} className="contents">
                <span className="border-b border-line py-1">{dayMonth(b.received_at)}</span>
                <span className="fig border-b border-line py-1 text-right">
                  {b.qty_received ? fmtQ(b.qty_received, row.unit, { suffix: false }) : '—'}
                </span>
                <span className={cx('border-b border-line py-1', soon ? 'text-bad-ink' : '')}>
                  {b.effective_expiry
                    ? `${fmtD(b.effective_expiry)} · ${b.days_left}d${b.expiry_assumed ? ' (assumed)' : ''}`
                    : 'keeps'}
                </span>
                <span className="fig border-b border-line py-1 text-right italic">
                  {fmtQ(b.qty_remaining, row.unit, { suffix: false })}
                </span>
              </div>
            )
          })}
        </div>
      )}
      {gap !== null && gap.u !== 0n && (
        <p className="text-sm text-ink-2">
          {fmtQ(row.batch_coverage_gap, row.unit)} on hand that no delivery accounts for; it can’t expire or be written
          off until counted.
        </p>
      )}
    </section>
  )
}

/* --------------------------------------------------------- delivery (walk-in) -- */

function DeliveryRow({ row }: { row: StockRow }) {
  const [operator] = useOperator()
  const [qty, setQty] = useState('')
  const shelf = row.shelf_life.shelf_life_days
  const assumed = shelf === null ? '' : addDays(todayIso(), shelf)
  const [date, setDate] = useState(assumed)
  const [edited, setEdited] = useState(false)
  const w = useWrite()
  useEffect(() => {
    setQty('')
    setDate(assumed)
    setEdited(false)
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [row.ingredient_id])
  const d = parseDec(qty)
  const ok = d !== null && d.u > 0n && operator !== null
  const record = async () => {
    if (!ok || operator === null) return
    await w.run(
      () =>
        stockWrites.delivery(row.ingredient_id, {
          qty: qty.trim(),
          received_by: operator,
          // Untouched default = let the server assume it, and say so on the batch (C16).
          ...(edited && date !== '' ? { expires_on: date } : {}),
        }),
      {
        invalidate: AFTER_STOCK_WRITE(row.ingredient_id),
        ok: (r) =>
          `Recorded ${fmtQ(r.qty, row.unit)}${r.expires_at ? `, use by ${dayMonth(r.expires_at)}${r.expiry_assumed ? ' (assumed)' : ''}` : ''}.`,
        after: () => {
          setQty('')
          setDate(assumed)
          setEdited(false)
        },
      },
    )
  }
  return (
    <section className="flex flex-col gap-1">
      <div className="flex flex-wrap items-center gap-1.5 text-sm">
        <span>Delivery</span>
        <Input
          size="xs"
          numeric
          aria-label={`Delivery quantity in ${unitWord(row.unit)}`}
          placeholder="qty"
          value={qty}
          onChange={(e) => setQty(e.target.value)}
          className="w-14!"
        />
        <span>use by</span>
        <Input
          size="xs"
          type="date"
          aria-label="Use-by date"
          value={date}
          onChange={(e) => {
            setDate(e.target.value)
            setEdited(true)
          }}
          className={cx('w-[132px]! text-xs', !edited && date !== '' && 'italic')}
        />
        <Button variant="outline" size="sm" disabled={!ok} pending={w.pending} pendingLabel="Saving…" onClick={record}>
          Record
        </Button>
      </div>
      {!edited && date !== '' && <p className="text-xs text-ink-2">Date assumed from shelf life until you change it.</p>}
      <OutcomeLine outcome={w.outcome} />
    </section>
  )
}

/* ------------------------------------------------------------- write-off (C11) -- */

const REASONS: ReadonlyArray<{ value: WriteOffReason; label: string }> = [
  { value: 'WENT_OFF', label: 'Went out of date' },
  { value: 'SPILLED', label: 'Spilled / dropped' },
  { value: 'STAFF', label: 'Staff drinks' },
  { value: 'OTHER', label: 'Other' },
]

function WriteOffRow({ row }: { row: StockRow }) {
  const [operator] = useOperator()
  const [qty, setQty] = useState('')
  const [reason, setReason] = useState<WriteOffReason>('WENT_OFF')
  const [note, setNote] = useState('')
  const w = useWrite()
  useEffect(() => {
    setQty('')
    setNote('')
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [row.ingredient_id])
  const d = parseDec(qty)
  const ok = d !== null && d.u > 0n && operator !== null && (reason !== 'OTHER' || note.trim() !== '')
  const submit = async () => {
    if (!ok || operator === null) return
    await w.run(
      () =>
        stockWrites.writeOff(row.ingredient_id, {
          qty: qty.trim(),
          reason,
          recorded_by: operator,
          ...(note.trim() ? { note: note.trim() } : {}),
        }),
      {
        invalidate: AFTER_STOCK_WRITE(row.ingredient_id),
        ok: (r) => {
          const v = r.value.pence === null ? 'value unknown' : `${gbp(r.value.pence)}${r.value.is_estimate ? ' (est.)' : ''}`
          const short = parseDec(r.shortfall_qty)
          return `Wrote off ${fmtQ(r.qty, row.unit)} · ${v}.${
            short && short.u > 0n ? ` ${fmtQ(r.shortfall_qty, row.unit)} was more than the batches held.` : ''
          }`
        },
        after: () => {
          setQty('')
          setNote('')
        },
      },
    )
  }
  return (
    <section className="flex flex-col gap-1">
      <div className="flex flex-wrap items-center gap-1.5 text-sm">
        <span>Write off</span>
        <Input
          size="xs"
          numeric
          aria-label={`Write-off quantity in ${unitWord(row.unit)}`}
          placeholder="qty"
          value={qty}
          onChange={(e) => setQty(e.target.value)}
          className="w-14!"
        />
        <Select
          size="xs"
          aria-label="Why"
          value={reason}
          onChange={(e) => setReason(e.target.value as WriteOffReason)}
          className="w-auto! text-sm"
        >
          {REASONS.map((r) => (
            <option key={r.value} value={r.value}>
              {r.label}
            </option>
          ))}
        </Select>
        <Button variant="danger" size="sm" disabled={!ok} pending={w.pending} pendingLabel="Saving…" onClick={submit}>
          Write off
        </Button>
      </div>
      {reason === 'OTHER' && (
        <Input
          size="xs"
          aria-label="What happened"
          placeholder="What happened (needed for Other)"
          value={note}
          onChange={(e) => setNote(e.target.value)}
        />
      )}
      <OutcomeLine outcome={w.outcome} />
    </section>
  )
}

/* -------------------------------------------------- how long it keeps (C4) -- */

function Keeps({ row }: { row: StockRow }) {
  const [operator] = useOperator()
  const sl = row.shelf_life
  const est = sl.source === 'ESTIMATE'
  const [days, setDays] = useState(sl.shelf_life_days?.toString() ?? '')
  const [open, setOpen] = useState(sl.open_life_days?.toString() ?? '')
  const [source, setSource] = useState<'supplier' | 'packaging' | null>(null)
  const [reorder, setReorder] = useState(row.par?.min_qty ?? '')
  const shelfW = useWrite()
  const parW = useWrite()
  useEffect(() => {
    setDays(sl.shelf_life_days?.toString() ?? '')
    setOpen(sl.open_life_days?.toString() ?? '')
    setSource(null)
    setReorder(row.par?.min_qty ?? '')
    shelfW.setOutcome(null)
    parW.setOutcome(null)
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [row.ingredient_id, sl.shelf_life_days, sl.open_life_days, row.par?.min_qty])

  const doesNotExpire = sl.shelf_life_days === null
  const shelfEdited =
    !doesNotExpire &&
    (days !== (sl.shelf_life_days?.toString() ?? '') || open !== (sl.open_life_days?.toString() ?? '') || source !== null)
  const daysOk = /^\d+$/.test(days.trim()) && Number(days) >= 1
  const openOk = open.trim() === '' || (/^\d+$/.test(open.trim()) && Number(open) >= 1)

  const saveShelf = async () => {
    if (!daysOk || !openOk || source === null) return
    await shelfW.run(
      () =>
        confirmShelfLife(row.ingredient_id, {
          shelf_life_days: Number(days),
          open_life_days: open.trim() === '' ? null : Number(open),
          source,
        }),
      {
        invalidate: AFTER_STOCK_WRITE(row.ingredient_id),
        ok: (r) =>
          r.usable_days_changed_by === null || r.usable_days_changed_by === 0
            ? `Saved. Orders are capped at ${r.usable_days_after} usable days.`
            : `Saved. A single order may now cover ${Math.abs(r.usable_days_changed_by)} ${
                r.usable_days_changed_by > 0 ? 'more' : 'fewer'
              } ${Math.abs(r.usable_days_changed_by) === 1 ? 'day' : 'days'}.`,
        after: () => setSource(null),
      },
    )
  }

  const reorderChanged = row.par !== null && row.par !== undefined && reorder.trim() !== row.par.min_qty
  const saveReorder = async () => {
    if (!reorderChanged || operator === null) return
    const d = parseDec(reorder)
    if (d === null || d.u < 0n) {
      parW.setOutcome({ tone: 'bad', text: 'A reorder level is a plain number, like 4 or 2.5.' })
      return
    }
    await parW.run(() => stockWrites.par(row.ingredient_id, { min_qty: reorder.trim(), changed_by: operator }), {
      invalidate: AFTER_STOCK_WRITE(row.ingredient_id),
      ok: (r) => `Reorder at ${fmtQ(r.min_qty_after, row.unit)} (was ${fmtQ(r.min_qty_before, row.unit)}).`,
    })
  }

  let note: string
  if (doesNotExpire) note = 'Doesn’t expire, so orders for this are not capped by shelf life.'
  else if (est && sl.is_perishable) note = 'Estimated — confirm it to make the order cap real.'
  else
    note = `Caps orders: never more than can be used in ${sl.usable_days ?? sl.shelf_life_days} days (less delivery time).`

  return (
    <section className="flex flex-col gap-2">
      <h3 className="text-md font-extrabold">How long it keeps</h3>
      <div className="grid grid-cols-3 gap-2">
        <label className="flex min-w-0 flex-col gap-1 text-xs text-ink-2">
          Unopened (days)
          <Input
            size="sm"
            numeric
            value={doesNotExpire ? '' : days}
            placeholder={doesNotExpire ? 'keeps' : 'not set'}
            disabled={doesNotExpire}
            onChange={(e) => setDays(e.target.value)}
            est={est && days === (sl.shelf_life_days?.toString() ?? '')}
            missing={est && sl.is_perishable}
          />
        </label>
        <label className="flex min-w-0 flex-col gap-1 text-xs text-ink-2">
          Opened (days)
          <Input
            size="sm"
            numeric
            value={open}
            placeholder="—"
            disabled={doesNotExpire}
            onChange={(e) => setOpen(e.target.value)}
            est={est && open !== '' && open === (sl.open_life_days?.toString() ?? '')}
          />
        </label>
        <label className="flex min-w-0 flex-col gap-1 text-xs text-ink-2">
          Reorder at
          <Input
            size="sm"
            numeric
            value={row.tier === 'C' || !row.par ? '' : reorder}
            placeholder="—"
            disabled={row.tier === 'C' || !row.par || operator === null}
            onChange={(e) => setReorder(e.target.value)}
            onBlur={saveReorder}
            onKeyDown={(e) => {
              if (e.key === 'Enter') void saveReorder()
            }}
          />
        </label>
      </div>
      <p className="text-sm text-ink-2">{note}</p>
      {shelfEdited && (
        <div className="flex flex-col gap-2 rounded-button border border-line bg-surface px-3 py-2.5 text-base">
          <span className="text-sm text-ink-2">Where does this come from?</span>
          <div className="flex flex-wrap gap-2">
            {(
              [
                ['supplier', 'Supplier told us'],
                ['packaging', 'Read off the pack'],
              ] as const
            ).map(([v, label]) => (
              <button
                key={v}
                type="button"
                aria-pressed={source === v}
                onClick={() => setSource(v)}
                className={cx(
                  'rounded-button border px-3.5 py-1.5 text-base',
                  source === v ? 'border-brand-line bg-brand-wash font-bold text-brand-ink' : 'border-line-strong bg-surface',
                )}
              >
                {label}
              </button>
            ))}
          </div>
          <div>
            <Button
              variant="primary"
              size="sm"
              disabled={!daysOk || !openOk || source === null}
              pending={shelfW.pending}
              pendingLabel="Saving…"
              onClick={saveShelf}
            >
              Save shelf life
            </Button>
          </div>
          {(!daysOk || !openOk) && <p className="text-sm text-bad-ink">Whole days, 1 or more.</p>}
        </div>
      )}
      <OutcomeLine outcome={shelfW.outcome} />
      <OutcomeLine outcome={parW.outcome} />
    </section>
  )
}

/* ---------------------------------------------------------- count history -- */

function CountHistory({ row, onCountNow }: { row: StockRow; onCountNow: () => void }) {
  const q = useQuery({
    queryKey: KEYS.stockDetail(row.ingredient_id),
    queryFn: () => stockApi.stockDetail(row.ingredient_id),
    staleTime: 30_000,
  })
  const counts = q.data?.counts ?? []
  return (
    <section className="flex flex-col gap-1">
      <h3 className="text-md font-extrabold">Count history</h3>
      {q.isPending ? (
        <p className="text-sm text-ink-2">Loading…</p>
      ) : q.isError ? (
        <p className="text-sm text-ink-2">{q.error instanceof Error ? q.error.message : 'Could not load.'}</p>
      ) : counts.length === 0 ? (
        <p className="text-sm text-ink-2">Never counted.</p>
      ) : (
        <ul>
          {counts.map((c) => {
            const d = c.drift_pct === null ? null : Math.abs(c.drift_pct)
            return (
              <li key={c.stock_count_id} className="flex justify-between gap-2 border-b border-line py-[3px] text-base">
                <span>{fmtD(c.counted_at)}</span>
                <span className="fig">{fmtQ(c.counted_qty, row.unit)}</span>
                <span className={cx('fig', d !== null && d > 15 ? 'text-bad-ink' : 'text-ink-2')}>
                  {d === null ? 'first' : `${(Math.round(d * 10) / 10).toFixed(1)}% out`}
                </span>
              </li>
            )
          })}
        </ul>
      )}
      <Button variant="primary" size="sm" block className="mt-2" onClick={onCountNow}>
        Count this now
      </Button>
    </section>
  )
}
