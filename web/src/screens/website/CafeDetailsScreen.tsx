/**
 * Website › Café details: the old site admin's Settings page (site/web/src/pages/
 * admin/settings.astro), moved into the back office (owner, 2026-09-28).
 *
 * Four sections, each its own <form> with its own Save (the one primary inside
 * it), as the old page had: café details (now with the map pin), opening hours,
 * booking rules, special closures. Each sends only its own part of
 * `PUT /api/website/settings`; the site merges and validates, and a 422 comes back
 * per field and is shown under that field.
 *
 * Dropped: the site admin password. The back office's own password (Settings)
 * guards these screens now, so the page says so in one line.
 *
 * A section's typed-but-unsaved edits survive a refetch (details-lib `useDraft`)
 * and closing the tab asks first.
 */
import { useEffect, useState } from 'react'
import type { FormEvent, ReactNode } from 'react'
import { useQueryClient } from '@tanstack/react-query'
import {
  Button,
  ConfirmTwiceButton,
  ErrorBox,
  Field,
  Input,
  LinkChip,
  Loading,
  PageBody,
  PageHeader,
  SectionHead,
  StatusLine,
  Toggle,
  cx,
} from '../../components/ui'
import type { Outcome } from '../../components/ui'
import { REDUCED_MOTION_QUERY } from '../../lib/media'
import { href } from '../../lib/router'
import { SETTINGS_KEY, livePageUrl, useInvalidateWebsite, useSiteSettings, useWebsiteConnection } from '../../lib/website-api'
import type { BookingRules, Closure, SiteSettings } from '../../lib/types/website'
import { WD_LONG as WEEKDAYS, longDate, todayISO } from './dates'
import { putSettings, useDraft } from './details-lib'
import type { PutResult, SettingsPut } from './details-lib'
import { WebsiteGate } from './shared'

type Errors = Record<string, string>

/* ------------------------------------------------------------ plumbing --- */

/** Closing the tab with unsaved edits asks first. */
function useLeaveGuard(dirty: boolean) {
  useEffect(() => {
    if (!dirty) return
    const onLeave = (e: BeforeUnloadEvent) => {
      e.preventDefault()
      e.returnValue = ''
    }
    window.addEventListener('beforeunload', onLeave)
    return () => window.removeEventListener('beforeunload', onLeave)
  }, [dirty])
}

/**
 * One section's save: PUT, then hand the fresh settings to the query cache.
 * `known` says which field paths the section shows errors for; a 422 on anything
 * else becomes the section's message.
 */
function useSectionSave() {
  const qc = useQueryClient()
  const invalidate = useInvalidateWebsite()
  const [busy, setBusy] = useState(false)
  const [outcome, setOutcome] = useState<Outcome | null>(null)
  const [errors, setErrors] = useState<Errors>({})

  async function save(
    body: SettingsPut,
    okText: string,
    known: (key: string) => boolean,
    remap: (fields: Errors) => Errors = (f) => f,
  ): Promise<SiteSettings | null> {
    setBusy(true)
    setOutcome(null)
    setErrors({})
    const r: PutResult = await putSettings(body)
    setBusy(false)
    if (r.kind === 'ok') {
      qc.setQueryData(SETTINGS_KEY, r.data)
      void invalidate()
      setOutcome({ kind: 'ok', text: okText })
      return r.data
    }
    if (r.kind === 'invalid') {
      const mine: Errors = {}
      const other: string[] = []
      for (const [k, v] of Object.entries(remap(r.fields))) {
        if (known(k)) mine[k] = v
        else other.push(v)
      }
      setErrors(mine)
      setOutcome({ kind: 'error', text: other.length ? other.join(' ') : 'Check the highlighted field.' })
      return null
    }
    setOutcome({ kind: 'error', text: r.message })
    return null
  }

  /** Client-side validation failed before sending. */
  function refuse(errs: Errors, text = 'Check the highlighted field.') {
    setErrors(errs)
    setOutcome({ kind: 'error', text })
  }

  /** Typing again retires a "Saved" line (a refusal stays until the next try). */
  function clear() {
    setOutcome((o) => (o?.kind === 'ok' ? null : o))
  }

  return { busy, outcome, errors, save, refuse, clear }
}

