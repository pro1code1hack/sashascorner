/**
 * The sections of one ingredient's stock page (StockItemPage, `#/stock/<id>`),
 * laid out like the menu item page (owner, 2026-09-26: "the stock itself should
 * be same as menu"): white panels, an `h2` per section, uppercase table labels,
 * labelled fields and full-width buttons in the side column.
 *
 * Reads: the row already in hand, plus `GET /api/stock/{id}` for the count
 * history. Writes, each signed with the operator's name (DECISIONS 6): tier
 * (one tap, no "why" prompt — DECISIONS 22; up to A only on earned drift
 * history, which the server enforces), checklist answer, walk-in delivery,
 * write-off (C11), shelf life (C4: a checked value, never an estimate),
 * reorder level. Every refusal is shown verbatim.
 */
import { useEffect, useState } from 'react'
import type { ReactNode, RefObject } from 'react'
import { useQuery } from '@tanstack/react-query'
import { Button, Field, Input, Meter, Select, TBody, THead, Table, Td, Th, Tr, cx } from '../../components/ui'
import { confirmShelfLife } from '../../lib/api'
import { parseDec } from '../../lib/dec'
import { gbp } from '../../lib/format'
import { href } from '../../lib/router'
import { useOperator } from '../../lib/operator'
import { KEYS, stockApi, stockWrites } from '../../lib/stock-api'
import type { StockRow, Tier, WriteOffReason } from '../../lib/types/stock'
import { addDays, dayMonth, driftShown, fmtD, fmtQ, todayIso, unitWord } from './fmt'
import { TIER_NOTE, runsOutCell, trustOf } from './model'
import { OutcomeLine, useWrite } from './writes'

const AFTER_STOCK_WRITE = (id: number) => [KEYS.stock, KEYS.stockDetail(id), KEYS.draft]

/* ------------------------------------------------------------ shared bits -- */

export function Panel({ children }: { children: ReactNode }) {
  return <div className="rounded-card-lg bg-surface p-4 shadow-raised sm:p-5">{children}</div>
}

function SectionTitle({ id, title, note, action }: { id: string; title: string; note?: ReactNode; action?: ReactNode }) {
  return (
    <div className="flex flex-wrap items-baseline justify-between gap-x-3 gap-y-1">
      <h2 id={id} className="text-xl font-extrabold tracking-[-.01em]">
        {title}
      </h2>
      {note && <span className="text-sm text-ink-2">{note}</span>}
      {action}
    </div>
  )
}

/** Side-column section heads: small uppercase, but still `h2` (the page outline is h1 → h2s). */
const SIDE_HEAD = 'text-label font-bold uppercase tracking-[.06em] text-ink-3'

function neg(q: string): string {
  return q.startsWith('-') ? q.slice(1) : q
}

/* ------------------------------------------------- overview (invariant 6) -- */

/**
 * The three figures, side by side: what the app thinks is there (italic,
 * worked out), the last count (upright, dated), and when it runs out.
 * Theoretical and counted are told apart by weight, style and label.
 */
export function Overview({ row }: { row: StockRow }) {
  const oh = row.on_hand
  const sc = row.since_count
  const ro = runsOutCell(row)
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
    <section aria-labelledby="st-overview" className="flex flex-col gap-3">
      <SectionTitle id="st-overview" title="On the shelf" />
      <div className="grid gap-4 border-b border-line pb-4 sm:grid-cols-3">
        <div className="flex min-w-0 flex-col gap-0.5">
          <span className={SIDE_HEAD}>We think you have</span>
          <span className="fig text-4xl italic leading-tight">{oh.has_count_basis ? fmtQ(oh.qty, row.unit) : '—'}</span>
          <span className="text-sm text-ink-2">{oh.has_count_basis ? 'worked out, not counted' : 'not counted yet'}</span>
        </div>
        <div className="flex min-w-0 flex-col gap-0.5">
          <span className={SIDE_HEAD}>Last counted</span>
          <span className="fig text-4xl leading-tight">
            {oh.has_count_basis && oh.basis_count_qty !== null ? fmtQ(oh.basis_count_qty, row.unit) : '—'}
          </span>
          <span className="text-sm text-ink-2">{oh.has_count_basis ? fmtD(oh.basis_counted_at) : 'never'}</span>
        </div>
        <div className="flex min-w-0 flex-col gap-0.5">
          <span className={SIDE_HEAD}>Runs out</span>
          {ro.reason || !ro.text ? (
            // Invariant 9: the reason where the date would be, in full, no hover needed.
            <span className="pt-2 text-base text-ink-2">
              {ro.text || 'no forecast yet'}
              {ro.title && ro.title !== ro.text && <span className="block text-sm">{ro.title}</span>}
            </span>
          ) : (
            <span className={cx('fig text-4xl leading-tight', ro.alert && 'text-bad-ink')}>{ro.text}</span>
          )}
          <a href={href('/stock', { tab: 'buy' })} className="text-sm font-bold text-brand-ink">
            What to buy ›
          </a>
        </div>
      </div>
      <p className="text-base text-ink-2">{how}</p>
      {oh.is_negative && (
        <p className="text-sm text-bad-ink">Below zero: more has been used than the last count had. Count it.</p>
      )}
    </section>
  )
}

