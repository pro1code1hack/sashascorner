/**
 * Rewards › Campaigns (`#/rewards/campaigns`): a lock-screen message to a segment of opted-in members
 * (SPEC.md phase 2; CONTRACT §6).
 *
 * The rule on show: at most `promo_limit_per_month` promotional messages per
 * member per calendar month (PECR; SPEC "≤2 promos/month"). The server
 * enforces it member by member and reports how many it skipped; this page
 * shows how many promos have gone out this month and says so before sending.
 * Sending is two steps: "Send…" opens an inline confirmation with the number
 * of recipients, then "Send now".
 */
import { useState } from 'react'
import { useQueryClient } from '@tanstack/react-query'
import { Button, Checkbox, ErrorBox, Field, Input, Loading, PageBody, PageHeader, Pill, Select, Textarea, cx } from '../../components/ui'
import { dayFull, stamp } from '../../lib/format'
import { MEMBERS_KEY, membersApi, useCampaigns } from '../../lib/members-api'
import type { Campaign, CampaignSegment } from '../../lib/types/members'
import { CAMPAIGN_SEGMENT, OutcomeLine, Panel, share, useWriteState } from './shared'

const MAX = 200

function londonMonth(iso: string): string {
  return new Intl.DateTimeFormat('en-CA', { year: 'numeric', month: '2-digit', timeZone: 'Europe/London' }).format(new Date(iso))
}

export function Campaigns() {
  const q = useCampaigns()
  const data = q.data
  const thisMonth = londonMonth(new Date().toISOString())
  const promosThisMonth = (data?.campaigns ?? []).filter((c) => c.is_promo && c.sent_at && londonMonth(c.sent_at) === thisMonth).length
  const limit = data?.promo_limit_per_month ?? 2
  return (
    <>
      <PageHeader title="Campaigns" subtitle="Lock-screen messages to members who opted in. At most two promotions a person a month." saved={q.isFetching ? 'Loading…' : undefined} />
      <PageBody className="bg-canvas">
        {q.isError && <ErrorBox error={q.error} what="the campaigns" />}
        {q.isPending && <Loading what="Reading the campaigns" />}
        {data && (
          <div className="grid gap-5 compact:grid-cols-[minmax(0,1fr)_minmax(300px,400px)]">
            <div className="flex min-w-0 flex-col gap-5">
              <section aria-labelledby="rule-h" className="min-w-0">
                <h2 id="rule-h" className="text-2xl font-extrabold tracking-[-.01em]">
                  <span className="fig">{promosThisMonth}</span> of <span className="fig">{limit}</span> promos sent this month
                </h2>
                <p className="mt-1 max-w-[70ch] text-base text-ink-2">
                  Each member gets at most {limit} promotional messages a calendar month; anyone already at {limit} is skipped when you send.
                  Card updates (a stamp, a free drink ready, a birthday drink) are not promotions and do not count. Only members who opted in
                  receive campaigns, and every message carries an unsubscribe link.
                </p>
              </section>
              <CampaignList campaigns={data.campaigns} limit={limit} promosThisMonth={promosThisMonth} />
            </div>
            <Compose />
          </div>
        )}
      </PageBody>
    </>
  )
}

/* -------------------------------------------------------------- compose --- */