/** A section is a focus target (tabIndex -1) so the page's table of contents can land on it, not only scroll to it. */
function Section({ id, title, hint, children }: { id: string; title: string; hint?: ReactNode; children: ReactNode }) {
  return (
    <section
      id={id}
      tabIndex={-1}
      aria-labelledby={`${id}-h`}
      className="min-w-0 scroll-mt-4 rounded-card-lg border border-line bg-surface px-4 py-4 outline-none focus-visible:ring-2 focus-visible:ring-brand sm:px-5"
    >
      <SectionHead>
        <span id={`${id}-h`}>{title}</span>
      </SectionHead>
      {hint && <p className="mt-0.5 text-sm text-ink-2">{hint}</p>}
      {children}
    </section>
  )
}

/** The Save button, and what happened, at the foot of a section. "Unsaved changes." fills the same line while there is nothing newer to say. */
function SaveRow({ label, busy, dirty, outcome }: { label: string; busy: boolean; dirty: boolean; outcome: Outcome | null }) {
  return (
    <div className="flex flex-wrap items-center gap-x-3 gap-y-2 border-t border-line pt-4">
      <Button type="submit" variant="primary" size="sm" pending={busy} pendingLabel="Saving…" className="max-sm:min-h-11">
        {label}
      </Button>
      <StatusLine outcome={outcome ?? (dirty ? { kind: 'info', text: 'Unsaved changes.' } : null)} />
    </div>
  )
}

/** A map coordinate: `inputMode="text"` on purpose, since the phone's decimal keypad has no minus and Dundee's longitude is negative. */
const COORD_PATTERN = '-?[0-9]{1,3}([.,][0-9]+)?'

/* ------------------------------------------------------------- details --- */

interface DetailsForm {
  name: string
  phone: string
  email: string
  line1: string
  city: string
  postcode: string
  lat: string
  lng: string
  instagram: string
  facebook: string
  tiktok: string
}

function detailsFrom(s: SiteSettings): DetailsForm {
  const c = s.cafe
  return {
    name: c.name,
    phone: c.phone ?? '',
    email: c.email ?? '',
    line1: c.address?.line1 ?? '',
    city: c.address?.city ?? '',
    postcode: c.address?.postcode ?? '',
    lat: c.geo ? String(c.geo.lat) : '',
    lng: c.geo ? String(c.geo.lng) : '',
    instagram: c.socials?.instagram ?? '',
    facebook: c.socials?.facebook ?? '',
    tiktok: c.socials?.tiktok ?? '',
  }
}

/** Form field -> the path a 422 names it by. */
const DETAIL_KEYS: Record<keyof DetailsForm, string> = {
  name: 'cafe.name',
  phone: 'cafe.phone',
  email: 'cafe.email',
  line1: 'cafe.address.line1',
  city: 'cafe.address.city',
  postcode: 'cafe.address.postcode',
  lat: 'cafe.geo.lat',
  lng: 'cafe.geo.lng',
  instagram: 'cafe.socials.instagram',
  facebook: 'cafe.socials.facebook',
  tiktok: 'cafe.socials.tiktok',
}

function coord(t: string, limit: number): number | null {
  const v = t.trim().replace(',', '.')
  if (!/^-?\d{1,3}(\.\d+)?$/.test(v)) return null
  const n = Number(v)
  return Math.abs(n) <= limit ? n : null
}