/* ------------------------------------------------------------ tier C card -- */

export function ChecklistSection({ row }: { row: StockRow }) {
  const [operator] = useOperator()
  const w = useWrite()
  const answer = async (status: 'OK' | 'LOW') => {
    await w.run(() => stockWrites.checklist(row.ingredient_id, { status, responded_by: operator }), {
      invalidate: AFTER_STOCK_WRITE(row.ingredient_id),
      ok: (d) => `Marked ${d.status === 'LOW' ? 'running low' : 'plenty'} by ${d.responded_by}.`,
    })
  }
  const current = row.checklist?.status ?? null
  return (
    <section aria-labelledby="st-check" className="flex flex-col gap-3">
      <SectionTitle id="st-check" title="Checklist" note="Not worked out from sales. Staff mark it plenty or low." />
      <div className="flex max-w-md gap-2">
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
            disabled={w.pending}
            onClick={() => answer(status)}
            className={cx(
              'h-10 flex-1 rounded-button border px-3 text-base disabled:opacity-50',
              current === status ? 'border-brand-line bg-brand-wash font-bold text-brand-ink' : 'border-line bg-surface hover:bg-canvas',
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

/* ------------------------------------------------ counts and drift (5.2) -- */

export function Counts({
  row,
  onCountNow,
  countRef,
}: {
  row: StockRow
  onCountNow: () => void
  /** The page focuses this button again when the count flow closes. */
  countRef?: RefObject<HTMLButtonElement>
}) {
  const q = useQuery({
    queryKey: KEYS.stockDetail(row.ingredient_id),
    queryFn: () => stockApi.stockDetail(row.ingredient_id),
    staleTime: 30_000,
  })
  const counts = q.data?.counts ?? []
  const d = row.drift
  const t = trustOf(row)
  const attribution = d.attribution
  const share = attribution?.expiry_share ?? null
  const pctAbs = d.drift_pct === null ? null : Math.abs(d.drift_pct)

  let driftLine = ''
  if (row.on_hand.has_count_basis) {
    if (!d.has_observation || d.drift_pct === null || d.theoretical_qty === null) driftLine = 'First count, nothing to compare yet.'
    else
      driftLine = `At the last count the app expected ${fmtQ(d.theoretical_qty, row.unit)}: out by ${driftShown(d.drift_pct).replace(/^[+−]/, '')} (${
        d.drift_pct > 0 ? 'less' : 'more'
      } on the shelf than expected).`
  }
  let autoLine: string
  if (row.tier !== 'A') autoLine = 'Not tier A, so never auto-ordered.'
  else if (t === 'trusted') autoLine = 'Two counts in a row under 10%: can be auto-ordered (still confirmed by a person).'
  else autoLine = 'Needs two counts in a row under 10% before it can be auto-ordered.'

  return (
    <section aria-labelledby="st-counts" className="flex flex-col gap-3">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <h2 id="st-counts" className="text-xl font-extrabold tracking-[-.01em]">
          Counts
        </h2>
        <Button ref={countRef} variant="primary" onClick={onCountNow}>
          Count it now
        </Button>
      </div>
      {driftLine && <p className="text-base">{driftLine}</p>}
      {attribution && pctAbs !== null && pctAbs >= 5 && share !== null && (
        <div className="flex flex-col gap-2 rounded-control bg-canvas px-3.5 py-3">
          <div className="grid grid-cols-[140px_minmax(0,1fr)_44px] items-center gap-x-3 gap-y-1.5 text-sm">
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
          <p className="text-base">
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
        </div>
      )}
      {q.isPending ? (
        <p className="py-2 text-sm text-ink-2">Loading…</p>
      ) : q.isError ? (
        <p className="py-2 text-sm text-ink-2">{q.error instanceof Error ? q.error.message : 'Could not load.'}</p>
      ) : counts.length === 0 ? (
        <p className="py-2 text-sm text-ink-2">Never counted.</p>
      ) : (
        <Table label="Counts, newest first">
          <THead>
            <tr>
              <Th>Date</Th>
              <Th numeric>Counted</Th>
              <Th numeric>Out by</Th>
            </tr>
          </THead>
          <TBody>
            {counts.map((c) => {
              const pd = c.drift_pct === null ? null : Math.abs(c.drift_pct)
              const over = pd !== null && pd > 15
              return (
                <Tr key={c.stock_count_id}>
                  <Td>{fmtD(c.counted_at)}</Td>
                  <Td numeric>{fmtQ(c.counted_qty, row.unit)}</Td>
                  <Td numeric alert={over} strong={over} className={over ? undefined : 'text-ink-2'}>
                    {pd === null ? 'first count' : `${(Math.round(pd * 10) / 10).toFixed(1)}%`}
                  </Td>
                </Tr>
              )
            })}
          </TBody>
        </Table>
      )}
      <p className="text-xs text-ink-2">{autoLine} Over 15% out is marked.</p>
    </section>
  )
}

/* ---------------------------------------------------------------- batches -- */

export function Batches({ row }: { row: StockRow }) {
  const gap = parseDec(row.batch_coverage_gap)
  const open = row.batches.filter((b) => {
    const q = parseDec(b.qty_remaining)
    return q !== null && q.u > 0n
  })
  return (
    <section aria-labelledby="st-batches" className="flex flex-col gap-3">
      <SectionTitle id="st-batches" title="Batches" note="Used oldest-first by use-by date." />
      {open.length === 0 ? (
        <p className="py-2 text-sm text-ink-2">No batches with stock left.</p>
      ) : (
        <Table label="Open batches">
          <THead>
            <tr>
              <Th>Came in</Th>
              <Th numeric>Qty</Th>
              <Th>Use by</Th>
              <Th numeric>Left (est.)</Th>
            </tr>
          </THead>
          <TBody>
            {open.map((b) => {
              const soon = b.days_left !== null && b.days_left <= 3
              return (
                <Tr key={b.batch_id}>
                  <Td>{dayMonth(b.received_at)}</Td>
                  <Td numeric>{b.qty_received ? fmtQ(b.qty_received, row.unit) : '—'}</Td>
                  <Td alert={soon} strong={soon}>
                    {b.effective_expiry
                      ? `${fmtD(b.effective_expiry)} · ${b.days_left}d${b.expiry_assumed ? ' (assumed)' : ''}`
                      : 'keeps'}
                  </Td>
                  <Td numeric est>
                    {fmtQ(b.qty_remaining, row.unit)}
                  </Td>
                </Tr>
              )
            })}
          </TBody>
        </Table>
      )}
      {gap !== null && gap.u !== 0n && (
        <p className="text-sm text-ink-2">
          {fmtQ(row.batch_coverage_gap, row.unit)} on hand that no delivery accounts for; it can’t expire or be written off
          until counted.
        </p>
      )}
      <p className="text-xs text-ink-2">
        <em>Italic</em> figures are worked out, not counted.
      </p>
    </section>
  )
}

/* ------------------------------------------------ record: delivery (walk-in) -- */

export function DeliveryForm({ row }: { row: StockRow }) {
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
  const ok = d !== null && d.u > 0n
  const record = async () => {
    if (!ok) return
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
    <section aria-labelledby="st-delivery" className="flex flex-col gap-2">
      <h2 id="st-delivery" className={SIDE_HEAD}>
        Delivery came in
      </h2>
      <div className="grid grid-cols-2 gap-2">
        <Field label={`Quantity (${unitWord(row.unit)})`}>
          <Input size="sm" numeric placeholder="0" value={qty} onChange={(e) => setQty(e.target.value)} />
        </Field>
        {shelf === null ? (
          <div className="flex min-w-0 flex-col gap-1 text-xs font-bold text-ink-2">
            Use by
            <span className="flex h-9 items-center text-base font-normal text-ink-2">keeps, no date</span>
          </div>
        ) : (
          <Field label="Use by" hint={!edited && date !== '' ? 'Assumed from shelf life until you change it.' : undefined}>
            <Input
              size="sm"
              type="date"
              value={date}
              onChange={(e) => {
                setDate(e.target.value)
                setEdited(true)
              }}
              est={!edited && date !== ''}
            />
          </Field>
        )}
      </div>
      <Button variant="primary" block disabled={!ok} pending={w.pending} pendingLabel="Saving…" onClick={record}>
        Record delivery
      </Button>
      <OutcomeLine outcome={w.outcome} />
    </section>
  )
}

/* ---------------------------------------------------- record: write-off (C11) -- */

const REASONS: ReadonlyArray<{ value: WriteOffReason; label: string }> = [
  { value: 'WENT_OFF', label: 'Went out of date' },
  { value: 'SPILLED', label: 'Spilled / dropped' },
  { value: 'STAFF', label: 'Staff drinks' },
  { value: 'OTHER', label: 'Other' },
]

export function WriteOffForm({ row }: { row: StockRow }) {
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
  const ok = d !== null && d.u > 0n && (reason !== 'OTHER' || note.trim() !== '')
  const submit = async () => {
    if (!ok) return
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
    <section aria-labelledby="st-writeoff" className="flex flex-col gap-2">
      <h2 id="st-writeoff" className={SIDE_HEAD}>
        Write off
      </h2>
      <div className="grid grid-cols-2 gap-2">
        <Field label={`Quantity (${unitWord(row.unit)})`}>
          <Input size="sm" numeric placeholder="0" value={qty} onChange={(e) => setQty(e.target.value)} />
        </Field>
        <Field label="Why">
          <Select size="sm" value={reason} onChange={(e) => setReason(e.target.value as WriteOffReason)}>
            {REASONS.map((r) => (
              <option key={r.value} value={r.value}>
                {r.label}
              </option>
            ))}
          </Select>
        </Field>
      </div>
      {reason === 'OTHER' && (
        <Field label="What happened">
          <Input size="sm" placeholder="A few words" value={note} onChange={(e) => setNote(e.target.value)} />
        </Field>
      )}
      <Button variant="danger-soft" block disabled={!ok} pending={w.pending} pendingLabel="Saving…" onClick={submit}>
        Write off
      </Button>
      <OutcomeLine outcome={w.outcome} />
    </section>
  )
}

/* ------------------------------------------------------- settings: tier (C1) -- */

/** One tap moves the tier (DECISIONS 22: no "why" prompt). Tier A stays earned: the server refuses it otherwise. */
export function TierPicker({ row }: { row: StockRow }) {
  const [operator] = useOperator()
  const w = useWrite()
  useEffect(() => {
    w.setOutcome(null)
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [row.ingredient_id])
  const move = async (tier: Tier) => {
    if (tier === row.tier || w.pending) return
    await w.run(() => stockWrites.tier(row.ingredient_id, { tier, changed_by: operator }), {
      invalidate: AFTER_STOCK_WRITE(row.ingredient_id),
      ok: (d) => d.note,
    })
  }
  const earned = row.drift.clean_streak >= row.drift.required_streak && row.drift.has_observation
  const aLocked = row.tier !== 'A' && (row.tier === 'C' || !earned)
  // Why A is disabled, printed under the control (never only in a tooltip).
  const lockReason = !aLocked
    ? null
    : row.tier === 'C'
      ? 'A is locked: a checklist item has no counts. Move it to B and count it first.'
      : `A is earned: ${row.drift.clean_streak} of ${row.drift.required_streak} counts in a row under 10% so far.`
  return (
    <section aria-labelledby="st-tier" className="flex flex-col gap-2">
      <h2 id="st-tier" className={SIDE_HEAD}>
        Tier
      </h2>
      <div
        role="group"
        aria-labelledby="st-tier"
        aria-describedby="st-tier-note"
        className="inline-flex h-10 w-full items-center gap-0.5 rounded-button bg-wash p-[3px]"
      >
        {(['A', 'B', 'C'] as const).map((t) => {
          const on = row.tier === t
          const locked = t === 'A' && aLocked
          return (
            <button
              key={t}
              type="button"
              aria-pressed={on}
              disabled={w.pending || locked}
              onClick={() => move(t)}
              className={cx(
                'h-[34px] flex-1 rounded-[calc(var(--radius-button)-3px)] text-base transition-[background-color] disabled:cursor-not-allowed',
                on ? 'bg-surface font-bold text-brand-ink shadow-seg' : 'font-medium text-ink hover:text-brand-ink disabled:text-ink-3',
              )}
            >
              {t}
            </button>
          )
        })}
      </div>
      <p id="st-tier-note" className="text-sm text-ink-2">
        {row.tier}: {TIER_NOTE[row.tier]}.{lockReason && ` ${lockReason}`}
      </p>
      <OutcomeLine outcome={w.outcome} />
    </section>
  )
}

/* ------------------------------------------- settings: how long it keeps (C4) -- */

export function KeepsForm({ row }: { row: StockRow }) {
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
    if (!reorderChanged) return
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
  if (doesNotExpire) note = 'Doesn’t expire, so orders for it are not capped by shelf life.'
  else if (est && sl.is_perishable) note = 'Estimated — confirm it to make the order cap real.'
  else note = `Caps orders: never more than can be used in ${sl.usable_days ?? sl.shelf_life_days} days (less delivery time).`

  return (
    <section aria-labelledby="st-keeps" className="flex flex-col gap-2">
      <h2 id="st-keeps" className={SIDE_HEAD}>
        How long it keeps
      </h2>
      <div className="grid grid-cols-2 gap-2">
        <Field label="Unopened (days)" error={shelfEdited && !daysOk ? 'Whole days, 1 or more.' : undefined}>
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
        </Field>
        <Field label="Opened (days)" error={shelfEdited && !openOk ? 'Whole days, 1 or more, or blank.' : undefined}>
          <Input
            size="sm"
            numeric
            value={open}
            placeholder="—"
            disabled={doesNotExpire}
            onChange={(e) => setOpen(e.target.value)}
            est={est && open !== '' && open === (sl.open_life_days?.toString() ?? '')}
          />
        </Field>
      </div>
      <p className="text-sm text-ink-2">{note}</p>
      {shelfEdited && (
        <div className="flex flex-col gap-2 border-t border-line pt-3">
          <span className="text-sm text-ink-2">Where does this come from?</span>
          <div className="grid grid-cols-2 gap-2">
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
                  'h-10 rounded-button border px-3 text-sm',
                  source === v ? 'border-brand-line bg-brand-wash font-bold text-brand-ink' : 'border-line-strong bg-surface',
                )}
              >
                {label}
              </button>
            ))}
          </div>
          <Button
            variant="primary"
            block
            disabled={!daysOk || !openOk || source === null}
            pending={shelfW.pending}
            pendingLabel="Saving…"
            onClick={saveShelf}
          >
            Save shelf life
          </Button>
        </div>
      )}
      <OutcomeLine outcome={shelfW.outcome} />

      {row.tier !== 'C' && row.par && (
        <Field label={`Reorder when down to (${unitWord(row.unit)})`} className="mt-2" hint="Saves when you leave the box.">
          <Input
            size="sm"
            numeric
            value={reorder}
            placeholder="—"
            changed={reorderChanged}
            onChange={(e) => setReorder(e.target.value)}
            onBlur={saveReorder}
            onKeyDown={(e) => {
              if (e.key === 'Enter') void saveReorder()
            }}
          />
        </Field>
      )}
      <OutcomeLine outcome={parW.outcome} />
    </section>
  )
}