function Compose() {
  const qc = useQueryClient()
  const [title, setTitle] = useState('')
  const [message, setMessage] = useState('')
  const [segment, setSegment] = useState<CampaignSegment>('ALL_OPTED_IN')
  const [promo, setPromo] = useState(true)
  const [later, setLater] = useState('')
  const w = useWriteState()
  const len = [...message].length
  const over = len > MAX
  const laterIso = later ? new Date(later).toISOString() : null
  const laterPast = laterIso !== null && Date.parse(laterIso) <= Date.now()
  const ready = title.trim() !== '' && message.trim() !== '' && !over && !laterPast

  return (
    <Panel title="New campaign" id="compose-h" className="self-start">
      <form
        className="flex flex-col gap-3"
        onSubmit={async (e) => {
          e.preventDefault()
          if (!ready) return
          const r = await w.run(
            () => membersApi.createCampaign({ title: title.trim(), message: message.trim(), segment, is_promo: promo, scheduled_at: laterIso }),
            (c) => (c.scheduled_at ? `Saved. It goes out ${stamp(c.scheduled_at)}.` : 'Saved. Send it from the list when you are ready.'),
          )
          if (r) {
            setTitle('')
            setMessage('')
            setLater('')
            void qc.invalidateQueries({ queryKey: [...MEMBERS_KEY, 'campaigns'] })
          }
        }}
      >
        <Field label="Title" hint="For you; members do not see it.">
          <Input value={title} onChange={(e) => setTitle(e.target.value)} maxLength={120} placeholder="Autumn menu" />
        </Field>
        <Field
          label="Message"
          hint={
            <span className={cx('fig', over && 'font-bold text-bad-ink')}>
              {len}/{MAX} characters · shown on the lock screen
            </span>
          }
          error={over ? `Cut ${len - MAX} ${len - MAX === 1 ? 'character' : 'characters'}: a lock-screen message stops at ${MAX}.` : undefined}
        >
          <Textarea value={message} onChange={(e) => setMessage(e.target.value)} placeholder="New autumn menu from Thursday…" className="min-h-24" />
        </Field>
        <Field label="Who gets it" hint={CAMPAIGN_SEGMENT[segment].hint}>
          <Select value={segment} onChange={(e) => setSegment(e.target.value as CampaignSegment)}>
            {(Object.keys(CAMPAIGN_SEGMENT) as CampaignSegment[]).map((k) => (
              <option key={k} value={k}>
                {CAMPAIGN_SEGMENT[k].label}
              </option>
            ))}
          </Select>
        </Field>
        <div className="flex flex-col gap-1">
          <Checkbox checked={promo} onChange={setPromo} label="This is a promotion" />
          <p className="text-xs text-ink-2">
            Untick only for a service notice (closed for a holiday, a changed opening time). Promotions count towards the monthly limit.
          </p>
        </div>
        <Field label="Send later (optional)" hint="Leave empty to send by hand from the list." error={laterPast ? 'That time has passed.' : undefined}>
          <Input type="datetime-local" value={later} onChange={(e) => setLater(e.target.value)} className="focus:edge-brand" />
        </Field>
        <div>
          <Button type="submit" variant="primary" size="sm" disabled={!ready} pending={w.busy} pendingLabel="Saving…">
            {later ? 'Schedule campaign' : 'Save campaign'}
          </Button>
        </div>
        <OutcomeLine outcome={w.outcome} />
      </form>
    </Panel>
  )
}

/* ----------------------------------------------------------------- list --- */

function CampaignList({ campaigns, limit, promosThisMonth }: { campaigns: Campaign[]; limit: number; promosThisMonth: number }) {
  if (campaigns.length === 0) {
    return <p className="rounded-card border border-dashed border-line-strong px-4 py-6 text-center text-base text-ink-2">No campaigns yet. Write one on the right.</p>
  }
  const sorted = [...campaigns].sort((a, b) => (a.sent_at === null ? 0 : 1) - (b.sent_at === null ? 0 : 1) || b.created_at.localeCompare(a.created_at))
  return (
    <ul className="flex flex-col gap-3">
      {sorted.map((c) => (
        <CampaignRow key={c.id} c={c} limit={limit} promosThisMonth={promosThisMonth} />
      ))}
    </ul>
  )
}

