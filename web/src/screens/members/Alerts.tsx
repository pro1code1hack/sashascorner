/**
 * Stamping alerts (CONTRACT §7: one staff user giving >20 stamps an hour, and
 * the like). An alert is a crossed threshold, so it is the one red thing on
 * the Members list; the full list lives on Staff & devices.
 */
import { cx } from '../../components/ui'
import { ago, stamp } from '../../lib/format'
import { useAlerts } from '../../lib/members-api'
import { href } from '../../lib/router'
import type { MemberAlert } from '../../lib/types/members'

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
        <a href={href('/members/staff')} className="text-sm text-brand-ink underline">
          All alerts and staff
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

export function AlertList({ alerts, className }: { alerts: MemberAlert[]; className?: string }) {
  if (alerts.length === 0) {
    return <p className={cx('text-base text-ink-2', className)}>No stamping alerts. One appears when somebody gives more than 20 stamps in an hour.</p>
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
