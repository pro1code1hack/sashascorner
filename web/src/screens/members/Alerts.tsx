/**
 * Rewards › Alerts (`#/rewards/alerts`): stamping alerts (CONTRACT §7: one staff
 * user giving >20 stamps an hour, a manager overriding the cooldown). An alert is
 * a crossed threshold, so it is the one red thing on the Members list, which
 * links here for the full history.
 */
import { ErrorBox, Loading, PageBody, PageHeader, cx } from '../../components/ui'
import { ago, stamp } from '../../lib/format'
import { useAlerts, usePrograms } from '../../lib/members-api'
import { href } from '../../lib/router'
import type { MemberAlert } from '../../lib/types/members'
import { Panel } from './shared'

const WEEK_MS = 7 * 864e5

const KIND_LABEL: Record<string, string> = {
  stamp_rate: 'Fast stamping',
  staff_rate: 'Fast stamping',
  cooldown_override: 'Cooldown overridden',
}
export const alertKind = (k: string) => KIND_LABEL[k] ?? k.replace(/_/g, ' ')

/** The strip above the list: alerts from the last 7 days, or nothing. */
export function AlertStrip() {
  const q = useAlerts()
  const recent = (q.data?.alerts ?? []).filter((a) => Date.now() - Date.parse(a.at) < WEEK_MS)
  if (recent.length === 0) return null
  return (
    <section aria-labelledby="alerts-h" className="rounded-card border-[1.5px] border-dashed border-alert px-3.5 py-2.5">
      <div className="flex flex-wrap items-baseline justify-between gap-x-3">
        <h2 id="alerts-h" className="text-base font-bold text-bad-ink">
          {recent.length} stamping {recent.length === 1 ? 'alert' : 'alerts'} this week
        </h2>
        <a href={href('/rewards/alerts')} className="text-sm text-brand-ink underline">
          All alerts
        </a>
      </div>
      <ul className="mt-1">
        {recent.slice(0, 3).map((a, i) => (
          <li key={`${a.at}-${i}`} className="grid gap-x-3 py-0.5 text-sm sm:grid-cols-[8.5rem_minmax(0,1fr)]">
            <span className="fig text-ink-2">{ago(a.at)}</span>
            <span className="min-w-0">
              <b className="font-bold">{alertKind(a.kind)}.</b> {a.detail}
            </span>
          </li>
        ))}
      </ul>
    </section>
  )
}

export function AlertsScreen() {
  const q = useAlerts()
  const main = usePrograms().data?.programs.find((p) => p.is_default)
  return (
    <>
      <PageHeader title="Alerts" subtitle="Stamping that looks unusual, newest first. Each one is also sent to the owner’s Telegram when the bot is set up." saved={q.isFetching ? 'Loading…' : undefined} />
      <PageBody className="bg-canvas">
        <div className="grid gap-5 wide:grid-cols-[minmax(0,1fr)_minmax(260px,340px)]">
          <Panel title="Stamping alerts" id="alerts-full-h">
            {q.isError && <ErrorBox error={q.error} what="the alerts" />}
            {q.isPending && <Loading what="Reading the alerts" />}
            {q.data && <AlertList alerts={q.data.alerts} />}
          </Panel>
          <Panel title="What raises one" id="alerts-why-h" className="self-start">
            <ul className="flex list-disc flex-col gap-2 pl-5 text-base text-ink-2">
              <li>One member of staff gives more than 20 stamps in an hour.</li>
              <li>
                A manager approves stamps past the cooldown
                {main ? ` (more than ${main.cooldown_max_stamps} on one card within ${main.cooldown_minutes} minutes)` : ''}.
              </li>
            </ul>
            <p className="mt-3 text-sm text-ink-2">
              The cooldown is set on{' '}
              <a href={href('/rewards')} className="font-bold text-brand-ink underline">
                Programme
              </a>
              .
            </p>
          </Panel>
        </div>
      </PageBody>
    </>
  )
}

export function AlertList({ alerts, className }: { alerts: MemberAlert[]; className?: string }) {
  if (alerts.length === 0) {
    return <p className={cx('text-base text-ink-2', className)}>No stamping alerts. One appears when somebody gives more than 20 stamps in an hour, or a manager overrides the cooldown.</p>
  }
  return (
    <ul className={className}>
      {alerts.map((a, i) => (
        <li key={`${a.at}-${i}`} className="grid gap-x-3 border-b-[1.5px] border-dashed border-line py-1.5 text-base sm:grid-cols-[11rem_minmax(0,1fr)]">
          <span className="fig text-sm text-ink-2">{stamp(a.at)}</span>
          <span className="min-w-0">
            <b className="font-bold">{alertKind(a.kind)}.</b> {a.detail}
          </span>
        </li>
      ))}
    </ul>
  )
}