function DetailsSection({ s }: { s: SiteSettings }) {
  const { draft: f, setDraft, dirty, accept } = useDraft(detailsFrom(s))
  const w = useSectionSave()
  useLeaveGuard(dirty)
  const set = (k: keyof DetailsForm) => (e: { target: { value: string } }) => {
    setDraft({ ...f, [k]: e.target.value })
    w.clear()
  }
  const err = (k: keyof DetailsForm) => w.errors[DETAIL_KEYS[k]]

  const submit = async (e: FormEvent) => {
    e.preventDefault()
    if (w.busy) return
    const errs: Errors = {}
    if (!f.name.trim()) errs['cafe.name'] = 'The café needs a name.'
    const lat = coord(f.lat, 90)
    const lng = coord(f.lng, 180)
    if (lat === null) errs['cafe.geo.lat'] = 'A number between -90 and 90.'
    if (lng === null) errs['cafe.geo.lng'] = 'A number between -180 and 180.'
    for (const k of ['instagram', 'facebook', 'tiktok'] as const) {
      const v = f[k].trim()
      if (v && !v.startsWith('https://')) errs[DETAIL_KEYS[k]] = 'Must be an https:// link, or empty.'
    }
    if (Object.keys(errs).length || lat === null || lng === null) {
      w.refuse(errs)
      return
    }
    const saved = await w.save(
      {
        cafe: {
          name: f.name.trim(),
          phone: f.phone.trim(),
          email: f.email.trim(),
          address: { ...s.cafe.address, line1: f.line1.trim(), city: f.city.trim(), postcode: f.postcode.trim() },
          geo: { lat, lng },
          socials: { instagram: f.instagram.trim(), facebook: f.facebook.trim(), tiktok: f.tiktok.trim() },
        },
      },
      'Café details saved.',
      (k) => Object.values(DETAIL_KEYS).includes(k),
    )
    if (saved) accept(detailsFrom(saved))
  }

  return (
    <Section id="set-details" title="Café details" hint="Shown on the website and in search results.">
      <form noValidate onSubmit={submit} className="mt-4 grid grid-cols-1 gap-4 sm:grid-cols-2">
        <Field label="Name" error={err('name')} className="sm:col-span-2">
          <Input value={f.name} onChange={set('name')} maxLength={80} required />
        </Field>
        <Field label="Phone" error={err('phone')}>
          <Input type="tel" value={f.phone} onChange={set('phone')} maxLength={30} />
        </Field>
        <Field label="Email" hint="Leave empty if there isn’t one yet." error={err('email')}>
          <Input type="email" value={f.email} onChange={set('email')} />
        </Field>
        <Field label="Street address" error={err('line1')} className="sm:col-span-2">
          <Input value={f.line1} onChange={set('line1')} autoComplete="off" />
        </Field>
        <Field label="Town" error={err('city')}>
          <Input value={f.city} onChange={set('city')} />
        </Field>
        <Field label="Postcode" error={err('postcode')}>
          <Input value={f.postcode} onChange={set('postcode')} />
        </Field>
        <Field label="Map pin: latitude" hint="Where the map on the Visit page points. From −90 to 90." error={err('lat')}>
          <Input inputMode="text" pattern={COORD_PATTERN} autoComplete="off" spellCheck={false} className="fig" value={f.lat} onChange={set('lat')} />
        </Field>
        <Field label="Map pin: longitude" hint="In Google Maps, right-click the café to copy both. From −180 to 180." error={err('lng')}>
          <Input inputMode="text" pattern={COORD_PATTERN} autoComplete="off" spellCheck={false} className="fig" value={f.lng} onChange={set('lng')} />
        </Field>
        <Field label="Instagram link" error={err('instagram')} className="sm:col-span-2">
          <Input type="url" value={f.instagram} onChange={set('instagram')} placeholder="https://www.instagram.com/…" />
        </Field>
        <Field label="Facebook link" error={err('facebook')} className="sm:col-span-2">
          <Input type="url" value={f.facebook} onChange={set('facebook')} placeholder="https://www.facebook.com/…" />
        </Field>
        <Field label="TikTok link" hint="Leave empty if none." error={err('tiktok')} className="sm:col-span-2">
          <Input type="url" value={f.tiktok} onChange={set('tiktok')} />
        </Field>
        <div className="sm:col-span-2">
          <SaveRow label="Save details" busy={w.busy} dirty={dirty} outcome={w.outcome} />
        </div>
      </form>
    </Section>
  )
}

/* --------------------------------------------------------------- hours --- */

