/**
 * New message: a lock-screen preview, the text (200 characters, Google cuts the rest),
 * who gets it, promotion or notice, and when. Sending is one POST; scheduling is the
 * same POST with a time. The server's answer is shown as it comes back.
 *
 * Promotions reach only people who ticked the box, at most `promo_limit_per_month` a
 * month each (the server skips anyone already at the limit). A notice (a holiday
 * closure) goes to everyone with a card.
 */
import { useId, useState } from 'react'
import { Button, Checkbox, ConfirmTwiceButton, Field, Input, Segmented, StatusLine, Textarea, WarnBox, cx } from '../../../components/ui'
import type { Outcome } from '../../../components/ui'
import { loyaltyApi, useInvalidateLoyalty } from '../../../lib/loyalty-api'
import type { LoyaltyCampaignSegment, LoyaltyCampaignsResponse } from '../../../lib/types/loyalty'
import { people, segmentPhrase, weekdayDateTime } from './words'

const MAX = 200

const CHOICES: { seg: LoyaltyCampaignSegment; label: (promo: boolean) => string }[] = [
  { seg: 'ALL_OPTED_IN', label: (p) => (p ? 'Everyone opted in' : 'Everyone with a card') },
  { seg: 'LAPSED_30', label: () => 'Lapsed 30+ days' },
  { seg: 'REWARD_READY', label: () => 'Reward ready, not used' },
  { seg: 'BIRTHDAY', label: () => 'Birthday this month' },
]

/** Who a choice reaches now; null when the server gave no figure (a notice to a narrow group). */
function reach(data: LoyaltyCampaignsResponse, seg: LoyaltyCampaignSegment, promo: boolean): number | null {
  if (promo) return data.audiences[seg] ?? null
  return seg === 'ALL_OPTED_IN' ? data.everyone_with_card : null
}

