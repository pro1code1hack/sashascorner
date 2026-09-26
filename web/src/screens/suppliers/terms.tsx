/**
 * Supplier terms: the draft a person types, its validation, and the grouped
 * fields. Shared by "Add a supplier" and the detail page's "Edit terms".
 *
 * All six terms travel together (§10.9b): the cover window is computed from
 * several at once, so there is no field-by-field save anywhere.
 */
import { Field, Input, MoneyInput, cx } from '../../components/ui'
import { parseCutoff, parseDays, penceToPounds, poundsToPence } from '../../components/confirm/numbers'
import { gbp } from '../../lib/format'
import type { Supplier } from '../../lib/types/stock'
import { WEEKDAYS } from '../stock/fmt'

export interface TermsDraft {
  lead: string
  days: number[]
  cutoff: string
  min: string
  fee: string
  free: string
}

export const EMPTY_TERMS: TermsDraft = {
  lead: '',
  days: [],
  cutoff: '',
  min: '',
  fee: '',
  free: '',
}

export function draftOf(s: Supplier): TermsDraft {
  return {
    lead: String(s.lead_time_days),
    days: [...s.delivery_weekdays].sort(),
    cutoff: s.cutoff_time ? s.cutoff_time.slice(0, 5) : '',
    min: s.min_order_pence > 0 ? penceToPounds(s.min_order_pence) : '',
    fee: s.delivery_fee_pence > 0 ? penceToPounds(s.delivery_fee_pence) : '',
    free: penceToPounds(s.free_delivery_threshold_pence),
  }
}

export function isBlank(t: TermsDraft): boolean {
  return (
    t.lead.trim() === '' &&
    t.days.length === 0 &&
    t.cutoff === '' &&
    t.min.trim() === '' &&
    t.fee.trim() === '' &&
    t.free.trim() === ''
  )
}

export interface ParsedTerms {
  lead_time_days: number
  delivery_weekdays: number[]
  min_order_pence: number
  delivery_fee_pence: number
  cutoff_time: string | null
  free_delivery_threshold_pence: number | null
}

export type TermsErrors = Partial<Record<keyof TermsDraft, string>>

/**
 * Parse a draft. `confirming` demands a delivery day: a supplier with none can
 * never satisfy a cover window, and the server refuses it.
 */
export function parseTerms(t: TermsDraft, confirming: boolean): { errors: TermsErrors; value: ParsedTerms | null } {
  const errors: TermsErrors = {}
  const lead = parseDays(t.lead, 0, 60)
  if (lead.kind === 'bad') errors.lead = lead.message
  if (lead.kind === 'blank') errors.lead = 'Days from ordering to delivery. 0 for same day.'
  if (confirming && t.days.length === 0) errors.days = 'Pick at least one. A shop you walk to delivers every day.'
  const cutoff = parseCutoff(t.cutoff)
  if (cutoff.kind === 'bad') errors.cutoff = cutoff.message
  const min = poundsToPence(t.min)
  if (min.kind === 'bad') errors.min = min.message
  const fee = poundsToPence(t.fee)
  if (fee.kind === 'bad') errors.fee = fee.message
  const free = poundsToPence(t.free)
  if (free.kind === 'bad') errors.free = free.message
  if (Object.keys(errors).length > 0 || lead.kind !== 'value') return { errors, value: null }
  return {
    errors,
    value: {
      lead_time_days: lead.value,
      delivery_weekdays: [...t.days].sort(),
      min_order_pence: min.kind === 'value' ? min.value : 0,
      delivery_fee_pence: fee.kind === 'value' ? fee.value : 0,
      cutoff_time: cutoff.kind === 'value' ? cutoff.value : null,
      free_delivery_threshold_pence: free.kind === 'value' ? free.value : null,
    },
  }
}

export function daysText(days: readonly number[]): string {
  if (days.length === 0) return 'not set'
  if (days.length === 7) return 'every day'
  return WEEKDAYS.filter((d) => days.includes(d.iso))
    .map((d) => d.label)
    .join(', ')
}

export function cutoffText(cutoff: string | null, lead: number): string {
  if (!cutoff) return 'no cut-off'
  const hhmm = cutoff.slice(0, 5)
  if (lead === 0) return `${hhmm}, same day`
  if (lead === 1) return `${hhmm}, the day before`
  return `${hhmm}, ${lead} days before`
}

