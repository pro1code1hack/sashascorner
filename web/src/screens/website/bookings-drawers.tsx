/**
 * The two booking panels: one booking (status, move, party, notes, cancel) and
 * "Add a phone booking". Both are the kit's Drawer, so a sheet on a phone.
 *
 * A move or a bigger party re-checks capacity on the site. When it is full the
 * site refuses ("Over capacity: …"), the refusal is shown as written, and the
 * "Book it anyway" box appears; ticking it sends `override_capacity`.
 */
import { useRef, useState } from 'react'
import type { FormEvent } from 'react'
import { Button, Checkbox, ConfirmTwiceButton, Drawer, Field, FilterChip, FilterChipRow, Input, Segmented, Textarea } from '../../components/ui'
import { siteWrite, useInvalidateWebsite } from '../../lib/website-api'
import type { Booking, BookingPatch, BookingStatus, NewBooking } from '../../lib/types/website'
import {
  ISO_DATE,
  STATUS_LABEL,
  StatusWord,
  TimePicker,
  addDays,
  isCapacityRefusal,
  longDate,
  partyOf,
  patchBooking,
  people,
  relDay,
  todayISO,
  useRoom,
} from './bookings-parts'
import type { Notice } from './bookings-parts'

const EDIT_FORM = 'bk-edit-form'
const ADD_FORM = 'bk-add-form'

function FormError({ text }: { text: string | null }) {
  if (!text) return null
  return (
    <p role="alert" className="text-sm text-bad-ink">
      {text}
    </p>
  )
}

function Fact({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <div className="min-w-0">
      <dt className="text-xs font-bold text-ink-2">{label}</dt>
      <dd className="break-words text-base">{children}</dd>
    </div>
  )
}

/* ---------------------------------------------------------- one booking --- */