/** "YYYY-MM-DDTHH:mm" for a datetime-local `min`, in the browser's time. */
function localInputValue(d: Date): string {
  const pad = (n: number) => String(n).padStart(2, '0')
  return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}T${pad(d.getHours())}:${pad(d.getMinutes())}`
}

export function Composer({ data }: { data: LoyaltyCampaignsResponse }) {
  const invalidate = useInvalidateLoyalty()
  const radioName = useId()
  const [title, setTitle] = useState('')
  const [message, setMessage] = useState('')
  const [segment, setSegment] = useState<LoyaltyCampaignSegment>('ALL_OPTED_IN')
  const [promo, setPromo] = useState(true)
  const [when, setWhen] = useState<'now' | 'later'>('now')
  const [at, setAt] = useState('')
  const [pending, setPending] = useState(false)
  const [result, setResult] = useState<Outcome | null>(null)

  const text = message.trim()
  const n = reach(data, segment, promo)
  const atDate = at ? new Date(at) : null
  const atValid = atDate !== null && !Number.isNaN(atDate.getTime()) && atDate.getTime() > Date.now() + 30_000
  const limitReached = promo && data.promos_this_month >= data.promo_limit_per_month
  const ready = text.length > 0 && (when === 'now' || atValid) && !pending

  const summary =
    (n === null
      ? `Goes to everyone with a card who is ${segmentPhrase(segment, promo)}.`
      : `Goes to ${people(n)}.`) +
    (promo
      ? ` Anyone already at ${data.promo_limit_per_month} promotions this month is skipped. Every message has an unsubscribe link.`
      : ' A notice does not count towards the promotions limit.')

  const label =
    when === 'later'
      ? atValid
        ? `Schedule for ${weekdayDateTime(atDate!)}`
        : 'Pick a time to schedule'
      : n === null
        ? 'Send now'
        : `Send to ${people(n)}`

  async function submit() {
    setPending(true)
    setResult(null)
    const r = await loyaltyApi.createCampaign({
      title: title.trim() || null,
      message: text,
      segment,
      is_promo: promo,
      ...(when === 'now' ? { send_now: true } : { scheduled_at: atDate!.toISOString() }),
    })
    setPending(false)
    if (r.kind !== 'ok') {
      setResult({ kind: 'error', text: r.message })
      return
    }
    const c = r.data
    setResult({
      kind: 'ok',
      text:
        c.status === 'sent'
          ? `Sent to ${people(c.recipients ?? 0)}.`
          : c.status === 'scheduled' && c.scheduled_at
            ? `Scheduled for ${weekdayDateTime(c.scheduled_at)}. You can cancel it until then.`
            : 'Saved.',
    })
    setTitle('')
    setMessage('')
    setAt('')
    setWhen('now')
    await invalidate()
  }

  return (
    <section aria-labelledby="new-message" className="flex flex-col gap-4">
      <h2 id="new-message" className="text-xl font-extrabold tracking-[-.01em]">
        New message
      </h2>

      {/* The lock screen: dark on purpose, it is a picture of the phone, not the app. */}
      <div className="rounded-card bg-ink px-3.5 py-3 text-white" aria-label="Lock screen preview">
        <div className="flex items-start gap-3">
          <span className="grid size-9 flex-none place-items-center rounded-sm bg-surface text-base font-extrabold text-ink" aria-hidden="true">
            S
          </span>
          <div className="min-w-0 flex-1">
            <div className="flex items-baseline justify-between gap-2 text-xs text-white/70">
              <span>Sasha's Corner</span>
              <span>now</span>
            </div>
            <p className={cx('mt-0.5 break-words text-base', !text && 'text-white/80')}>
              {text || 'Your message shows here, on the lock screen.'}
            </p>
          </div>
        </div>
      </div>

      <Field label="Name (only you see it)" hint="Leave blank and the first words are used.">
        <Input size="sm" value={title} maxLength={120} onChange={(e) => setTitle(e.target.value)} placeholder="Autumn menu" />
      </Field>

      <Field
        label={
          <>
            Message <span className="fig font-normal text-ink-3">· {message.length}/{MAX}</span>
          </>
        }
      >
        <Textarea
          value={message}
          maxLength={MAX}
          rows={3}
          onChange={(e) => {
            setMessage(e.target.value)
            if (result) setResult(null)
          }}
          placeholder="New autumn menu from Thursday. Come and try the pumpkin latte."
        />
      </Field>

      <fieldset className="flex flex-col gap-2">
        <legend className="mb-1 text-xs font-bold text-ink-2">Who gets it</legend>
        <div role="radiogroup" aria-label="Who gets it" className="flex flex-col gap-2">
          {CHOICES.map((c) => {
            const on = c.seg === segment
            const count = reach(data, c.seg, promo)
            return (
              <label
                key={c.seg}
                className={cx(
                  'flex cursor-pointer items-center gap-2.5 rounded-control border px-3 py-2.5 text-base',
                  on ? 'border-brand bg-brand-wash font-bold text-ink' : 'border-line-control bg-surface hover:bg-canvas',
                )}
              >
                <input
                  type="radio"
                  name={radioName}
                  checked={on}
                  onChange={() => setSegment(c.seg)}
                  className="size-4 flex-none accent-brand"
                />
                <span className="min-w-0 flex-1">{c.label(promo)}</span>
                {count !== null && <span className="fig flex-none text-xs font-normal text-ink-2">{people(count)}</span>}
              </label>
            )
          })}
        </div>
      </fieldset>

      <Checkbox
        checked={promo}
        onChange={setPromo}
        label={
          <span className="text-sm text-ink-2">
            <span className="font-bold text-ink">This is a promotion</span> · untick only for a notice like a holiday
            closure. Notices go to everyone with a card.
          </span>
        }
        className="items-start"
      />

      <div className="flex flex-col gap-2">
        <span className="text-xs font-bold text-ink-2">When</span>
        <Segmented
          label="When"
          className="w-full [&>button]:flex-1"
          options={[
            { value: 'now', label: 'Send now' },
            { value: 'later', label: 'Pick a time' },
          ]}
          value={when}
          onChange={setWhen}
        />
        {when === 'later' && (
          <Field label="Goes out at" error={at && !atValid ? 'Pick a time in the future.' : undefined}>
            <Input
              type="datetime-local"
              size="sm"
              value={at}
              min={localInputValue(new Date())}
              onChange={(e) => setAt(e.target.value)}
            />
          </Field>
        )}
      </div>

      <p className="text-xs text-ink-2">{summary}</p>

      {limitReached && (
        <WarnBox className="text-sm">
          {data.promos_this_month} of {data.promo_limit_per_month} promotions are already sent or scheduled this
          month, so members who get both will be skipped — this may reach nobody. A notice is not limited.
        </WarnBox>
      )}

      {/* Sending pushes a lock-screen notification that cannot be taken back: always two taps. */}
      {when === 'now' ? (
        <ConfirmTwiceButton
          variant="primary"
          size="lg"
          className="w-full"
          armedLabel={
            limitReached
              ? 'Tap again to send anyway'
              : n === null
                ? 'Tap again to send it'
                : `Tap again to send to ${people(n)}`
          }
          onConfirm={submit}
          disabled={!ready}
          pending={pending}
          pendingLabel="Sending…"
        >
          {label}
        </ConfirmTwiceButton>
      ) : (
        <Button variant="primary" size="lg" block onClick={submit} disabled={!ready} pending={pending} pendingLabel="Sending…">
          {label}
        </Button>
      )}

      <StatusLine outcome={result} />
    </section>
  )
}
