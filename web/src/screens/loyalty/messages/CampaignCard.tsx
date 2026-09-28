/**
 * One campaign in the list: title, status, the message, when and to whom, and on the
 * right what happened (sent: received / came back) or what will (scheduled: will get
 * it, and Cancel). A draft can be sent from here.
 */
import { useState } from 'react'
import { Button, ConfirmTwiceButton, Pill, cx } from '../../../components/ui'
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

function Figure({ value, label, good }: { value: string; label: string; good?: boolean }) {
  return (
    <div className="flex min-w-[4.5rem] flex-col items-end text-right">
      <span className={cx('fig text-2xl font-extrabold leading-tight', good ? 'text-ok-ink' : 'text-ink')}>{value}</span>
      <span className="text-xs text-ink-2">{label}</span>
    </div>
  )
}

export function CampaignCard({ c }: { c: LoyaltyCampaign }) {
  const invalidate = useInvalidateLoyalty()
  const [pending, setPending] = useState(false)
  const [note, setNote] = useState<{ bad: boolean; text: string } | null>(null)
  const st = STATUS[c.status]

  async function cancel() {
    setPending(true)
    const r = await loyaltyApi.cancelCampaign(c.id)
    setPending(false)
    if (r.kind === 'ok') {
      setNote(null)
      await invalidate()
    } else setNote({ bad: true, text: r.message })
  }

  async function send() {
    setPending(true)
    const r = await loyaltyApi.sendCampaign(c.id)
    setPending(false)
    if (r.kind === 'ok') {
      const skipped = r.data.skipped_over_limit
      setNote({
        bad: false,
        text:
          `Sent to ${people(r.data.recipients)}.` +
          (skipped > 0 ? ` ${people(skipped)} skipped: they already had this month's promotions.` : ''),
      })
      await invalidate()
    } else setNote({ bad: true, text: r.message })
  }

  return (
    <article
      className={cx(
        'flex flex-col gap-3 rounded-card border border-line bg-surface px-4 py-3.5 sm:flex-row sm:items-center',
        c.status === 'cancelled' && 'opacity-70',
      )}
    >
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
        {note && (
          <p role="status" className={cx('mt-1 text-sm', note.bad ? 'text-bad-ink' : 'text-ok-ink')}>
            {note.text}
          </p>
        )}
      </div>
      <div className="flex flex-none items-center justify-end gap-4">
        {c.status === 'sent' && (
          <>
            <Figure value={c.recipients === null ? '—' : String(c.recipients)} label="received it" />
            <Figure
              value={c.return_rate === null ? '—' : `${Math.round(c.return_rate * 100)}%`}
              label={c.return_rate === null ? 'came back (too soon)' : 'came back in 7 days'}
              good={c.return_rate !== null}
            />
          </>
        )}
        {c.status === 'scheduled' && (
          <>
            <Figure value={c.audience === null ? '—' : String(c.audience)} label="will get it" />
            <ConfirmTwiceButton
              variant="outline"
              armedLabel="Tap again to cancel"
              onConfirm={cancel}
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
            <Button variant="outline" onClick={send} pending={pending} pendingLabel="Sending…">
              Send
            </Button>
          </>
        )}
      </div>
    </article>
  )
}
