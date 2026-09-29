/**
 * Online orders › Settings (`#/shop/settings`): the one `shop_settings` row
 * (contract §2.1), in sections that each save on their own: on/off with the
 * closed message; the hero copy; dining; ordering hours per weekday and closures;
 * lead time, slots, capacity and days ahead; payment; the notices; loyalty and
 * Telegram. A refusal from the server is shown as written (it validates hours
 * and closures).
 */
import { useState } from 'react'
import type { ReactNode } from 'react'
import { Button, ErrorBox, Field, Input, Loading, PageBody, PageHeader, Segmented, Select, Textarea, Toggle, cx } from '../../components/ui'
import { dayFull } from '../../lib/format'
import { SHOP_KEY, settingsWrites, useShopSettings, useShopSummary } from '../../lib/shop-api'
import type { Closure, DiningOption, HoursRow, ShopSettings, ShopSettingsPatch, ShopSummary, SmsNotify } from '../../lib/types/shop'
import { OutcomeLine, useWrite } from '../stock/writes'
import { todayIso } from '../stock/fmt'
import { Panel, ShopGate, intOrNull, plural } from './shared'

const WEEKDAYS = ['Monday', 'Tuesday', 'Wednesday', 'Thursday', 'Friday', 'Saturday', 'Sunday']

export function ShopSettingsScreen() {
  const q = useShopSettings()
  const summary = useShopSummary()
  return (
    <>
      <PageHeader title="Settings" subtitle="How online ordering runs: when it is on, what it says, when orders can be collected, how people pay." saved={q.data?.updated_at ? `Saved ${new Date(q.data.updated_at).toLocaleString('en-GB', { timeZone: 'Europe/London' })}` : undefined} />
      <PageBody className="compact:px-5">
        <ShopGate>
          {q.isPending && <Loading what="Reading settings" />}
          {q.isError && <ErrorBox error={q.error} what="the shop settings" />}
          {q.data && (
            <div key={q.data.updated_at ?? 'x'} className="grid gap-4 wide:grid-cols-2">
              <OnOff s={q.data} />
              <Hero s={q.data} />
              <Dining s={q.data} />
              <Timing s={q.data} />
              <Hours s={q.data} />
              <Payment s={q.data} summary={summary.data} />
              <Notices s={q.data} />
              <CustomerUpdates s={q.data} summary={summary.data} />
              <Loyalty s={q.data} telegram={summary.data?.telegram_configured ?? q.data.telegram_configured ?? null} />
            </div>
          )}
        </ShopGate>
      </PageBody>
    </>
  )
}

/* -------------------------------------------------------------- section --- */

function useSave() {
  const w = useWrite()
  const save = (patch: ShopSettingsPatch) => void w.run(() => settingsWrites.update(patch), { invalidate: [SHOP_KEY], ok: () => 'Saved.' })
  return { w, save }
}

function SaveRow({ w, onSave, disabled, children }: { w: ReturnType<typeof useWrite>; onSave: () => void; disabled?: boolean; children?: ReactNode }) {
  return (
    <div className="mt-4 flex flex-wrap items-center gap-3">
      <Button variant="primary" size="sm" disabled={disabled} pending={w.pending} pendingLabel="Saving…" onClick={onSave}>
        Save
      </Button>
      {children}
      <OutcomeLine outcome={w.outcome} />
    </div>
  )
}

/* ---------------------------------------------------------------- on/off --- */

function OnOff({ s }: { s: ShopSettings }) {
  const { w, save } = useSave()
  const [enabled, setEnabled] = useState(s.enabled)
  const [msg, setMsg] = useState(s.closed_message)
  const dirty = enabled !== s.enabled || msg !== s.closed_message
  return (
    <Panel title="Ordering">
      <Toggle checked={enabled} onChange={setEnabled} label={enabled ? 'Online ordering is on' : 'Online ordering is off'} />
      <p className="mt-1 text-sm text-ink-2">Off: the shop shows the message below and takes no orders. Orders already placed still show on the board.</p>
      <Field label="Message while it is off" className="mt-3">
        <Textarea rows={2} maxLength={300} className="min-h-0" value={msg} onChange={(e) => setMsg(e.target.value)} />
      </Field>
      <SaveRow w={w} disabled={!dirty} onSave={() => save({ enabled, closed_message: msg.trim() })} />
    </Panel>
  )
}