interface DayForm {
  open: boolean
  from: string
  to: string
}

function hoursFrom(s: SiteSettings): DayForm[] {
  return WEEKDAYS.map((_, i) => {
    const h = s.cafe.hours.find((x) => x.weekday === i)
    return { open: !!h && !h.closed && !!h.open, from: h?.open ?? '', to: h?.close ?? '' }
  })
}

/** "weekday 3: open must be before close" (a whole-model 422) -> day 3's closing time. */
function remapHours(fields: Errors): Errors {
  const out: Errors = {}
  for (const [k, v] of Object.entries(fields)) {
    const m = /weekday (\d)/i.exec(v)
    if (k === 'cafe' && m) {
      out[`cafe.hours.${m[1]}.close`] = v.replace(/^weekday \d: /i, '').replace(/^./, (c) => c.toUpperCase())
    } else out[k] = v
  }
  return out
}

function HoursSection({ s }: { s: SiteSettings }) {
  const { draft: days, setDraft, dirty, accept } = useDraft(hoursFrom(s))
  const w = useSectionSave()
  useLeaveGuard(dirty)
  const setDay = (i: number, patch: Partial<DayForm>) => {
    setDraft(days.map((d, j) => (j === i ? { ...d, ...patch } : d)))
    w.clear()
  }

  const submit = async (e: FormEvent) => {
    e.preventDefault()
    if (w.busy) return
    const errs: Errors = {}
    const hours = days.map((d, i) => {
      if (!d.open) return { weekday: i, closed: true }
      if (!d.from) errs[`cafe.hours.${i}.open`] = 'Opening time?'
      else if (!d.to) errs[`cafe.hours.${i}.close`] = 'Closing time?'
      else if (d.to <= d.from) errs[`cafe.hours.${i}.close`] = 'Closes before it opens.'
      return { weekday: i, open: d.from, close: d.to, closed: false }
    })
    if (Object.keys(errs).length) {
      w.refuse(errs, 'Check the highlighted times.')
      return
    }
    const saved = await w.save({ cafe: { hours } }, 'Opening hours saved.', (k) => k.startsWith('cafe.hours.'), remapHours)
    if (saved) accept(hoursFrom(saved))
  }

  return (
    <Section id="set-hours" title="Opening hours" hint="Bookings follow these. For one-off days off, use Special closures below.">
      <form noValidate onSubmit={submit} className="mt-4 flex flex-col gap-4">
        <ul className="flex flex-col">
          {days.map((d, i) => {
            const day = WEEKDAYS[i] ?? ''
            return (
              <li
                key={day}
                className="grid grid-cols-[minmax(0,1fr)_auto] items-start gap-x-4 gap-y-2 border-b border-line-row py-3 first:pt-0 sm:grid-cols-[7.5rem_7rem_minmax(0,9rem)_minmax(0,9rem)]"
              >
                <div className="flex min-h-11 items-center text-md font-bold sm:mt-5 sm:min-h-10">{day}</div>
                <div className="flex min-h-11 items-center sm:mt-5 sm:min-h-10">
                  <Toggle
                    checked={d.open}
                    onChange={(v) => setDay(i, { open: v })}
                    label={
                      <>
                        <span className="sr-only">{day}: </span>
                        {d.open ? 'Open' : 'Closed'}
                      </>
                    }
                    className="min-h-11 sm:min-h-10"
                  />
                </div>
                {d.open ? (
                  <div className="col-span-2 grid grid-cols-2 gap-3 sm:contents">
                    <Field
                      label={
                        <>
                          Opens<span className="sr-only"> on {day}</span>
                        </>
                      }
                      error={w.errors[`cafe.hours.${i}.open`]}
                    >
                      <Input type="time" step={900} value={d.from} onChange={(e) => setDay(i, { from: e.target.value })} />
                    </Field>
                    <Field
                      label={
                        <>
                          Closes<span className="sr-only"> on {day}</span>
                        </>
                      }
                      error={w.errors[`cafe.hours.${i}.close`]}
                    >
                      <Input type="time" step={900} value={d.to} onChange={(e) => setDay(i, { to: e.target.value })} />
                    </Field>
                  </div>
                ) : (
                  <p className="col-span-2 text-base text-ink-2 sm:mt-5 sm:flex sm:min-h-10 sm:items-center">
                    Closed all day. Nobody can book.
                  </p>
                )}
              </li>
            )
          })}
        </ul>
        <SaveRow label="Save hours" busy={w.busy} dirty={dirty} outcome={w.outcome} />
      </form>
    </Section>
  )
}

