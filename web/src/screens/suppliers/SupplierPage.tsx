/**
 * One supplier (§3.1): profile, terms, delivery days, what we buy, notes.
 *
 * - Profile fields (name, type, how we order, contact, notes) save on blur or
 *   change with PATCH /api/suppliers/{id}, which refuses any terms field (C20).
 * - Terms (lead time, minimum, cut-off, fee, free-over, delivery days) are held
 *   locally and saved only together by "Confirm these terms", which posts all
 *   six to /confirm (C3, §10.9b). Editing them re-arms the button; nothing
 *   saves terms field by field, and there is no "unconfirm".
 * - "Delete" archives, with a second tap, and is refused while an order is open (C12).
 */
import { useEffect, useMemo, useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import { OperatorNeeded } from '../../components/shell/Operator'
import {
  ConfirmTwiceButton,
  ErrorBox,
  Input,
  Loading,
  MoneyInput,
  Select,
  TitleInput,
  Button,
  cx,
} from '../../components/ui'
import { parseCutoff, parseDays, penceToPounds, poundsToPence } from '../../components/confirm/numbers'
import type { Parsed } from '../../components/confirm/numbers'
import { confirmSupplierTerms } from '../../lib/api'
import { useOperator } from '../../lib/operator'
import { navigate } from '../../lib/router'
import { KEYS, stockApi, supplierWrites } from '../../lib/stock-api'
import type { OrderChannel, Supplier, SupplierPatchIn, SupplierProductsResponse } from '../../lib/types/stock'
import { WEEKDAYS } from '../stock/fmt'
import { useWrite } from '../stock/writes'
import { Products } from './Products'
import type { Saved } from './SuppliersScreen'
import { CHANNELS, KINDS } from './vocab'

const AFTER_PROFILE = [KEYS.suppliers, KEYS.draft, KEYS.orders]

export function SupplierPage({ supplierId, onSaved }: { supplierId: number; onSaved: (s: Saved) => void }) {
  const q = useQuery({
    queryKey: KEYS.supplierProducts(supplierId),
    queryFn: () => stockApi.supplierProducts(supplierId),
    staleTime: 30_000,
  })
  if (q.isPending) return <Loading what="Reading supplier" />
  if (q.isError) return <ErrorBox error={q.error} what="this supplier" />
  return <Page data={q.data} onSaved={onSaved} />
}

function Page({
  data,
  onSaved,
}: {
  data: SupplierProductsResponse
  onSaved: (s: Saved) => void
}) {
  const [operator] = useOperator()
  const s = data.supplier
  const w = useWrite()
  const [name, setName] = useState(s.name)
  const [contact, setContact] = useState(s.contact ?? '')
  const [notes, setNotes] = useState(s.notes ?? '')
  useEffect(() => {
    setName(s.name)
    setContact(s.contact ?? '')
    setNotes(s.notes ?? '')
  }, [s.name, s.contact, s.notes])

  const patch = async (body: Omit<SupplierPatchIn, 'changed_by'>) => {
    if (operator === null) {
      onSaved({ tone: 'bad', text: 'Say who you are first (bottom of the page).' })
      return
    }
    const r = await w.run(() => supplierWrites.patch(s.supplier_id, { changed_by: operator, ...body }), {
      invalidate: [...AFTER_PROFILE, KEYS.supplierProducts(s.supplier_id)],
    })
    if (r) onSaved({ tone: 'ok', text: 'Saved' })
  }
  // `w.outcome` is set asynchronously; mirror refusals into the header when they land.
  useEffect(() => {
    if (w.outcome?.tone === 'bad') onSaved({ tone: 'bad', text: w.outcome.text })
  }, [w.outcome, onSaved])

  const archive = async () => {
    if (operator === null) return
    const r = await w.run(() => supplierWrites.archive(s.supplier_id, operator), {
      invalidate: AFTER_PROFILE,
      ok: (d) =>
        `${d.supplier.name} archived.${d.restarred.length ? ` Recipe prices moved to another supplier for: ${d.restarred.join(', ')}.` : ''}`,
    })
    if (r) {
      onSaved({ tone: 'ok', text: `${r.supplier.name} archived` })
      navigate('/suppliers')
    }
  }

  const kinds = s.kind && !KINDS.includes(s.kind) ? [s.kind, ...KINDS] : KINDS

  return (
    <div className="flex flex-col">
      <div className="flex flex-wrap items-center gap-3">
        <TitleInput
          aria-label="Supplier name"
          value={name}
          onChange={(e) => setName(e.target.value)}
          onBlur={() => name.trim() !== s.name && name.trim() !== '' && patch({ name: name.trim() })}
          className="min-w-0 flex-1 border-line py-0.5 text-[24px]!"
        />
        <ConfirmTwiceButton
          armedLabel="Tap again to archive"
          onConfirm={archive}
          disabled={operator === null}
          pending={w.pending}
          className="rounded-card"
        >
          Delete
        </ConfirmTwiceButton>
      </div>
      {operator === null && (
        <div className="mt-3 max-w-md">
          <OperatorNeeded what="change a supplier" />
        </div>
      )}

      <Terms s={s} onSaved={onSaved} kinds={kinds} contact={contact} setContact={setContact} patch={patch} />

      <Products data={data} onSaved={onSaved} />

      <label className="mt-4.5 flex flex-col gap-1 text-base text-ink-2">
        Notes
        <Input
          value={notes}
          placeholder="Account number, rep, anything"
          onChange={(e) => setNotes(e.target.value)}
          onBlur={() => notes !== (s.notes ?? '') && patch({ notes: notes.trim() === '' ? null : notes })}
        />
      </label>
    </div>
  )
}

/* ------------------------------------------------------------------- terms -- */

function show<T>(p: Parsed<T>): string | null {
  return p.kind === 'bad' ? p.message : null
}

function Terms({
  s,
  onSaved,
  kinds,
  contact,
  setContact,
  patch,
}: {
  s: Supplier
  onSaved: (x: Saved) => void
  kinds: readonly string[]
  contact: string
  setContact: (v: string) => void
  patch: (body: Omit<SupplierPatchIn, 'changed_by'>) => Promise<void>
}) {
  const initial = useMemo(
    () => ({
      lead: String(s.lead_time_days),
      min: s.min_order_pence > 0 ? penceToPounds(s.min_order_pence) : '',
      cutoff: s.cutoff_time ? s.cutoff_time.slice(0, 5) : '',
      fee: s.delivery_fee_pence > 0 ? penceToPounds(s.delivery_fee_pence) : '',
      free: penceToPounds(s.free_delivery_threshold_pence),
      days: [...s.delivery_weekdays].sort(),
    }),
    [s],
  )
  const [t, setT] = useState(initial)
  useEffect(() => setT(initial), [initial])
  const cw = useWrite()
  const [changed, setChanged] = useState<string[] | null>(null)

  const lead = parseDays(t.lead, 0, 60)
  const min = poundsToPence(t.min)
  const fee = poundsToPence(t.fee)
  const free = poundsToPence(t.free)
  const cutoff = parseCutoff(t.cutoff)
  const errors = [show(lead), show(min), show(fee), show(free), show(cutoff)].filter((e): e is string => e !== null)
  if (lead.kind === 'blank') errors.push('Lead time is needed: days from order to delivery.')
  if (t.days.length === 0) errors.push('Pick at least one delivery day. A walk-in shop delivers every day.')
  const dirty = JSON.stringify(t) !== JSON.stringify(initial)
  const armed = s.terms_are_placeholders || dirty

  const confirm = async () => {
    if (errors.length > 0 || lead.kind !== 'value') return
    const r = await cw.run(
      () =>
        confirmSupplierTerms(s.supplier_id, {
          lead_time_days: lead.value,
          delivery_weekdays: t.days,
          min_order_pence: min.kind === 'value' ? min.value : 0,
          delivery_fee_pence: fee.kind === 'value' ? fee.value : 0,
          cutoff_time: cutoff.kind === 'value' ? cutoff.value : null,
          free_delivery_threshold_pence: free.kind === 'value' ? free.value : null,
        }),
      { invalidate: [KEYS.suppliers, KEYS.draft, KEYS.supplierProducts(s.supplier_id)] },
    )
    if (r) {
      setChanged(r.changed)
      onSaved({ tone: 'ok', text: r.was_placeholder ? 'Terms confirmed' : 'Saved' })
    }
  }
  useEffect(() => {
    if (cw.outcome?.tone === 'bad') onSaved({ tone: 'bad', text: cw.outcome.text })
  }, [cw.outcome, onSaved])

  const field = 'flex min-w-0 flex-col gap-1 text-base text-ink-2'
  return (
    <>
      <div className="my-4 grid grid-cols-[repeat(auto-fit,minmax(170px,1fr))] gap-3.5">
        <label className={field}>
          Type
          <Select size="sm" value={s.kind ?? ''} onChange={(e) => patch({ kind: e.target.value || null })}>
            <option value="">—</option>
            {kinds.map((k) => (
              <option key={k} value={k}>
                {k}
              </option>
            ))}
          </Select>
        </label>
        <label className={field}>
          How we order
          <Select size="sm" value={s.order_channel} onChange={(e) => patch({ order_channel: e.target.value as OrderChannel })}>
            {CHANNELS.map((c) => (
              <option key={c.value} value={c.value}>
                {c.label}
              </option>
            ))}
          </Select>
        </label>
        <label className={field}>
          Contact / website
          <Input
            size="sm"
            value={contact}
            placeholder="—"
            onChange={(e) => setContact(e.target.value)}
            onBlur={() => contact !== (s.contact ?? '') && patch({ contact: contact.trim() === '' ? null : contact })}
          />
        </label>
        <label className={field}>
          Lead time (days)
          <Input size="sm" numeric value={t.lead} placeholder="—" onChange={(e) => setT({ ...t, lead: e.target.value })} />
        </label>
        <label className={field}>
          Minimum order £
          <MoneyInput size="sm" value={t.min} placeholder="—" onChange={(e) => setT({ ...t, min: e.target.value })} />
        </label>
        <label className={field}>
          Order cut-off
          <Input size="sm" type="time" value={t.cutoff} onChange={(e) => setT({ ...t, cutoff: e.target.value })} />
        </label>
        <label className={field}>
          Delivery fee £
          <MoneyInput size="sm" value={t.fee} placeholder="—" onChange={(e) => setT({ ...t, fee: e.target.value })} />
        </label>
        <label className={field}>
          Free delivery over £
          <MoneyInput size="sm" value={t.free} placeholder="—" onChange={(e) => setT({ ...t, free: e.target.value })} />
        </label>
      </div>
      {t.cutoff && lead.kind === 'value' && (
        <p className="-mt-2 mb-3 text-sm text-ink-2">
          Order by {t.cutoff}
          {lead.value === 0 ? ' on the day of delivery' : lead.value === 1 ? ', the day before delivery' : `, ${lead.value} days before delivery`}.
        </p>
      )}

      <div className="mb-2 flex flex-wrap items-center gap-2 text-base">
        <span className="text-ink-2">Delivers on</span>
        {WEEKDAYS.map((d) => {
          const on = t.days.includes(d.iso)
          return (
            <button
              key={d.iso}
              type="button"
              aria-pressed={on}
              onClick={() =>
                setT({ ...t, days: on ? t.days.filter((x) => x !== d.iso) : [...t.days, d.iso].sort() })
              }
              className={cx(
                'rounded-button border px-3.5 py-1.5 text-base transition-[background-color,border-color]',
                on ? 'border-brand-line bg-brand-wash font-bold text-brand-ink' : 'border-line bg-surface font-medium text-ink hover:bg-canvas',
              )}
            >
              {d.label}
            </button>
          )
        })}
        {t.days.length === 0 && (
          <span className="text-sm text-ink-2">{s.terms_are_placeholders ? 'not set' : 'any day'}</span>
        )}
      </div>

      <div className="mb-4.5 flex flex-wrap items-center gap-3 text-base">
        <span
          className={cx(
            'inline-flex items-center gap-2',
            s.terms_are_placeholders && !dirty ? 'font-bold text-alert' : 'text-ink',
          )}
        >
          <span
            aria-hidden="true"
            className={cx(
              'grid size-[18px] place-items-center rounded-xs border-[1.5px]',
              s.terms_are_placeholders ? 'border-alert' : 'border-ink',
            )}
          >
            {!s.terms_are_placeholders && <span className="text-xs font-extrabold leading-none">✓</span>}
          </span>
          {s.terms_are_placeholders
            ? 'Terms are a guess: confirm them with the supplier, then save them here'
            : dirty
              ? 'Terms changed: confirm the new ones with the supplier'
              : 'Terms confirmed with the supplier'}
        </span>
        {armed && (
          <Button
            variant="primary"
            size="sm"
            disabled={errors.length > 0}
            pending={cw.pending}
            pendingLabel="Confirming…"
            onClick={confirm}
          >
            Confirm these terms
          </Button>
        )}
        {dirty && (
          <Button variant="ghost" size="sm" onClick={() => setT(initial)}>
            Undo changes
          </Button>
        )}
      </div>
      {armed && errors.length > 0 && dirty && (
        <ul className="-mt-3 mb-4 text-sm text-bad-ink">
          {errors.map((e) => (
            <li key={e}>{e}</li>
          ))}
        </ul>
      )}
      {changed !== null && (
        <p className="-mt-3 mb-4 text-sm text-ink-2">
          {changed.length === 0 ? 'Confirmed as they were: nothing moved.' : `Changed: ${changed.join('; ')}.`}
        </p>
      )}
    </>
  )
}
