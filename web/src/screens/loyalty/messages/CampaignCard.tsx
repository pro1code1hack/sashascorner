/**
 * One campaign in the list: title, status, the message, when and to whom, and on the
 * right what happened (sent: received / came back) or what will (scheduled: will get
 * it, and Cancel). A draft can be sent from here.
 */
import { useState } from 'react'
import { ConfirmTwiceButton, Pill, StatusLine } from '../../../components/ui'
import type { Outcome } from '../../../components/ui'
import { loyaltyApi, useInvalidateLoyalty } from '../../../lib/loyalty-api'
import type { LoyaltyCampaign } from '../../../lib/types/loyalty'
import { people, segmentPhrase, weekdayDate, weekdayDateTime } from './words'

const STATUS = {
  scheduled: { tone: 'brand', text: 'Scheduled' },
  sent: { tone: 'ok', text: 'Sent' },
  cancelled: { tone: 'muted', text: 'Cancelled' },
  draft: { tone: 'neutral', text: 'Draft' },
} as const

function metaLine(c: LoyaltyCampaign): string {
  const to = segmentPhrase(c.segment, c.is_promo) + (c.is_promo ? '' : ' (notices only)')
  switch (c.status) {
    case 'sent':
      return `Sent ${c.sent_at ? weekdayDate(c.sent_at) : ''} · to ${to}`
    case 'scheduled':
      return `Goes out ${c.scheduled_at ? weekdayDateTime(c.scheduled_at) : ''} · to ${to}`
    case 'cancelled':
      return `Cancelled ${c.cancelled_at ? weekdayDate(c.cancelled_at) : ''} · was for ${to}`
    case 'draft':
      return `Not sent yet · to ${to}`
  }
}

function Figure({ value, label }: { value: string; label: string }) {
  return (
    <div className="flex min-w-[4.5rem] flex-col items-end text-right">
      <span className="fig text-2xl font-extrabold leading-tight text-ink">{value}</span>
      <span className="text-xs text-ink-2">{label}</span>
    </div>
  )
}

export function CampaignCard({ c }: { c: LoyaltyCampaign }) {
  const invalidate = useInvalidateLoyalty()
  const [pending, setPending] = useState(false)
  const [note, setNote] = useState<Outcome | null>(null)
  const st = STATUS[c.status]

  async function cancel() {
    setPending(true)
    const r = await loyaltyApi.cancelCampaign(c.id)
    setPending(false)
    if (r.kind === 'ok') {
      setNote(null)
      await invalidate()
    } else setNote({ kind: 'error', text: r.message })
  }

  async function send() {
    setPending(true)
    const r = await loyaltyApi.sendCampaign(c.id)
    setPending(false)
    if (r.kind === 'ok') {
      const skipped = r.data.skipped_over_limit
      setNote({
        kind: 'ok',
        text:
          `Sent to ${people(r.data.recipients)}.` +
          (skipped > 0 ? ` ${people(skipped)} skipped: they already had this month's promotions.` : ''),
      })
      await invalidate()
    } else setNote({ kind: 'error', text: r.message })
  }

  return (
    <article className="flex flex-col gap-3 rounded-card border border-line bg-surface px-4 py-3.5 sm:flex-row sm:items-center">
      <div className="min-w-0 flex-1">
        <div className="flex flex-wrap items-center gap-2">
          <h3 className="text-md font-bold">{c.title}</h3>
          <Pill tone={st.tone} className="h-5 px-2 text-label">
            {st.text}
          </Pill>
          {c.is_promo && (
            <span className="inline-flex h-5 items-center rounded-full border border-line px-2 text-label text-ink-2">
              promotion
            </span>
          )}
        </div>
        <p className="mt-1 text-base text-ink">{c.message}</p>
        <p className="mt-1 text-xs text-ink-2">{metaLine(c)}</p>
        <StatusLine className="mt-1" outcome={note} />
      </div>
      <div className="flex flex-none items-center justify-end gap-4">
        {c.status === 'sent' && (
          <>
            <Figure value={c.recipients === null ? '—' : String(c.recipients)} label="received it" />
            <Figure
              value={c.return_rate === null ? '—' : `${Math.round(c.return_rate * 100)}%`}
              label={c.return_rate === null ? 'came back (too soon)' : 'came back in 7 days'}
            />
          </>
        )}
        {c.status === 'scheduled' && (
          <>
            <Figure value={c.audience === null ? '—' : String(c.audience)} label="will get it" />
            <ConfirmTwiceButton
              variant="outline"
              armedLabel="Tap again to cancel"
              onConfirm={() => void cancel()}
              pending={pending}
              pendingLabel="Cancelling…"
            >
              Cancel
            </ConfirmTwiceButton>
          </>
        )}
        {c.status === 'draft' && (
          <>
            <Figure value={c.audience === null ? '—' : String(c.audience)} label="would get it" />
            <ConfirmTwiceButton
              variant="outline"
              armedLabel={c.audience === null ? 'Tap again to send it' : `Tap again to send to ${people(c.audience)}`}
              onConfirm={() => void send()}
              pending={pending}
              pendingLabel="Sending…"
            >
              Send
            </ConfirmTwiceButton>
          </>
        )}
      </div>
    </article>
  )
}