/* --------------------------------------------------------------- rules --- */

type RuleKey = keyof BookingRules

/** Copy from the old page; bounds from site settings_store.BookingSettings. */
const RULES: { key: RuleKey; label: string; unit: string; hint: string; min: number; max: number }[] = [
  { key: 'covers_per_slot', label: 'Seats', unit: 'seats', hint: 'How many people the café can seat at once. Bookings stop when this is full.', min: 1, max: 500 },
  { key: 'max_party', label: 'Largest party online', unit: 'people', hint: 'Bigger groups are asked to use the contact form.', min: 1, max: 500 },
  { key: 'slot_minutes', label: 'Time between slots', unit: 'minutes', hint: 'e.g. 30 gives 9am, 9.30am, 10am…', min: 5, max: 240 },
  { key: 'duration_minutes', label: 'Table time', unit: 'minutes', hint: 'How long a booking holds its seats.', min: 15, max: 600 },
  { key: 'last_seating_before_close_minutes', label: 'Last booking before closing', unit: 'minutes', hint: '60 means the last table is an hour before you close.', min: 0, max: 600 },
  { key: 'min_lead_minutes', label: 'Notice needed', unit: 'minutes', hint: 'How soon ahead people can book online. Phone bookings ignore this.', min: 0, max: 10080 },
  { key: 'horizon_days', label: 'How far ahead', unit: 'days', hint: 'How many days ahead the booking page shows.', min: 1, max: 365 },
]

type RulesForm = Record<RuleKey, string>

function rulesFrom(s: SiteSettings): RulesForm {
  const out = {} as RulesForm
  for (const r of RULES) out[r.key] = String(s.booking[r.key])
  return out
}

/** "max_party cannot exceed covers_per_slot" (a whole-model 422) -> the party field. */
function remapRules(fields: Errors): Errors {
  const out: Errors = {}
  for (const [k, v] of Object.entries(fields)) {
    if (k === 'booking' && /max_party/.test(v)) out['booking.max_party'] = 'Can’t be more than the seats.'
    else out[k] = v
  }
  return out
}

function RulesSection({ s }: { s: SiteSettings }) {
  const { draft: f, setDraft, dirty, accept } = useDraft(rulesFrom(s))
  const w = useSectionSave()
  useLeaveGuard(dirty)

  const submit = async (e: FormEvent) => {
    e.preventDefault()
    if (w.busy) return
    const errs: Errors = {}
    const booking = { ...s.booking }
    for (const r of RULES) {
      const raw = f[r.key].trim()
      const n = Number(raw)
      if (!/^\d+$/.test(raw)) errs[`booking.${r.key}`] = 'A whole number, 0 or more.'
      else if (n < r.min || n > r.max) errs[`booking.${r.key}`] = `From ${r.min} to ${r.max} ${r.unit}.`
      else booking[r.key] = n
    }
    if (!errs['booking.covers_per_slot'] && !errs['booking.max_party'] && booking.max_party > booking.covers_per_slot) {
      errs['booking.max_party'] = `Can’t be more than the ${booking.covers_per_slot} seats.`
    }
    if (Object.keys(errs).length) {
      w.refuse(errs)
      return
    }
    const saved = await w.save({ booking }, 'Booking rules saved.', (k) => k.startsWith('booking.'), remapRules)
    if (saved) accept(rulesFrom(saved))
  }

  return (
    <Section id="set-rules" title="Booking rules" hint="How the booking page on the website decides what’s free.">
      <form noValidate onSubmit={submit} className="mt-4 grid grid-cols-1 gap-4 sm:grid-cols-2">
        {RULES.map((r) => (
          <Field key={r.key} label={r.label} hint={r.hint} error={w.errors[`booking.${r.key}`]}>
            <div className="flex items-center gap-2">
              <div className="w-28">
                <Input
                  numeric
                  inputMode="numeric"
                  value={f[r.key]}
                  onChange={(e) => {
                    setDraft({ ...f, [r.key]: e.target.value })
                    w.clear()
                  }}
                />
              </div>
              <span className="text-base text-ink-2">{r.unit}</span>
            </div>
          </Field>
        ))}
        <div className="sm:col-span-2">
          <SaveRow label="Save booking rules" busy={w.busy} dirty={dirty} outcome={w.outcome} />
        </div>
      </form>
    </Section>
  )
}