export function BookingDrawer({
  booking,
  onClose,
  notify,
}: {
  booking: Booking
  onClose: () => void
  notify: (n: Notice) => void
}) {
  const invalidate = useInvalidateWebsite()
  // The drawer outlives a status change; keep the answer the site gave.
  const [current, setCurrent] = useState(booking)
  const [seen, setSeen] = useState(booking)
  if (seen !== booking) {
    setSeen(booking)
    setCurrent(booking)
  }
  const b = current

  const [party, setParty] = useState(String(b.party))
  const [date, setDate] = useState(b.date)
  const [time, setTime] = useState(b.time)
  const [notes, setNotes] = useState(b.notes ?? '')
  const [override, setOverride] = useState(false)
  const [refusedFull, setRefusedFull] = useState(false)
  const [formErr, setFormErr] = useState<string | null>(null)
  const [fieldErr, setFieldErr] = useState<{ party?: string; date?: string; time?: string }>({})
  const [saving, setSaving] = useState(false)
  const [statusPending, setStatusPending] = useState<BookingStatus | null>(null)
  const inFlight = useRef(false)

  const p = partyOf(party)
  const { room } = useRoom(date, time, p, b)
  const showOverride = refusedFull || (room !== null && !room.fits)

  const setStatus = async (status: BookingStatus, how: 'seg' | 'cancel' | 'reinstate') => {
    if (inFlight.current || status === b.status) return
    inFlight.current = true
    setStatusPending(status)
    setFormErr(null)
    const r = await patchBooking(b.id, { status })
    inFlight.current = false
    setStatusPending(null)
    if (r.kind !== 'ok') {
      setFormErr(
        how === 'reinstate' && r.kind === 'refused' && isCapacityRefusal(r)
          ? `${r.message} Move it to another time first, then reinstate it.`
          : r.message,
      )
      return
    }
    void invalidate()
    if (how === 'cancel') {
      notify({
        tone: 'stale',
        text: `${b.name}’s booking is cancelled. They are not told automatically: call or email them.`,
        undo: { id: b.id, body: { status: 'confirmed' }, label: `Reinstate ${b.name}` },
      })
      onClose()
      return
    }
    setCurrent(r.data)
    notify({ tone: 'stale', text: how === 'reinstate' ? `${b.name}’s booking is reinstated.` : `${b.name}: ${STATUS_LABEL[status].toLowerCase()}.` })
  }

  const save = async (e: FormEvent) => {
    e.preventDefault()
    if (inFlight.current) return
    setFormErr(null)
    const errs: typeof fieldErr = {}
    if (p === null) errs.party = 'Enter how many people (1 or more).'
    if (!ISO_DATE.test(date)) errs.date = 'Pick a date.'
    if (!time) errs.time = 'Pick a time.'
    setFieldErr(errs)
    if (Object.keys(errs).length || p === null) return
    const body: BookingPatch = {}
    if (p !== b.party) body.party = p
    if (date !== b.date) body.date = date
    if (time !== b.time) body.time = time
    if ((notes.trim() || null) !== (b.notes || null)) body.notes = notes.trim()
    if (Object.keys(body).length === 0) {
      setFormErr('Nothing changed.')
      return
    }
    if (override) body.override_capacity = true
    inFlight.current = true
    setSaving(true)
    const r = await patchBooking(b.id, body)
    inFlight.current = false
    setSaving(false)
    if (r.kind !== 'ok') {
      setFormErr(r.message)
      if (r.kind === 'refused' && isCapacityRefusal(r)) setRefusedFull(true)
      return
    }
    void invalidate()
    notify({
      tone: 'stale',
      text: body.date || body.time ? `${b.name} moved to ${relDay(r.data.date)}, ${r.data.time}.` : `${b.name}’s booking is saved.`,
    })
    onClose()
  }

  const busy = saving || statusPending !== null
  const mailSubject = encodeURIComponent(`Your booking at Sasha's Corner (${b.reference})`)

  return (
    <Drawer
      open
      onClose={onClose}
      title={b.name}
      context={`${people(b.party)} · ${relDay(b.date)}, ${b.time}`}
      footer={
        b.status !== 'cancelled' ? (
          <Button type="submit" form={EDIT_FORM} variant="primary" className="min-h-11 flex-1" pending={saving} pendingLabel="Saving…" disabled={busy}>
            Save changes
          </Button>
        ) : undefined
      }
    >
      {(b.phone || b.email) && (
        <div className="flex flex-wrap gap-2">
          {b.phone && (
            <a
              href={`tel:${b.phone.replace(/[^\d+]/g, '')}`}
              className="inline-flex min-h-11 items-center rounded-control border border-line-control bg-surface px-3.5 text-base font-semibold text-ink no-underline hover:bg-canvas"
            >
              Call {b.phone}
            </a>
          )}
          {b.email && (
            <a
              href={`mailto:${b.email}?subject=${mailSubject}`}
              className="inline-flex min-h-11 items-center rounded-control border border-line-control bg-surface px-3.5 text-base font-semibold text-ink no-underline hover:bg-canvas"
            >
              Email
            </a>
          )}
        </div>
      )}

      <dl className="grid grid-cols-2 gap-x-4 gap-y-2.5">
        <Fact label="Reference">
          <span className="fig">{b.reference}</span>
        </Fact>
        <Fact label="Status">
          {b.status === 'confirmed' ? 'Booked' : <StatusWord status={b.status} />}
        </Fact>
        <Fact label="When">
          <span className="fig">
            {longDate(b.date)}, {b.time}
          </span>
        </Fact>
        <Fact label="Booked">{b.source === 'admin' ? 'By phone (added here)' : 'Online'}</Fact>
        {b.phone && (
          <Fact label="Phone">
            <span className="fig">{b.phone}</span>
          </Fact>
        )}
        {b.email && <Fact label="Email">{b.email}</Fact>}
      </dl>

      {b.status === 'cancelled' ? (
        <section className="flex flex-col gap-2 rounded-card border border-line px-3.5 py-3">
          <p className="text-base font-bold">This booking is cancelled.</p>
          <p className="text-sm text-ink-2">Reinstating it holds the table again, if there is still room.</p>
          <FormError text={formErr} />
          <Button
            className="min-h-11 self-start"
            pending={statusPending === 'confirmed'}
            pendingLabel="Reinstating…"
            disabled={busy}
            onClick={() => void setStatus('confirmed', 'reinstate')}
          >
            Reinstate booking
          </Button>
        </section>
      ) : (
        <>
          <div className="flex flex-col gap-1">
            <span className="text-xs font-bold text-ink-2" aria-hidden="true">
              Mark as
            </span>
            <Segmented<BookingStatus>
              label="Mark as"
              value={statusPending ?? b.status}
              onChange={(s) => void setStatus(s, 'seg')}
              options={(['confirmed', 'arrived', 'no_show'] as const).map((s) => ({ value: s, label: STATUS_LABEL[s] }))}
              className="self-start"
            />
          </div>

          <form id={EDIT_FORM} noValidate onSubmit={(e) => void save(e)} className="flex flex-col gap-3.5">
            <h3 className="text-lg font-extrabold tracking-[-.01em]">Change the booking</h3>
            <div className="grid grid-cols-2 gap-3">
              <Field label="Party" error={fieldErr.party}>
                <Input inputMode="numeric" value={party} onChange={(e) => setParty(e.target.value)} className="fig" />
              </Field>
              <Field label="Date" error={fieldErr.date}>
                <Input type="date" value={date} onChange={(e) => setDate(e.target.value)} />
              </Field>
            </div>
            <TimePicker date={date} party={p} value={time} onChange={setTime} except={b} error={fieldErr.time} />
            <Field label="Notes" hint="Only you see these.">
              <Textarea rows={3} maxLength={500} value={notes} onChange={(e) => setNotes(e.target.value)} />
            </Field>
            {showOverride && (
              <Checkbox alert className="min-h-11" checked={override} onChange={setOverride} label="Book it anyway, over capacity" />
            )}
            <FormError text={formErr} />
          </form>

          <section className="flex flex-col gap-2 border-t border-line pt-4">
            <ConfirmTwiceButton
              armedLabel="Tap again to cancel it"
              onConfirm={() => void setStatus('cancelled', 'cancel')}
              pending={statusPending === 'cancelled'}
              pendingLabel="Cancelling…"
              disabled={busy}
              className="min-h-11 self-start"
            >
              Cancel booking
            </ConfirmTwiceButton>
            <p className="text-sm text-ink-2">The guest is not told automatically. Call or email them.</p>
          </section>
        </>
      )}
    </Drawer>
  )
}