function CampaignRow({ c, limit, promosThisMonth }: { c: Campaign; limit: number; promosThisMonth: number }) {
  const qc = useQueryClient()
  const [confirming, setConfirming] = useState(false)
  const w = useWriteState()
  const seg = CAMPAIGN_SEGMENT[c.segment]
  const returned = c.returned ?? null
  return (
    <li className="rounded-card-lg border border-line bg-surface px-4 py-3.5">
      <div className="flex flex-wrap items-baseline justify-between gap-x-3 gap-y-1">
        <h3 className="text-md font-bold">{c.title}</h3>
        <span className="flex flex-wrap items-center gap-1.5">
          {c.is_promo ? <Pill tone="neutral">Promotion</Pill> : <Pill tone="muted">Service notice</Pill>}
          {c.sent_at ? <Pill tone="neutral">Sent</Pill> : c.scheduled_at ? <Pill tone="brand">Scheduled</Pill> : <Pill tone="brand">Not sent</Pill>}
        </span>
      </div>
      <blockquote className="mt-1.5 border-l-2 border-line-strong pl-3 text-base">{c.message}</blockquote>
      <p className="mt-2 text-sm text-ink-2">
        To: {seg.label} · written by {c.created_by}, {dayFull(c.created_at)}
      </p>
      {c.sent_at ? (
        <dl className="mt-2 flex flex-wrap gap-x-6 gap-y-1 text-base">
          <div>
            <dt className="text-xs font-bold text-ink-2">Sent</dt>
            <dd className="fig">{stamp(c.sent_at)}</dd>
          </div>
          <div>
            <dt className="text-xs font-bold text-ink-2">Recipients</dt>
            <dd className="fig">{c.recipients ?? '—'}</dd>
          </div>
          <div>
            <dt className="text-xs font-bold text-ink-2">Came back within 7 days</dt>
            <dd className="fig">
              {returned === null ? (
                <span className="text-sm text-ink-2">not reported</span>
              ) : (
                <>
                  {returned}
                  {c.recipients ? <span className="text-ink-2"> · {share(returned / c.recipients)}</span> : null}
                </>
              )}
            </dd>
          </div>
        </dl>
      ) : (
        <div className="mt-3">
          {c.scheduled_at && <p className="mb-2 text-sm text-ink-2">Goes out automatically {stamp(c.scheduled_at)}.</p>}
          {!confirming ? (
            <Button variant="outline" onClick={() => setConfirming(true)} disabled={w.outcome?.tone === 'ok'}>
              {c.scheduled_at ? 'Send now instead…' : 'Send…'}
            </Button>
          ) : (
            <div className="rounded-button border-[1.5px] border-dashed border-line-strong px-3 py-2.5">
              <p className="text-base">
                {typeof c.audience === 'number' ? (
                  <>
                    Send to <b className="fig">{c.audience}</b> {c.audience === 1 ? 'member' : 'members'} ({seg.label.toLowerCase()}) now?
                  </>
                ) : (
                  <>Send to every member in “{seg.label}” now? The count is shown once it has gone.</>
                )}{' '}
                It appears on their lock screen and cannot be recalled.
              </p>
              {c.is_promo && (
                <p className="mt-1 text-sm text-ink-2">
                  {promosThisMonth >= limit
                    ? `${promosThisMonth} promos have already gone out this month, so most members are at the limit of ${limit} and will be skipped.`
                    : `This is promo ${promosThisMonth + 1} of ${limit} this month for anyone who received the earlier ones; members already at ${limit} are skipped.`}
                </p>
              )}
              <div className="mt-2.5 flex flex-wrap gap-2">
                <Button
                  variant="primary"
                  size="sm"
                  pending={w.busy}
                  pendingLabel="Sending…"
                  onClick={async () => {
                    const r = await w.run(
                      () => membersApi.sendCampaign(c.id),
                      (d) =>
                        `Sent to ${d.recipients} ${d.recipients === 1 ? 'member' : 'members'}` +
                        (d.skipped_over_limit > 0 ? `; ${d.skipped_over_limit} skipped, already at ${limit} promos this month.` : '.'),
                    )
                    if (r) {
                      setConfirming(false)
                      void qc.invalidateQueries({ queryKey: [...MEMBERS_KEY, 'campaigns'] })
                    }
                  }}
                >
                  Send now
                </Button>
                <Button variant="secondary" size="sm" onClick={() => setConfirming(false)} disabled={w.busy}>
                  Not yet
                </Button>
              </div>
            </div>
          )}
          <OutcomeLine outcome={w.outcome} className="mt-2" />
        </div>
      )}
    </li>
  )
}