/* ------------------------------------------------------------------ hero --- */

function Hero({ s }: { s: ShopSettings }) {
  const { w, save } = useSave()
  const [title, setTitle] = useState(s.hero_title)
  const [sub, setSub] = useState(s.hero_subtitle)
  const dirty = title !== s.hero_title || sub !== s.hero_subtitle
  return (
    <Panel title="Front page">
      <Field label="Title">
        <Input value={title} maxLength={120} onChange={(e) => setTitle(e.target.value)} />
      </Field>
      <Field label="Line under it" className="mt-3" hint="Say takeaway and collection plainly: this is not delivery.">
        <Textarea rows={2} maxLength={300} className="min-h-0" value={sub} onChange={(e) => setSub(e.target.value)} />
      </Field>
      <SaveRow w={w} disabled={!dirty || title.trim() === ''} onSave={() => save({ hero_title: title.trim(), hero_subtitle: sub.trim() })} />
    </Panel>
  )
}

/* ---------------------------------------------------------------- dining --- */

function Dining({ s }: { s: ShopSettings }) {
  const { w, save } = useSave()
  const [takeaway, setTakeaway] = useState(s.takeaway_enabled)
  const [eatIn, setEatIn] = useState(s.eat_in_enabled)
  const [def, setDef] = useState<DiningOption>(s.default_dining)
  const dirty = takeaway !== s.takeaway_enabled || eatIn !== s.eat_in_enabled || def !== s.default_dining
  const bad = !takeaway && !eatIn
  return (
    <Panel title="Dining">
      <div className="flex flex-col gap-2">
        <Toggle checked={takeaway} onChange={setTakeaway} label="Takeaway" />
        <Toggle checked={eatIn} onChange={setEatIn} label="Eat in" />
      </div>
      <Field label="Picked first" className="mt-3" error={bad ? 'At least one of takeaway and eat in must be on.' : undefined}>
        <Select value={def} onChange={(e) => setDef(e.target.value as DiningOption)}>
          <option value="TAKEAWAY">Takeaway</option>
          <option value="EAT_IN">Eat in</option>
        </Select>
      </Field>
      <SaveRow w={w} disabled={!dirty || bad} onSave={() => save({ takeaway_enabled: takeaway, eat_in_enabled: eatIn, default_dining: def })} />
    </Panel>
  )
}

/* ---------------------------------------------------------------- timing --- */

function Timing({ s }: { s: ShopSettings }) {
  const { w, save } = useSave()
  const [lead, setLead] = useState(String(s.lead_minutes))
  const [slot, setSlot] = useState(String(s.slot_minutes))
  const [cap, setCap] = useState(String(s.max_orders_per_slot))
  const [days, setDays] = useState(String(s.days_ahead))
  const [last, setLast] = useState(String(s.last_order_minutes_before_close))
  const vals = { lead: intOrNull(lead), slot: intOrNull(slot), cap: intOrNull(cap), days: intOrNull(days), last: intOrNull(last) }
  const bad = Object.values(vals).some((v) => v === undefined || v === null)
  const dirty = [lead, slot, cap, days, last].join() !== [s.lead_minutes, s.slot_minutes, s.max_orders_per_slot, s.days_ahead, s.last_order_minutes_before_close].join()
  const num = (v: string, set: (x: string) => void) => <Input numeric inputMode="numeric" value={v} onChange={(e) => set(e.target.value)} />
  return (
    <Panel title="Collection times">
      <div className="grid grid-cols-2 gap-3 sm:grid-cols-3">
        <Field label="Lead time (min)" hint="Earliest collection is now plus this, rounded up to a slot.">
          {num(lead, setLead)}
        </Field>
        <Field label="Slot length (min)">{num(slot, setSlot)}</Field>
        <Field label="Orders per slot" hint="0 means no limit.">
          {num(cap, setCap)}
        </Field>
        <Field label="Days ahead" hint="0: today only.">
          {num(days, setDays)}
        </Field>
        <Field label="Last order (min before close)">{num(last, setLast)}</Field>
      </div>
      {bad && <p className="mt-2 text-sm text-bad-ink">Whole numbers of minutes, orders and days.</p>}
      <SaveRow
        w={w}
        disabled={!dirty || bad}
        onSave={() =>
          save({
            lead_minutes: vals.lead ?? 0,
            slot_minutes: vals.slot ?? 0,
            max_orders_per_slot: vals.cap ?? 0,
            days_ahead: vals.days ?? 0,
            last_order_minutes_before_close: vals.last ?? 0,
          })
        }
      />
    </Panel>
  )
}