/* ------------------------------------------------------------ closures --- */

function ClosuresSection({ s }: { s: SiteSettings }) {
  const [date, setDate] = useState('')
  const [note, setNote] = useState('')
  const [removing, setRemoving] = useState<string | null>(null)
  const w = useSectionSave()
  useLeaveGuard(date !== '' || note.trim() !== '')
  const today = todayISO()
  const list = [...s.closures].sort((a, b) => a.date.localeCompare(b.date))
  const known = (k: string) => k === 'closures' || k.startsWith('closures.')
  // Any closures error (a date, the list) is shown by the date picker.
  const dateErr = Object.entries(w.errors).find(([k]) => known(k))?.[1]

  const add = async (e: FormEvent) => {
    e.preventDefault()
    if (w.busy) return
    if (!date) {
      w.refuse({ closures: 'Pick a date.' })
      return
    }
    if (s.closures.some((c) => c.date === date)) {
      w.refuse({ closures: 'That day is already on the list.' })
      return
    }
    const next: Closure[] = [...s.closures, { date, note: note.trim() }]
    const saved = await w.save({ closures: next }, `Closed on ${longDate(date)}.`, known)
    if (saved) {
      setDate('')
      setNote('')
    }
  }

  const remove = async (c: Closure) => {
    setRemoving(c.date)
    await w.save({ closures: s.closures.filter((x) => x.date !== c.date) }, `Closure on ${longDate(c.date)} removed.`, known)
    setRemoving(null)
  }

  return (
    <Section
      id="set-closures"
      title="Special closures"
      hint="Days the café is shut outside the usual hours: holidays, private events. Nobody can book those days, and the booking page shows your note."
    >
      <ul className="mt-3 border-t border-line">
        {list.length === 0 ? (
          <li className="py-3 text-base text-ink-2">No special closures.</li>
        ) : (
          list.map((c) => {
            const past = c.date < today
            return (
              <li key={c.date} className="flex min-h-13 flex-wrap items-center gap-x-4 gap-y-1 border-b border-line py-2">
                <span className={cx('min-w-[11rem] font-bold', past && 'text-ink-2')}>
                  {longDate(c.date)}
                  {past && <span className="ml-2 text-sm font-semibold">(past)</span>}
                </span>
                <span className={cx('min-w-0 flex-1 break-words text-base', !c.note && 'italic text-ink-2')}>
                  {c.note || 'No note'}
                </span>
                <ConfirmTwiceButton
                  variant="ghost"
                  size="sm"
                  armedLabel="Tap again to remove"
                  onConfirm={() => void remove(c)}
                  pending={removing === c.date}
                  pendingLabel="Removing…"
                  disabled={w.busy && removing !== c.date}
                  className="max-sm:min-h-11"
                >
                  <span>
                    Remove<span className="sr-only"> the closure on {longDate(c.date)}</span>
                  </span>
                </ConfirmTwiceButton>
              </li>
            )
          })
        )}
      </ul>
      <form noValidate onSubmit={add} className="mt-4 flex flex-wrap items-start gap-3">
        <Field label="Date" error={dateErr} className="w-full sm:w-44">
          <Input
            type="date"
            value={date}
            min={today}
            onChange={(e) => {
              setDate(e.target.value)
              w.clear()
            }}
          />
        </Field>
        <Field label="Note for guests" hint="e.g. Closed for Christmas" className="w-full min-w-0 sm:w-auto sm:flex-1">
          <Input value={note} onChange={(e) => setNote(e.target.value)} maxLength={120} />
        </Field>
        <div className="flex w-full flex-wrap items-center gap-x-3 gap-y-2">
          <Button
            type="submit"
            variant="primary"
            size="sm"
            pending={w.busy && removing === null}
            pendingLabel="Adding…"
            disabled={w.busy}
            className="max-sm:min-h-11"
          >
            Add closure
          </Button>
          <StatusLine outcome={w.outcome} />
        </div>
      </form>
    </Section>
  )
}

