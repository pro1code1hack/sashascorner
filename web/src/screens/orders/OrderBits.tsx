/**
 * Pieces shared by the order list and the order page (owner, 2026-09-26: orders
 * are expenses; each has its own page, with a receipt photo).
 *
 * Receiving writes a DELIVERY movement and a stock batch per line
 * (services/receive_delivery.py), so "Receive" is what moves stock on the shelf.
 * A receipt photo is evidence only: it changes no quantity, price or status.
 */
import { useEffect, useMemo, useRef, useState } from 'react'
import type { ButtonHTMLAttributes, ReactNode } from 'react'
import { useQuery } from '@tanstack/react-query'
import { OperatorNeeded } from '../../components/shell/Operator'
import { Button, Input, cx } from '../../components/ui'
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

/** Small square receipt thumbnail for list rows; a dashed empty slot when none. */
export function ReceiptThumb({ url }: { url: string | null | undefined }) {
  return (
    <span className="relative size-11 flex-none overflow-hidden rounded-control" title={url ? 'Receipt attached' : 'No receipt yet'}>
      <PhotoView url={url ?? null} placeholder="" className="absolute inset-0" />
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
          placeholder={w.pending ? 'Uploading…' : 'Drop a photo of the receipt or delivery note, or click to choose one'}
          className="absolute inset-0"
        />
      </button>
      <input
        ref={input}
        type="file"
        accept="image/webp,image/jpeg,image/png"
        className="sr-only"
        tabIndex={-1}
        onChange={(e) => {
          void upload(e.target.files?.[0])
          e.target.value = ''
        }}
      />
      <div className="flex flex-wrap items-center gap-2 text-sm text-ink-2">
        {url && (
          <a href={mediaSrc(url) ?? undefined} target="_blank" rel="noreferrer">
            Open full size
          </a>
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
          <div key={l.po_line_id} className="grid grid-cols-[minmax(0,1fr)_80px_150px] items-end gap-2 border-b border-line py-1.5 text-base">
            <span className="truncate pb-1.5">
              {l.ingredient_name}{' '}
              <span className="text-sm text-ink-2">
                · ordered {l.final_packs} × {fmtQ(l.pack_size, l.pack_unit)}
                {l.received_qty !== null ? `, ${fmtQ(l.received_qty, l.unit)} in already` : ''}
              </span>
            </span>
            <label className="flex flex-col gap-1 text-xs text-ink-2">
              Packs
              <Input
                size="xs"
                numeric
                value={packs[l.po_line_id] ?? ''}
                onChange={(e) => setPacks({ ...packs, [l.po_line_id]: e.target.value })}
              />
            </label>
            <label className="flex flex-col gap-1 text-xs text-ink-2">
              Use by{!edited && assumed ? ' (assumed)' : ''}
              <Input
                size="xs"
                type="date"
                value={dates[l.po_line_id] ?? assumed}
                className={cx(!edited && 'italic')}
                onChange={(e) => setDates({ ...dates, [l.po_line_id]: e.target.value })}
              />
            </label>
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
          size="sm"
          disabled={operator === null || lines.length === 0}
          pending={w.pending}
          pendingLabel="Receiving…"
          onClick={submit}
        >
          Receive
        </Button>
        <Button variant="ghost" size="sm" onClick={onDone}>
          Close
        </Button>
      </div>
      <OutcomeLine outcome={w.outcome} className="mt-2" />
    </div>
  )
}

/** The design's small outline action chip (§2.3): 12px, radius 12. */
export function ActionChip({
  danger = false,
  armed = false,
  ...rest
}: ButtonHTMLAttributes<HTMLButtonElement> & { danger?: boolean; armed?: boolean }) {
  return (
    <button
      type="button"
      className={cx(
        'h-7 whitespace-nowrap rounded-button border px-3 text-xs disabled:opacity-50',
        danger
          ? cx('border-alert font-bold text-bad-ink hover:bg-alert-wash', armed ? 'bg-alert-wash' : 'bg-surface')
          : 'border-line-strong bg-surface hover:bg-canvas',
      )}
      {...rest}
    />
  )
}

/** Cancel with no preview: the first tap arms it for 4 seconds, the second fires. */
export function ArmChip({ children, onConfirm, disabled }: { children: ReactNode; onConfirm: () => void; disabled?: boolean }) {
  const [armed, setArmed] = useState(false)
  useEffect(() => {
    if (!armed) return
    const t = setTimeout(() => setArmed(false), 4000)
    return () => clearTimeout(t)
  }, [armed])
  return (
    <ActionChip
      disabled={disabled}
      danger
      armed={armed}
      onClick={() => {
        if (armed) {
          setArmed(false)
          onConfirm()
        } else setArmed(true)
      }}
    >
      {armed ? 'Tap again to cancel' : children}
    </ActionChip>
  )
}