/* ----------------------------------------------------------------- hours --- */

type DayDraft = { open: boolean; from: string; to: string }

function Hours({ s }: { s: ShopSettings }) {
  const { w, save } = useSave()
  const [days, setDays] = useState<DayDraft[]>(() =>
    WEEKDAYS.map((_, wd) => {
      const row = s.hours.find((h) => h.weekday === wd)
      return row ? { open: true, from: row.open, to: row.close } : { open: false, from: '09:00', to: '17:00' }
    }),
  )
  const [closures, setClosures] = useState<Closure[]>(s.closures.map((c) => ({ date: c.date, note: c.note ?? '' })))
  const [newDate, setNewDate] = useState('')
  const [newNote, setNewNote] = useState('')
  const hours: HoursRow[] = days.flatMap((d, wd) => (d.open ? [{ weekday: wd, open: d.from, close: d.to }] : []))
  const cleanClosures: Closure[] = closures.map((c) => ({ date: c.date, note: (c.note ?? '').trim() || null }))
  const bad = days.some((d) => d.open && (!d.from || !d.to || d.from >= d.to))
  const setDay = (i: number, p: Partial<DayDraft>) => setDays((ds) => ds.map((d, j) => (j === i ? { ...d, ...p } : d)))
  // Monday's hours onto Tuesday to Friday; the weekend keeps its own.
  const copyMonday = () => setDays((ds) => ds.map((d, j) => (j >= 1 && j <= 4 && ds[0] ? { ...ds[0] } : d)))
  const mondayCopied = days.slice(1, 5).every((d) => d.open === days[0]?.open && d.from === days[0]?.from && d.to === days[0]?.to)
  const today = todayIso()
  const addClosure = () => {
    if (!newDate || closures.some((c) => c.date === newDate)) return
    setClosures((cs) => [...cs, { date: newDate, note: newNote.trim() }].sort((a, b) => a.date.localeCompare(b.date)))
    setNewDate('')
    setNewNote('')
  }
  const time = 'h-[38px] rounded-control border border-line-control bg-surface px-2 text-base fig'
  return (
    <Panel title="Ordering hours" className="wide:col-span-2">
      <p className="mb-3 text-sm text-ink-2">The shop's own hours, not the café's: a collection slot is offered only inside them. A closed day takes no orders.</p>
      <div className="grid gap-x-6 gap-y-2 sm:grid-cols-2">
        {days.map((d, i) => (
          <div key={i} className="flex flex-wrap items-center gap-x-3 gap-y-1 border-b border-line-row py-1.5">
            <span className="w-24 text-base font-semibold">{WEEKDAYS[i]}</span>
            <Toggle checked={d.open} onChange={(on) => setDay(i, { open: on })} label={<span className="text-sm">{d.open ? 'Open' : 'Closed'}</span>} />
            {d.open && (
              <span className="flex items-center gap-1.5 text-sm text-ink-2">
                <input type="time" aria-label={`${WEEKDAYS[i]} opens`} value={d.from} onChange={(e) => setDay(i, { from: e.target.value })} className={time} />
                to
                <input type="time" aria-label={`${WEEKDAYS[i]} closes`} value={d.to} onChange={(e) => setDay(i, { to: e.target.value })} className={time} />
              </span>
            )}
          </div>
        ))}
      </div>
      <div className="mt-2 flex flex-wrap items-center gap-x-3 gap-y-1">
        <Button variant="link" size="sm" disabled={mondayCopied} onClick={copyMonday}>
          Copy Monday to all weekdays
        </Button>
        <span className="text-sm text-ink-2">{mondayCopied ? 'Tuesday to Friday already match Monday.' : 'Tuesday to Friday take Monday’s hours; Saturday and Sunday stay as they are.'}</span>
      </div>
      {bad && <p className="mt-2 text-sm text-bad-ink">An open day needs an opening time before its closing time.</p>}

      <h3 className="mb-1.5 mt-5 text-base font-bold">Closures</h3>
      <p className="mb-1.5 text-sm text-ink-2">Days the shop takes no orders whatever the hours say: bank holidays, a day off. The note is for the counter; customers see the closed message.</p>
      {closures.length === 0 ? (
        <p className="text-sm text-ink-2">No closures planned.</p>
      ) : (
        <ul className="flex flex-col">
          {closures.map((c) => (
            <li key={c.date} className={cx('flex items-center gap-3 border-b border-line-row py-1.5 text-base', c.date < today && 'text-ink-3')}>
              <span className="fig w-36">{dayFull(c.date)}</span>
              <span className="min-w-0 flex-1 truncate text-ink-2">
                {c.note || 'Closed'}
                {c.date < today ? ' · past' : ''}
              </span>
              <Button variant="ghost" size="sm" aria-label={`Remove closure on ${c.date}`} onClick={() => setClosures((cs) => cs.filter((x) => x.date !== c.date))}>
                <span aria-hidden="true">×</span>
              </Button>
            </li>
          ))}
        </ul>
      )}
      <div className="mt-2 grid grid-cols-1 gap-2 sm:grid-cols-[auto_minmax(0,1fr)_auto] sm:items-start">
        <Field label="Date" error={newDate && closures.some((c) => c.date === newDate) ? 'Already a closure.' : undefined}>
          <Input size="sm" type="date" min={today} value={newDate} onChange={(e) => setNewDate(e.target.value)} />
        </Field>
        <Field label="Note" className="min-w-0" hint="Why, for the counter: “Christmas Day”.">
          <Input
            size="sm"
            value={newNote}
            maxLength={80}
            placeholder="Christmas Day"
            onChange={(e) => setNewNote(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === 'Enter') {
                e.preventDefault()
                addClosure()
              }
            }}
          />
        </Field>
        <Button variant="add" size="sm" className="sm:mt-[22px]" disabled={!newDate || closures.some((c) => c.date === newDate)} onClick={addClosure}>
          + Add closure
        </Button>
      </div>
      <SaveRow w={w} disabled={bad} onSave={() => save({ hours, closures: cleanClosures })}>
        <span className="text-sm text-ink-2">
          {plural(hours.length, 'open day')}, {plural(closures.length, 'closure')}
        </span>
      </SaveRow>
    </Panel>
  )
}

