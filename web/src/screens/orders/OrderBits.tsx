/**
 * Pieces shared by the order list and the order page (owner, 2026-09-26: orders
 * are expenses; each has its own page, with a receipt photo).
 *
 * Receiving writes a DELIVERY movement and a stock batch per line
 * (services/receive_delivery.py), so "Receive" is what moves stock on the shelf.
 * A receipt photo is evidence only: it changes no quantity, price or status.
 */
import { useMemo, useRef, useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import { OperatorNeeded } from '../../components/shell/Operator'
import { Button, Field, Input, LinkButton, cx } from '../../components/ui'
import { mustDec } from '../../lib/dec'
import { mediaSrc } from '../../lib/menu-api'
import { useOperator } from '../../lib/operator'
import { KEYS, orderWrites, stockApi } from '../../lib/stock-api'
import type { PurchaseOrder } from '../../lib/types/stock'
import { PhotoView, shrink } from '../menu/Photo'
import { addDays, fmtQ, todayIso } from '../stock/fmt'
import { OutcomeLine, useWrite } from '../stock/writes'

export const AFTER_ORDER_WRITE = [KEYS.orders, KEYS.draft, KEYS.stock]
const AFTER = AFTER_ORDER_WRITE

export function orderWhat(o: PurchaseOrder): string {
  return o.lines.map((l) => `${l.final_packs}× ${l.ingredient_name}`).join(', ')
}

export const orderPath = (id: number) => `/orders/${id}`

/** Small square receipt thumbnail for list rows; a dashed empty slot when none.
 *  The state is said in text (sr-only), not in a hover-only `title`; the list row
 *  prints "no receipt yet" in words where it matters. */
export function ReceiptThumb({ url }: { url: string | null | undefined }) {
  return (
    <span className="relative size-11 flex-none overflow-hidden rounded-control">
      <PhotoView url={url ?? null} placeholder="" className="absolute inset-0" />
      <span className="sr-only">{url ? 'Receipt attached.' : 'No receipt yet.'}</span>
    </span>
  )
}

/** The order page's receipt slot: click or drop a photo of the delivery note / till receipt. */
export function ReceiptSlot({ o }: { o: PurchaseOrder }) {
  const input = useRef<HTMLInputElement>(null)
  const [operator] = useOperator()
  const [over, setOver] = useState(false)
  const w = useWrite()
  const url = o.receipt_url ?? null
  const keys = [KEYS.orders, KEYS.order(o.po_id)]
  const upload = async (file: File | undefined) => {
    if (!file) return
    const blob = await shrink(file).catch(() => file)
    await w.run(() => orderWrites.uploadReceipt(o.po_id, blob, operator), { invalidate: keys, ok: () => 'Receipt saved.' })
  }
  return (
    <div className="flex flex-col gap-2">
      <button
        type="button"
        onClick={() => input.current?.click()}
        onDragOver={(e) => {
          e.preventDefault()
          setOver(true)
        }}
        onDragLeave={() => setOver(false)}
        onDrop={(e) => {
          e.preventDefault()
          setOver(false)
          void upload(e.dataTransfer.files[0])
        }}
        aria-label={url ? 'Replace the receipt photo' : 'Add a receipt photo'}
        className={cx('relative aspect-[3/4] w-full overflow-hidden rounded-card', over && 'ring-2 ring-brand')}
      >
        <PhotoView
          url={url}
          placeholder={
            w.pending ? 'Uploading…' : 'Drop a photo of the receipt or delivery note, or click to choose one (any photo: JPEG, PNG, HEIC, WebP)'
          }
          className="absolute inset-0"
        />
      </button>
      <input
        ref={input}
        type="file"
        // Any photo the browser can decode: `shrink` re-encodes it to WebP/JPEG
        // before upload, so an iPhone HEIC photo is fine too.
        accept="image/*"
        className="sr-only"
        tabIndex={-1}
        onChange={(e) => {
          void upload(e.target.files?.[0])
          e.target.value = ''
        }}
      />
      <div className="flex flex-wrap items-center gap-2 text-sm text-ink-2">
        {url && (
          <LinkButton variant="link" href={mediaSrc(url) ?? undefined} newTab>
            Open full size
          </LinkButton>
        )}
        {url && o.receipt_uploaded_by && <span>added by {o.receipt_uploaded_by}</span>}
        {url && (
          <Button
            variant="link"
            className="ml-auto"
            disabled={w.pending}
            onClick={() => void w.run(() => orderWrites.clearReceipt(o.po_id), { invalidate: keys, ok: () => 'Receipt removed.' })}
          >
            Remove
          </Button>
        )}
      </div>
      <OutcomeLine outcome={w.outcome} />
    </div>
  )
}

export function ReceiveSheet({ o, onDone }: { o: PurchaseOrder; onDone: () => void }) {
  const [operator] = useOperator()
  const stock = useQuery({ queryKey: KEYS.stock, queryFn: stockApi.stock, staleTime: 60_000 })
  const shelf = useMemo(() => {
    const m = new Map<number, number | null>()
    for (const r of stock.data?.rows ?? []) m.set(r.ingredient_id, r.shelf_life.shelf_life_days)
    return m
  }, [stock.data])
  const remaining = (l: PurchaseOrder['lines'][number]): string => {
    if (l.received_qty === null) return String(l.final_packs)
    if (l.unit !== l.pack_unit) return ''
    const got = mustDec(l.received_qty)
    const size = mustDec(l.pack_size)
    // Whole packs still to come, when the units agree; otherwise ask.
    const left = l.final_packs - Number((got.u * 10n ** BigInt(size.s)) / (size.u * 10n ** BigInt(got.s)))
    return left > 0 ? String(left) : ''
  }
  const [packs, setPacks] = useState<Record<number, string>>(() =>
    Object.fromEntries(o.lines.map((l) => [l.po_line_id, remaining(l)])),
  )
  const [dates, setDates] = useState<Record<number, string>>({})
  const w = useWrite()

  const lines = o.lines
    .filter((l) => /^\d+$/.test((packs[l.po_line_id] ?? '').trim()) && Number(packs[l.po_line_id]) > 0)
    .map((l) => ({
      po_line_id: l.po_line_id,
      received_packs: Number(packs[l.po_line_id]),
      ...(dates[l.po_line_id] ? { expires_on: dates[l.po_line_id] } : {}),
    }))

  const submit = async () => {
    if (operator === null || lines.length === 0) return
    await w.run(() => orderWrites.receive(o.po_id, { received_by: operator, lines }), {
      invalidate: AFTER,
      ok: (r) =>
        `Received ${r.receipts.length} line(s) into stock. ${r.order.status === 'RECEIVED' ? 'The order is complete.' : 'Some lines are still to come.'}`,
      after: (r) => {
        if (r.order.status === 'RECEIVED') onDone()
      },
    })
  }

  return (
    <div className="mb-2 rounded-card border border-line bg-canvas-2 px-3.5 py-3">
      <p className="mb-2 text-sm text-ink-2">
        Count what came in and read the use-by date off the carton. A date left as it is stays marked as assumed.
      </p>
      {o.lines.map((l) => {
        const days = shelf.get(l.ingredient_id) ?? null
        const assumed = days === null ? '' : addDays(todayIso(), days)
        const edited = dates[l.po_line_id] !== undefined
        return (
          // Phone: the name on its own line, then packs and use-by side by side at
          // full input height; from `compact` the three sit in one row.
          <div
            key={l.po_line_id}
            className="grid grid-cols-2 items-end gap-x-2 gap-y-1.5 border-b border-line py-2 text-base compact:grid-cols-[minmax(0,1fr)_96px_170px] compact:py-1.5"
          >
            <span className="col-span-2 min-w-0 compact:col-span-1 compact:truncate compact:pb-2">
              {l.ingredient_name}{' '}
              <span className="text-sm text-ink-2">
                · ordered {l.final_packs} × {fmtQ(l.pack_size, l.pack_unit)}
                {l.received_qty !== null ? `, ${fmtQ(l.received_qty, l.unit)} in already` : ''}
              </span>
            </span>
            <Field
              label={
                <>
                  Packs<span className="sr-only"> of {l.ingredient_name}</span>
                </>
              }
            >
              <Input
                size="sm"
                numeric
                value={packs[l.po_line_id] ?? ''}
                onChange={(e) => setPacks({ ...packs, [l.po_line_id]: e.target.value })}
              />
            </Field>
            <Field
              label={
                <>
                  Use by{!edited && assumed ? ' (assumed)' : ''}
                  <span className="sr-only"> for {l.ingredient_name}</span>
                </>
              }
            >
              <Input
                size="sm"
                type="date"
                value={dates[l.po_line_id] ?? assumed}
                est={!edited && assumed !== ''}
                onChange={(e) => setDates({ ...dates, [l.po_line_id]: e.target.value })}
              />
            </Field>
          </div>
        )
      })}
      {operator === null && (
        <div className="mt-2">
          <OperatorNeeded what="receive a delivery" />
        </div>
      )}
      <div className="mt-2.5 flex gap-2">
        <Button
          variant="primary"
          disabled={operator === null || lines.length === 0}
          pending={w.pending}
          pendingLabel="Receiving…"
          onClick={submit}
        >
          Receive
        </Button>
        <Button variant="ghost" onClick={onDone}>
          Close
        </Button>
      </div>
      <OutcomeLine outcome={w.outcome} className="mt-2" />
    </div>
  )
}
