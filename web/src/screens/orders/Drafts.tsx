/**
 * Draft orders (§2.2): what the next ordering run will send, per supplier.
 *
 * The Telegram bot is not in use for now (DECISIONS 18): "Create order" writes a
 * supplier's basket as a DRAFT order (recomputed by the server, never taken from
 * this page), and the order page is where its packs are set and a named person
 * confirms it (invariant 1). A basket already stored for the same delivery date
 * shows the stored lines and its status instead of the live suggestion (C14).
 *
 * Invariants on the page: a shelf-life or season cap is stated on the line
 * (4); top-up suggestions are non-perishable only, from the server (5); a
 * withheld forecast shows its reason where the rate would be (9); an unpriced
 * figure is words, never £0 (8).
 */
import { useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import { OperatorNeeded } from '../../components/shell/Operator'
import {
  Button,
  DashedPanel,
  Empty,
  ErrorBox,
  Input,
  Loading,
  MoneyInput,
  StatusTag,
  WarnBox,
  cx,
} from '../../components/ui'
import { poundsToPence } from '../../components/confirm/numbers'
import { mul, mustDec, parseDec } from '../../lib/dec'
import { gbp, plural } from '../../lib/format'
import { useOperator } from '../../lib/operator'
import { href, navigate } from '../../lib/router'
import { KEYS, orderWrites, stockApi } from '../../lib/stock-api'
import type { EmergencyLine, OrderLine, PersistedOrder, SupplierOrder } from '../../lib/types/stock'
import { decStr, fmtD, fmtQ, sumPence } from '../stock/fmt'
import { OutcomeLine, useWrite } from '../stock/writes'
import { orderPath } from './OrderBits'
import { statusWord } from './status'

export function Drafts() {
  const q = useQuery({ queryKey: KEYS.draft, queryFn: stockApi.draft, staleTime: 60_000 })
  if (q.isPending) return <Loading what="Working out the next orders" />
  if (q.isError) return <ErrorBox error={q.error} what="draft orders" />
  const d = q.data
  const baskets = d.suppliers.filter((s) => s.lines.length > 0 || s.persisted)
  const guesses = d.placeholder_supplier_names.length
  const total = d.supplier_count ?? d.suppliers.length
  const uncounted = d.uncounted ?? []
  const unsourced = d.unsourced ?? []

  return (
    <div className="flex flex-col">
      {guesses > 0 && (
        <WarnBox className="mb-3.5">
          {guesses} of {total} suppliers’ terms are guesses. Delivery days, lead times and minimums below are built on
          them; confirm each on its <a href="#/suppliers">supplier page</a>.
        </WarnBox>
      )}
      {baskets.length === 0 ? (
        <Empty>Nothing needs ordering right now.</Empty>
      ) : (
        <div className="overflow-hidden rounded-card-lg bg-surface shadow-raised">
          <div className="hidden grid-cols-[44px_minmax(0,1fr)_150px_110px_90px] gap-3 border-b border-line px-3.5 py-2 text-label font-bold uppercase tracking-[.06em] text-ink-3 compact:grid">
            <span />
            <span>Supplier · what</span>
            <span>Status</span>
            <span className="text-center">Packs</span>
            <span className="text-right">Total</span>
          </div>
          {baskets.map((s) => (
            <Basket key={s.supplier.supplier_id} s={s} />
          ))}
        </div>
      )}
      {d.emergency.length > 0 && <ShopRunBox lines={d.emergency} premium={d.emergency_total_premium_pence} />}
      {uncounted.length > 0 && (
        <p className="mt-4 text-base text-ink-2">
          Count these first; they can’t be ordered until there’s a real number:{' '}
          {uncounted
            .slice(0, 12)
            .map((u) => u.name)
            .join(', ')}
          {uncounted.length > 12 ? ` and ${uncounted.length - 12} more` : ''}.
        </p>
      )}
      {unsourced.length > 0 && (
        <p className="mt-2 text-base text-ink-2">
          No supplier set, so not ordered:{' '}
          {unsourced
            .slice(0, 10)
            .map((u) => u.name)
            .join(', ')}
          {unsourced.length > 10 ? ` and ${unsourced.length - 10} more` : ''}.
        </p>
      )}
      <p className="mt-4 text-sm text-ink-2">
        Nothing is ordered from this page. “Create order” makes a draft; you set the packs and confirm it on the order’s
        page, and only then does it count as ordered.
      </p>
    </div>
  )
}

function termsLine(s: SupplierOrder): string {
  const sup = s.supplier
  const parts: string[] = []
  const orderBy = s.order_by_date ? fmtD(s.order_by_date) : null
  if (orderBy) parts.push(`Order by ${sup.cutoff_time ? `${sup.cutoff_time.slice(0, 5)}, ` : ''}${orderBy}`)
  parts.push(`arrives ${fmtD(s.target_delivery_date)}`)
  if (s.next_delivery_date) parts.push(`next after that ${fmtD(s.next_delivery_date)}`)
  parts.push(`min ${sup.min_order_pence > 0 ? gbp(sup.min_order_pence) : 'none'}`)
  if (sup.delivery_fee_pence > 0) {
    const free =
      sup.free_delivery_threshold_pence !== null ? ` (free over ${gbp(sup.free_delivery_threshold_pence)})` : ''
    parts.push(`fee ${gbp(sup.delivery_fee_pence)}${free}, ${s.fee_applies ? 'included' : 'waived'}`)
  }
  return parts.join(' · ')
}

function Basket({ s }: { s: SupplierOrder }) {
  const p = s.persisted ?? null
  const status = p ? statusWord(p.status) : { label: 'Draft', tone: 'ink-3' as const }
  const total = p ? p.total_pence + p.delivery_fee_pence : s.total_pence
  const short = s.supplier.min_order_pence - s.subtotal_pence
  const lineCount = p ? p.lines.length : s.lines.length
  const head = (
    <>
      <span className="grid size-11 place-items-center rounded-control bg-brand-wash text-lg font-extrabold text-brand-ink" aria-hidden="true">
        {s.supplier.name.slice(0, 1)}
      </span>
      <span className="min-w-0">
        <span className="block truncate text-md font-bold">
          {s.supplier.name}
          {p && <span className="font-normal text-ink-3"> #{p.po_id}</span>}
          <span className="font-normal text-ink-2"> · {lineCount} {plural(lineCount, 'line')}</span>
        </span>
        <span className="block text-sm text-ink-2">{termsLine(s)}</span>
      </span>
      <span className="max-compact:hidden">
        <StatusTag tone={status.tone}>{status.label}</StatusTag>
      </span>
      <span className="max-compact:hidden" />
      <span className="fig text-right text-md font-bold">{gbp(total)}</span>
    </>
  )
  const rowCls = 'grid grid-cols-[44px_minmax(0,1fr)_auto] items-center gap-x-3 bg-canvas-2 px-3.5 py-2.5 compact:grid-cols-[44px_minmax(0,1fr)_150px_110px_90px]'
  return (
    <section className="border-b border-line last:border-b-0" aria-label={`${s.supplier.name} basket`}>
      {p ? (
        <a href={href(orderPath(p.po_id))} className={cx(rowCls, 'text-ink no-underline hover:bg-canvas')}>
          {head}
        </a>
      ) : (
        <div className={rowCls}>{head}</div>
      )}
      {p ? <PendingLines p={p} /> : s.lines.map((l) => <DraftLine key={l.ingredient_id} l={l} />)}
      {!p && !s.meets_minimum && s.supplier.min_order_pence > 0 && short > 0 && (
        <div className="border-t border-line-row py-2 pl-[70px] pr-3.5 text-base text-bad-ink">
          <p>
            {gbp(short)} under their {gbp(s.supplier.min_order_pence)} minimum. Top up with something that keeps (never
            with fresh stock):
          </p>
          {(s.top_up_candidates ?? []).length === 0 ? (
            <p className="mt-1 text-sm text-ink-2">Nothing on this supplier keeps well enough to top up with.</p>
          ) : (
            <ul className="mt-2 flex flex-wrap gap-2" aria-label="Could top up with">
              {(s.top_up_candidates ?? []).map((c) => (
                <li
                  key={c.ingredient_id}
                  className="rounded-button border-[1.5px] border-dashed border-line-strong px-3.5 py-1.5 text-sm text-ink"
                >
                  + {c.name} · {gbp(c.pack_price_pence)}
                </li>
              ))}
            </ul>
          )}
        </div>
      )}
      <div className="flex flex-wrap items-center gap-2 border-t border-line-row py-2 pl-[70px] pr-3.5">
        <p className="min-w-0 flex-1 text-sm text-ink-2">
        {p?.status === 'PENDING_CONFIRM'
          ? 'Waiting to be confirmed. Open it to set the packs and confirm.'
          : p?.status === 'DRAFT'
            ? 'Draft: open it to set the packs and confirm. Nothing is ordered until then.'
            : p?.status === 'CONFIRMED'
              ? `Confirmed by ${p.confirmed_by ?? 'someone'}. Open the order to receive it when it arrives.`
              : p?.status === 'SENT'
                ? `Sent${p.sent_by ? ` by ${p.sent_by}` : ''}. Open the order to receive it when it arrives.`
                : s.supplier.terms_are_placeholders
                  ? 'Terms are a guess.'
                  : 'Terms confirmed.'}
        </p>
        <BasketAction s={s} />
      </div>
    </section>
  )
}

function BasketAction({ s }: { s: SupplierOrder }) {
  const [operator] = useOperator()
  const w = useWrite()
  const p = s.persisted ?? null
  if (p) {
    return (
      <a href={href(orderPath(p.po_id))} className="inline-flex h-8 items-center rounded-button border border-line-strong px-3 text-sm font-bold text-brand-ink no-underline hover:bg-canvas">
        {p.status === 'DRAFT' || p.status === 'PENDING_CONFIRM' ? 'Open to confirm ›' : 'Open order ›'}
      </a>
    )
  }
  return (
    <span className="flex items-center gap-2">
      <OutcomeLine outcome={w.outcome} />
      <Button
        variant="primary"
        size="sm"
        pending={w.pending}
        pendingLabel="Creating…"
        onClick={() =>
          void w.run(() => orderWrites.fromDraft(s.supplier.supplier_id, operator), {
            invalidate: [KEYS.orders, KEYS.draft],
            after: (po) => navigate(orderPath(po.po_id)),
          })
        }
      >
        Create order · {gbp(s.total_pence)}
      </Button>
    </span>
  )
}

function whyLine(l: OrderLine): { text: string; reason: boolean } {
  if (l.is_top_up) return { text: 'Added to reach the minimum. Keeps well, so nothing is wasted.', reason: false }
  if (l.forecast.is_low_confidence) {
    // Invariant 9: the reason replaces the rate, the need and the resulting stock.
    return { text: l.forecast.reasons[0] ?? 'Forecast withheld: not enough history.', reason: true }
  }
  const rate = l.daily_rate_qty ? fmtQ(l.daily_rate_qty, l.unit) : null
  const onOrder = parseDec(l.on_open_pos_qty)
  const oo = onOrder !== null && onOrder.u > 0n ? ` + ${fmtQ(l.on_open_pos_qty, l.unit)} on order` : ''
  const days = l.cover_days ?? 0
  return {
    text: `${rate ? `About ${rate} a day for ${days} ${plural(days, 'day')}` : `For ${days} ${plural(days, 'day')}`}; you have ~${fmtQ(l.on_hand_qty, l.unit)}${oo}.`,
    reason: false,
  }
}

function capLine(l: OrderLine): string | null {
  if (!l.is_capped) return null
  const days = l.cover_days ?? 0
  if (l.cap_kind === 'SHELF_LIFE') return `Cut to ${days} ${plural(days, 'day')} after delivery: that is all it keeps.`
  if (l.cap_kind === 'SEASON_END') return `Cut to ${days} ${plural(days, 'day')}: its season ends.`
  if (l.cap_kind === 'OUT_OF_SEASON') return 'Not ordered: out of season.'
  return l.cap_reason
}

function DraftLine({ l }: { l: OrderLine }) {
  const why = whyLine(l)
  const cap = capLine(l)
  return (
    <div className="grid grid-cols-[minmax(0,1fr)_auto_auto] gap-x-3 border-t border-line-row py-2 pl-[70px] pr-3.5 compact:grid-cols-[minmax(0,1fr)_110px_90px]">
      <div className="min-w-0">
        <div className="text-md">{l.ingredient_name}</div>
        <div className={cx('text-sm', why.reason ? 'text-ink-2 italic' : 'text-ink-2')}>{why.text}</div>
        {cap && <div className="text-sm text-bad-ink">{cap}</div>}
      </div>
      <div className="fig whitespace-nowrap text-center text-base">
        {l.packs} × {fmtQ(l.pack_size, l.pack_unit)}
      </div>
      <div className="fig text-right text-base">{gbp(l.line_total_pence)}</div>
    </div>
  )
}

function PendingLines({ p }: { p: PersistedOrder }) {
  return (
    <>
      {p.lines.map((l) => (
        <div key={l.po_line_id} className="grid grid-cols-[minmax(0,1fr)_auto_auto] gap-x-3 border-t border-line-row py-2 pl-[70px] pr-3.5 compact:grid-cols-[minmax(0,1fr)_110px_90px]">
          <div className="min-w-0">
            <div className="text-md">{l.ingredient_name}</div>
            {l.final_packs !== l.suggested_packs && (
              <div className="text-sm text-ink-2">
                Suggested {l.suggested_packs}, changed to {l.final_packs}.
              </div>
            )}
            {l.checklist_requested_by && (
              <div className="text-sm text-ink-2">Asked for by {l.checklist_requested_by} from the checklist.</div>
            )}
            {l.cap_reason && <div className="text-sm text-bad-ink">{l.cap_reason}</div>}
          </div>
          <div className="fig whitespace-nowrap text-center text-base">
            {l.final_packs} × {fmtQ(l.pack_size, l.pack_unit)}
          </div>
          <div className="fig text-right text-base">{gbp(l.final_packs * l.unit_price_pence)}</div>
        </div>
      ))}
    </>
  )
}

/* -------------------------------------------------------------- shop run -- */

function lineCost(l: EmergencyLine) {
  if (l.retail_unit_price_pence === null) return null
  return mul(mustDec(l.qty), mustDec(l.retail_unit_price_pence))
}

function ShopRunBox({ lines, premium }: { lines: EmergencyLine[]; premium: string | null }) {
  const [operator] = useOperator()
  const [open, setOpen] = useState(false)
  const [where, setWhere] = useState('Tesco')
  const [qtys, setQtys] = useState<Record<number, string>>(() => Object.fromEntries(lines.map((l) => [l.ingredient_id, l.qty])))
  const [paid, setPaid] = useState<Record<number, string>>({})
  const w = useWrite()
  const costs = lines.map(lineCost)
  const total = sumPence(costs.map((c) => (c === null ? null : decStr(c))))

  const bad: string[] = []
  const body = lines.map((l) => {
    const pq = (paid[l.ingredient_id] ?? '').trim()
    const pp = pq === '' ? null : poundsToPence(pq)
    if (pp && pp.kind === 'bad') bad.push(`${l.ingredient_name}: ${pp.message}`)
    return {
      ingredient_id: l.ingredient_id,
      qty: (qtys[l.ingredient_id] ?? l.qty).trim(),
      ...(pp && pp.kind === 'value' ? { paid_pence: pp.value } : {}),
    }
  })
  const submit = async () => {
    if (operator === null || bad.length > 0) return
    await w.run(() => orderWrites.shopRun({ bought_by: operator, where, lines: body }), {
      invalidate: [KEYS.draft, KEYS.stock, KEYS.shopRuns],
      ok: (r) =>
        `Logged ${r.receipts.length} ${plural(r.receipts.length, 'line')} as bought at ${where}${
          r.paid_pence === null ? '' : ` for ${gbp(r.paid_pence)}`
        }. The stock is on the shelf and the run is in Shop runs.`,
      after: () => setOpen(false),
    })
  }

  return (
    <DashedPanel className="mt-4 max-w-[760px]">
      <div className="flex items-baseline gap-2.5">
        <h2 className="flex-1 text-xl font-extrabold">Shop run · can’t wait for a delivery</h2>
        <span className="fig text-lg">{total === null ? 'not all priced' : gbp(decStr(total))}</span>
      </div>
      {lines.map((l, i) => {
        const c = costs[i] ?? null
        return (
          <div key={l.ingredient_id} className="grid grid-cols-[minmax(0,1fr)_90px_80px] gap-2.5 border-t border-line py-1.5 text-base">
            <div className="min-w-0">
              <div>{l.ingredient_name}</div>
              <div className="text-sm text-ink-2">{l.reason}</div>
              {l.retail_is_cheaper && (
                <div className="text-sm text-ink-2">The shop is cheaper than the usual supplier for this one.</div>
              )}
            </div>
            <div className="fig">{fmtQ(l.qty, l.unit)}</div>
            <div className="fig text-right">{c === null ? 'no price' : gbp(decStr(c))}</div>
          </div>
        )
      })}
      <div className="mt-2 flex flex-wrap items-center gap-2.5">
        <p className="min-w-0 flex-1 text-sm text-ink-2">
          {premium === null
            ? 'Retail premium unknown: a line has no retail or supplier price. '
            : `Retail premium over the usual supplier: ${gbp(premium)}. `}
          Every shop run is logged in Shop runs.
        </p>
        {!open && (
          <Button variant="outline" onClick={() => setOpen(true)}>
            Log as bought
          </Button>
        )}
      </div>
      {open && (
        <div className="mt-2.5 flex flex-col gap-2 border-t border-line pt-2.5">
          <label className="flex flex-col gap-1 text-xs font-bold text-ink-2">
            Where
            <Input size="sm" value={where} onChange={(e) => setWhere(e.target.value)} />
          </label>
          {lines.map((l) => (
            <div key={l.ingredient_id} className="grid grid-cols-[minmax(0,1fr)_90px_110px] items-end gap-2 text-base">
              <span className="truncate pb-2">{l.ingredient_name}</span>
              <label className="flex flex-col gap-1 text-xs text-ink-2">
                Bought
                <Input
                  size="xs"
                  numeric
                  value={qtys[l.ingredient_id] ?? ''}
                  onChange={(e) => setQtys({ ...qtys, [l.ingredient_id]: e.target.value })}
                />
              </label>
              <label className="flex flex-col gap-1 text-xs text-ink-2">
                Paid (if known)
                <MoneyInput
                  size="xs"
                  value={paid[l.ingredient_id] ?? ''}
                  onChange={(e) => setPaid({ ...paid, [l.ingredient_id]: e.target.value })}
                />
              </label>
            </div>
          ))}
          {bad.map((b) => (
            <p key={b} className="text-sm text-bad-ink">
              {b}
            </p>
          ))}
          {operator === null && <OperatorNeeded what="log a shop run" />}
          <div className="flex gap-2">
            <Button
              variant="primary"
              size="sm"
              disabled={operator === null || bad.length > 0 || where.trim() === ''}
              pending={w.pending}
              pendingLabel="Logging…"
              onClick={submit}
            >
              Log as bought
            </Button>
            <Button variant="ghost" size="sm" onClick={() => setOpen(false)}>
              Not now
            </Button>
          </div>
        </div>
      )}
      <OutcomeLine outcome={w.outcome} className="mt-2" />
    </DashedPanel>
  )
}