/** The drawer for an id we can't find in what is loaded (a stale link). */
export function MissingBookingDrawer({ id, onClose }: { id: string; onClose: () => void }) {
  return (
    <Drawer open onClose={onClose} title="Booking not found">
      <p className="text-base text-ink-2">
        Booking #{id} isn&rsquo;t on the day or week shown. Go to its date, or search for the guest&rsquo;s name or
        reference, and open it from the list.
      </p>
    </Drawer>
  )
}

/* ------------------------------------------------------------- phone-in --- */

export function AddBookingDrawer({
  defaultDate,
  onClose,
  onCreated,
}: {
  defaultDate: string
  onClose: () => void
  onCreated: (b: Booking) => void
}) {
  const invalidate = useInvalidateWebsite()
  const [name, setName] = useState('')
  const [phone, setPhone] = useState('')
  const [email, setEmail] = useState('')
  const [party, setParty] = useState('2')
  const [date, setDate] = useState(defaultDate)
  const [time, setTime] = useState('')
  const [notes, setNotes] = useState('')
  const [override, setOverride] = useState(false)
  const [refusedFull, setRefusedFull] = useState(false)
  const [formErr, setFormErr] = useState<string | null>(null)
  const [fieldErr, setFieldErr] = useState<{ name?: string; party?: string; date?: string; time?: string }>({})
  const [saving, setSaving] = useState(false)
  const inFlight = useRef(false)

  const p = partyOf(party)
  const { room } = useRoom(date, time, p)
  const showOverride = refusedFull || (room !== null && !room.fits)
  const today = todayISO()

  const pickDate = (d: string) => {
    setDate(d)
    setTime('')
    setRefusedFull(false)
  }

  const submit = async (e: FormEvent) => {
    e.preventDefault()
    if (inFlight.current) return
    setFormErr(null)
    const errs: typeof fieldErr = {}
    if (!name.trim()) errs.name = 'Enter a name so you know who is coming.'
    if (p === null) errs.party = 'Enter how many people (1 or more).'
    if (!ISO_DATE.test(date)) errs.date = 'Pick a date.'
    if (!time) errs.time = 'Pick a time.'
    setFieldErr(errs)
    if (Object.keys(errs).length || p === null) return
    const body: NewBooking = { name: name.trim(), party: p, date, time }
    if (phone.trim()) body.phone = phone.trim()
    if (email.trim()) body.email = email.trim()
    if (notes.trim()) body.notes = notes.trim()
    if (override) body.override_capacity = true
    inFlight.current = true
    setSaving(true)
    const r = await siteWrite<Booking>('/bookings', body, 'POST')
    inFlight.current = false
    setSaving(false)
    if (r.kind !== 'ok') {
      setFormErr(r.message)
      if (r.kind === 'refused' && isCapacityRefusal(r)) setRefusedFull(true)
      return
    }
    void invalidate()
    onCreated(r.data)
  }

  return (
    <Drawer
      open
      onClose={onClose}
      title="Add a phone booking"
      context="No lead time or online limits apply. Capacity does, unless you override it."
      footer={
        <Button type="submit" form={ADD_FORM} variant="primary" className="min-h-11 flex-1" pending={saving} pendingLabel="Adding…">
          Add booking
        </Button>
      }
    >
      <form id={ADD_FORM} noValidate onSubmit={(e) => void submit(e)} className="flex flex-col gap-3.5">
        <Field label="Name" error={fieldErr.name}>
          <Input autoComplete="off" maxLength={80} value={name} onChange={(e) => setName(e.target.value)} />
        </Field>
        <div className="grid grid-cols-[minmax(0,1fr)_6rem] gap-3">
          <Field label="Phone" hint="Optional">
            <Input type="tel" inputMode="tel" autoComplete="off" maxLength={30} value={phone} onChange={(e) => setPhone(e.target.value)} />
          </Field>
          <Field label="Party" error={fieldErr.party}>
            <Input inputMode="numeric" value={party} onChange={(e) => setParty(e.target.value)} className="fig" />
          </Field>
        </div>
        <Field label="Email" hint="Optional. They get no email from this; it is for your records.">
          <Input type="email" autoComplete="off" value={email} onChange={(e) => setEmail(e.target.value)} />
        </Field>
        <Field label="Date" error={fieldErr.date}>
          <Input type="date" value={date} onChange={(e) => pickDate(e.target.value)} />
        </Field>
        <FilterChipRow label="Quick date">
          {[0, 1, 2].map((n) => {
            const d = addDays(today, n)
            return (
              <FilterChip key={d} active={date === d} onClick={() => pickDate(d)}>
                {relDay(d)}
              </FilterChip>
            )
          })}
        </FilterChipRow>
        <TimePicker date={date} party={p} value={time} onChange={setTime} error={fieldErr.time} />
        <Field label="Notes" hint="Optional: high chair, birthday, dog…">
          <Textarea rows={2} maxLength={500} value={notes} onChange={(e) => setNotes(e.target.value)} />
        </Field>
        {showOverride && (
          <Checkbox alert className="min-h-11" checked={override} onChange={setOverride} label="Book it anyway, over capacity" />
        )}
        <FormError text={formErr} />
      </form>
    </Drawer>
  )
}