/** Read view of the six terms, for the detail page. */
export function termsRows(s: Supplier): Array<{ key: string; label: string; value: string }> {
  return [
    {
      key: 'lead',
      label: 'Lead time',
      value: s.lead_time_days === 1 ? '1 day' : `${s.lead_time_days} days`,
    },
    { key: 'days', label: 'Delivers on', value: daysText(s.delivery_weekdays) },
    {
      key: 'cutoff',
      label: 'Order by',
      value: cutoffText(s.cutoff_time, s.lead_time_days),
    },
    {
      key: 'min',
      label: 'Minimum order',
      value: s.min_order_pence > 0 ? gbp(s.min_order_pence) : 'none',
    },
    {
      key: 'fee',
      label: 'Delivery fee',
      value: s.delivery_fee_pence > 0 ? gbp(s.delivery_fee_pence) : 'free',
    },
    {
      key: 'free',
      label: 'Free delivery over',
      value: s.free_delivery_threshold_pence === null ? '—' : gbp(s.free_delivery_threshold_pence),
    },
  ]
}

export function DayPicker({
  days,
  onChange,
  error,
}: {
  days: number[]
  onChange: (d: number[]) => void
  error?: string
}) {
  return (
    <div role="group" aria-label="Delivery days" className="flex flex-col gap-1">
      <span className="text-xs font-bold text-ink-2">Delivery days</span>
      <div className="flex flex-wrap gap-1.5">
        {WEEKDAYS.map((d) => {
          const on = days.includes(d.iso)
          return (
            <button
              key={d.iso}
              type="button"
              aria-pressed={on}
              onClick={() => onChange(on ? days.filter((x) => x !== d.iso) : [...days, d.iso].sort())}
              className={cx(
                'h-9 min-w-[46px] rounded-button border px-2.5 text-base transition-[background-color,border-color]',
                on
                  ? 'border-brand-line bg-brand-wash font-bold text-brand-ink'
                  : 'border-line bg-surface font-medium text-ink hover:bg-canvas',
              )}
            >
              {d.label}
            </button>
          )
        })}
        <button
          type="button"
          onClick={() => onChange(days.length === 7 ? [] : [1, 2, 3, 4, 5, 6, 7])}
          className="h-9 px-1.5 text-sm text-brand-ink underline"
        >
          {days.length === 7 ? 'Clear' : 'Every day'}
        </button>
      </div>
      {error && <span className="text-sm text-bad-ink">{error}</span>}
    </div>
  )
}

/** The six terms, in two groups: when it arrives, and what it costs. */
export function TermsFields({
  t,
  setT,
  errors,
}: {
  t: TermsDraft
  setT: (t: TermsDraft) => void
  errors: TermsErrors
}) {
  const lead = parseDays(t.lead, 0, 60)
  return (
    <div className="flex flex-col gap-4">
      <fieldset className="flex flex-col gap-3">
        <legend className="mb-2 text-label font-bold uppercase tracking-[.05em] text-ink-2">Delivery</legend>
        <div className="grid grid-cols-1 gap-3 sm:grid-cols-2">
          <Field label="Lead time (days)" hint="From ordering to the delivery arriving" error={errors.lead}>
            <Input
              numeric
              inputMode="numeric"
              value={t.lead}
              placeholder="e.g. 2"
              onChange={(e) => setT({ ...t, lead: e.target.value })}
            />
          </Field>
          <Field
            label="Order cut-off"
            hint={
              t.cutoff && lead.kind === 'value'
                ? `Order by ${cutoffText(t.cutoff, lead.value)}`
                : 'Leave empty if there is none'
            }
            error={errors.cutoff}
          >
            <Input type="time" value={t.cutoff} onChange={(e) => setT({ ...t, cutoff: e.target.value })} />
          </Field>
        </div>
        <DayPicker days={t.days} onChange={(days) => setT({ ...t, days })} error={errors.days} />
      </fieldset>
      <fieldset className="flex flex-col gap-3">
        <legend className="mb-2 text-label font-bold uppercase tracking-[.05em] text-ink-2">Money</legend>
        <div className="grid grid-cols-1 gap-3 sm:grid-cols-3">
          <Field label="Minimum order £" hint="Empty = no minimum" error={errors.min}>
            <MoneyInput value={t.min} placeholder="0.00" onChange={(e) => setT({ ...t, min: e.target.value })} />
          </Field>
          <Field label="Delivery fee £" hint="Empty = free" error={errors.fee}>
            <MoneyInput value={t.fee} placeholder="0.00" onChange={(e) => setT({ ...t, fee: e.target.value })} />
          </Field>
          <Field label="Free delivery over £" hint="Empty = never free" error={errors.free}>
            <MoneyInput value={t.free} placeholder="—" onChange={(e) => setT({ ...t, free: e.target.value })} />
          </Field>
        </div>
      </fieldset>
    </div>
  )
}