/* ---------------------------------------------------------- the screen --- */

function SignInAndNotices({ s }: { s: SiteSettings }) {
  return (
    <section aria-labelledby="set-about-h" className="min-w-0 rounded-card-lg border border-line bg-surface px-4 py-4 sm:px-5">
      <SectionHead>
        <span id="set-about-h">Sign-in and notices</span>
      </SectionHead>
      <dl className="mt-3 grid grid-cols-1 gap-x-5 gap-y-1 text-base sm:grid-cols-[9rem_minmax(0,1fr)] sm:gap-y-3">
        <dt className="font-bold text-ink-2">Password</dt>
        <dd className="mb-2 sm:mb-0">
          The website has no password of its own any more: the back office password guards it. Change it in{' '}
          <a href={href('/settings')}>Settings</a>.
        </dd>
        <dt className="font-bold text-ink-2">Telegram</dt>
        <dd>
          {s.telegram_configured
            ? 'Set up. New bookings, contact messages and event replies from the website are sent there.'
            : 'Not set up on the website’s server, so new bookings and messages are not sent anywhere. Check them here.'}
        </dd>
      </dl>
    </section>
  )
}

const TOC = [
  { id: 'set-details', label: 'Details' },
  { id: 'set-hours', label: 'Hours' },
  { id: 'set-rules', label: 'Bookings' },
  { id: 'set-closures', label: 'Closures' },
]

/** Scroll to a section and move focus into it, as photos/model.ts `goToSlot` does. */
function goToSection(id: string) {
  const el = document.getElementById(id)
  if (!el) return
  const reduce = window.matchMedia(REDUCED_MOTION_QUERY).matches
  el.scrollIntoView({ behavior: reduce ? 'auto' : 'smooth', block: 'start' })
  el.focus({ preventScroll: true })
}

function Body() {
  const q = useSiteSettings()
  if (q.isPending) return <Loading what="Reading the café’s settings" />
  if (q.isError) return <ErrorBox error={q.error} what="the café’s settings" />
  const s = q.data
  return (
    <div className="mx-auto flex w-full max-w-[820px] flex-col gap-5">
      {/* The app routes on the hash, so these scroll rather than link. */}
      <nav aria-label="Sections on this page" className="flex flex-wrap gap-2">
        {TOC.map((t) => (
          <LinkChip key={t.id} onClick={() => goToSection(t.id)}>
            {t.label}
          </LinkChip>
        ))}
      </nav>
      <DetailsSection s={s} />
      <HoursSection s={s} />
      <RulesSection s={s} />
      <ClosuresSection s={s} />
      <SignInAndNotices s={s} />
    </div>
  )
}

export function CafeDetailsScreen() {
  const conn = useWebsiteConnection()
  const visit = livePageUrl(conn.data, '/visit')
  return (
    <>
      <PageHeader
        title="Café details"
        subtitle="What the website says about the café, and how bookings work."
        actions={
          visit && (
            <a href={visit} target="_blank" rel="noopener noreferrer" className="inline-flex min-h-11 items-center text-base sm:min-h-0">
              See the Visit page<span className="sr-only"> (opens in a new tab)</span>
            </a>
          )
        }
      />
      <PageBody className="bg-canvas">
        <WebsiteGate>
          <Body />
        </WebsiteGate>
      </PageBody>
    </>
  )
}