/* --------------------------------------------------------------- payment --- */

function Payment({ s, summary }: { s: ShopSettings; summary: ShopSummary | undefined }) {
  const { w, save } = useSave()
  const [counter, setCounter] = useState(s.pay_at_counter)
  const [online, setOnline] = useState(s.pay_online)
  const [provider, setProvider] = useState(s.payment_provider ?? '')
  const [sink, setSink] = useState(s.pos_sink || 'none')
  const providers = summary?.payment_providers ?? []
  const sinks = summary?.pos_sinks ?? []
  const chosen = providers.find((p) => p.key === provider) ?? null
  // Until the server lists providers, fall back to the older single flag.
  const providerReady = chosen ? chosen.configured : (summary?.stripe_configured ?? s.stripe_configured ?? null)
  const dirty = counter !== s.pay_at_counter || online !== s.pay_online || provider !== (s.payment_provider ?? '') || sink !== (s.pos_sink || 'none')
  const none = !counter && !online
  const canOnline = providerReady !== false
  return (
    <Panel title="Payments">
      <Field label="Online payment provider" hint="Set the provider's keys in .env; the Stripe ones are CAFEOPS_STRIPE_*, Lightspeed uses the existing CAFEOPS_LIGHTSPEED_*.">
        <Select value={provider} onChange={(e) => setProvider(e.target.value)} disabled={providers.length === 0}>
          {providers.length === 0 && <option value={provider}>{provider || 'Stripe'}</option>}
          {providers.map((p) => (
            <option key={p.key} value={p.key}>
              {p.display_name}
              {p.configured ? '' : ' — not configured'}
            </option>
          ))}
        </Select>
      </Field>
      <div className="mt-3 flex flex-col gap-2">
        <Toggle checked={counter} onChange={setCounter} label="Pay at the counter" />
        <Toggle checked={online && canOnline} disabled={!canOnline} onChange={setOnline} label={`Pay online${chosen ? ` (${chosen.display_name})` : ''}`} />
      </div>
      {!canOnline && (
        <p className="mt-2 text-sm text-ink-2">
          {chosen ? chosen.display_name : 'The chosen provider'} isn't configured on the server, so online payment stays off for customers however this is set. Add its keys to .env and restart.
        </p>
      )}
      {none && <p className="mt-2 text-sm text-bad-ink">At least one way to pay must be on.</p>}
      <p className="mt-2 text-sm text-ink-2">Counter orders are marked paid when collected. Refunds are not automatic: cancelling a paid order notes that a refund is needed.</p>

      <h3 className="mb-1.5 mt-5 text-base font-bold">Till</h3>
      <Field label="Push accepted orders to" hint="A pushed order shows its till reference on the order page, and “Sent to the till” in what happened.">
        <Select value={sink} onChange={(e) => setSink(e.target.value)}>
          {!sinks.some((k) => k.key === 'none') && <option value="none">Don't push to the till</option>}
          {sinks.map((k) => (
            <option key={k.key} value={k.key}>
              {k.display_name}
              {k.configured ? '' : ' — not configured'}
            </option>
          ))}
          {sink !== 'none' && !sinks.some((k) => k.key === sink) && <option value={sink}>{sink}</option>}
        </Select>
      </Field>
      {sink !== 'none' && sinks.find((k) => k.key === sink)?.configured === false && (
        <p className="mt-2 text-sm text-ink-2">That till isn't configured on the server; orders are not pushed until it is.</p>
      )}
      <SaveRow
        w={w}
        disabled={!dirty || none}
        onSave={() => save({ pay_at_counter: counter, pay_online: online && canOnline, payment_provider: provider, pos_sink: sink })}
      />
    </Panel>
  )
}

/* --------------------------------------------------------------- notices --- */

function Notices({ s }: { s: ShopSettings }) {
  const { w, save } = useSave()
  const [kcal, setKcal] = useState(s.kcal_notice)
  const [allergen, setAllergen] = useState(s.allergen_notice)
  const [collect, setCollect] = useState(s.collection_note)
  const [terms, setTerms] = useState(s.terms_url)
  const dirty = kcal !== s.kcal_notice || allergen !== s.allergen_notice || collect !== s.collection_note || terms !== s.terms_url
  return (
    <Panel title="Notices">
      <div className="flex flex-col gap-3">
        <Field label="Kcal line" hint="Under the category tabs.">
          <Input value={kcal} maxLength={200} onChange={(e) => setKcal(e.target.value)} />
        </Field>
        <Field label="Allergy notice" hint="Shown before checkout; the customer acknowledges it.">
          <Textarea rows={4} value={allergen} onChange={(e) => setAllergen(e.target.value)} />
        </Field>
        <Field label="Collection note" hint="On the confirmation page.">
          <Textarea rows={2} maxLength={300} className="min-h-0" value={collect} onChange={(e) => setCollect(e.target.value)} />
        </Field>
        <Field label="Terms link">
          <Input value={terms} maxLength={300} onChange={(e) => setTerms(e.target.value)} placeholder="/privacy" />
        </Field>
      </div>
      <SaveRow w={w} disabled={!dirty} onSave={() => save({ kcal_notice: kcal.trim(), allergen_notice: allergen.trim(), collection_note: collect.trim(), terms_url: terms.trim() })} />
    </Panel>
  )
}

/* ------------------------------------------------------ customer updates --- */

function CustomerUpdates({ s, summary }: { s: ShopSettings; summary: ShopSummary | undefined }) {
  const { w, save } = useSave()
  const [email, setEmail] = useState(s.email_notify ?? false)
  const [push, setPush] = useState(s.push_notify ?? false)
  const [sms, setSms] = useState<SmsNotify>(s.sms_notify ?? 'off')
  const dirty = email !== (s.email_notify ?? false) || push !== (s.push_notify ?? false) || sms !== (s.sms_notify ?? 'off')
  const state = (ok: boolean | undefined, what: string) => (ok === undefined ? null : ok ? `${what} is set up.` : `${what} isn't set up on the server yet.`)
  return (
    <Panel title="Customer updates">
      <p className="mb-3 text-sm text-ink-2">How a customer hears that their order is accepted, ready or cancelled. Each one is best effort: a failed send never holds up the order.</p>
      <div className="flex flex-col gap-3">
        <div>
          <Toggle checked={email} onChange={setEmail} label="Email" />
          <p className="mt-1 text-sm text-ink-2">Free; needs SMTP. {state(summary?.smtp_configured, 'SMTP')}</p>
        </div>
        <div>
          <Toggle checked={push} onChange={setPush} label="Push notification (in the browser)" />
          <p className="mt-1 text-sm text-ink-2">
            Free; needs VAPID keys — run <code>cafeops shop vapid-keys</code>. {state(summary?.push_configured, 'Push')}
          </p>
        </div>
        <div>
          <Segmented<SmsNotify>
            label="Text messages"
            showLabel
            value={sms}
            onChange={setSms}
            options={[
              { value: 'off', label: 'Off' },
              { value: 'ready', label: 'When ready' },
              { value: 'all', label: 'Every change' },
            ]}
          />
          <p className="mt-1 text-sm text-ink-2">
            Texts cost about 4p each (Twilio); customers opt in per order. ‘When ready’ is one text per order. {state(summary?.sms_configured, 'Twilio')}
          </p>
        </div>
      </div>
      <SaveRow w={w} disabled={!dirty} onSave={() => save({ email_notify: email, push_notify: push, sms_notify: sms })} />
    </Panel>
  )
}

/* --------------------------------------------------------------- loyalty --- */

function Loyalty({ s, telegram }: { s: ShopSettings; telegram: boolean | null }) {
  const { w, save } = useSave()
  const [stamps, setStamps] = useState(s.loyalty_stamps_online)
  const [notify, setNotify] = useState(s.notify_telegram)
  const dirty = stamps !== s.loyalty_stamps_online || notify !== s.notify_telegram
  return (
    <Panel title="Rewards and notifications">
      <div className="flex flex-col gap-2">
        <Toggle checked={stamps} onChange={setStamps} label="Stamp the Rewards card for collected online orders" />
        <Toggle checked={notify} onChange={setNotify} label="Telegram message for every new order" />
      </div>
      {telegram === false && <p className="mt-2 text-sm text-ink-2">Telegram isn't configured on the server (owner chat id and bot token), so no message goes out however this is set.</p>}
      <SaveRow w={w} disabled={!dirty} onSave={() => save({ loyalty_stamps_online: stamps, notify_telegram: notify })} />
    </Panel>
  )
}
