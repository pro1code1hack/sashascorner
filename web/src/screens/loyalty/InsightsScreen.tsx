/**
 * Loyalty card › Insights (`#/loyalty/insights?days=90`): how the card is doing,
 * against the same length of time just before. docs/loyalty/BACKOFFICE-V2.md §3.
 *
 * Every figure is the server's; this screen only formats. A figure the server could
 * not work out (`null`) is a dash with the reason under it, never a 0.
 */
import { ErrorBox, Loading, PageBody, Segmented, WarnBox, cx } from '../../components/ui'
import { ago, plural } from '../../lib/format'
import { useInsights } from '../../lib/loyalty-api'
import { href, navigate, useLocation } from '../../lib/router'
import type { Insights, InsightsDays, Kpi } from '../../lib/types/loyalty'
import { ChartCard, Columns, ShareRows, useReadout, type ColumnDatum } from './insights/charts'
import { LoyaltyHeader } from './LoyaltyHeader'

const RANGES: { value: `${InsightsDays}`; label: string; before: string }[] = [
  { value: '30', label: '30 days', before: 'the 30 days before' },
  { value: '90', label: '90 days', before: 'the 90 days before' },
  { value: '180', label: '6 months', before: 'the 6 months before' },
]

function daysFromQuery(raw: string | null): InsightsDays {
  return raw === '30' ? 30 : raw === '180' ? 180 : 90
}

export function InsightsScreen() {
  const loc = useLocation()
  const days = daysFromQuery(loc.query.get('days'))
  const q = useInsights(days)
  const range = RANGES.find((r) => r.value === String(days)) ?? RANGES[1]!

  return (
    <>
      <LoyaltyHeader current="insights" />
      <PageBody className="bg-canvas">
        <div className="flex flex-col gap-4">
          <div className="flex flex-wrap items-center justify-between gap-3">
            <div className="min-w-0">
              <h2 className="text-xl font-extrabold tracking-[-.01em]">How the card is doing</h2>
              <p className="text-base text-ink-2">
                Compared with {range.before}.
                {q.isFetching && q.data && <span className="ml-2 text-ink-3">Updating…</span>}
              </p>
            </div>
            <Segmented
              label="Period"
              options={RANGES.map((r) => ({ value: r.value, label: r.label }))}
              value={range.value}
              onChange={(v) => navigate('/loyalty/insights', { replace: true, query: { days: v } })}
            />
          </div>
          {q.isError && !q.data ? (
            <ErrorBox error={q.error} what="the card's figures" />
          ) : !q.data ? (
            <Loading what="Counting stamps" />
          ) : (
            <Body data={q.data} />
          )}
        </div>
      </PageBody>
    </>
  )
}

/* ------------------------------------------------------------------ helpers --- */

const whole = (share: number) => `${Math.round(share * 100)}%`

/** Signed count delta: "+7", "−4", "±0". */
function countDelta(k: Kpi): { text: string; dir: -1 | 0 | 1 } | null {
  if (k.value === null || k.previous === null) return null
  const d = k.value - k.previous
  return { text: d > 0 ? `+${d}` : d < 0 ? `−${Math.abs(d)}` : '±0', dir: d > 0 ? 1 : d < 0 ? -1 : 0 }
}

/** Signed share delta in percentage points: "+5 pts". */
function shareDelta(k: Kpi): { text: string; dir: -1 | 0 | 1 } | null {
  if (k.value === null || k.previous === null) return null
  const d = Math.round(k.value * 100) - Math.round(k.previous * 100)
  const n = Math.abs(d)
  return {
    text: `${d > 0 ? '+' : d < 0 ? '−' : '±'}${n} ${plural(n, 'pt')}`,
    dir: d > 0 ? 1 : d < 0 ? -1 : 0,
  }
}

/** "29 Jun" from a local "YYYY-MM-DD". */
function weekLabel(ymd: string): string {
  const [y, m, d] = ymd.split('-').map(Number)
  if (!y || !m || !d) return ymd
  return new Intl.DateTimeFormat('en-GB', { day: 'numeric', month: 'short', timeZone: 'UTC' }).format(
    new Date(Date.UTC(y, m - 1, d)),
  )
}

function initials(name: string): string {
  const parts = name.trim().split(/\s+/).filter(Boolean)
  const a = parts[0]?.[0] ?? '?'
  const b = parts.length > 1 ? (parts[parts.length - 1]?.[0] ?? '') : ''
  return (a + b).toUpperCase()
}

/* --------------------------------------------------------------------- tiles --- */

function KpiTile({
  label,
  value,
  delta,
  sub,
}: {
  label: string
  value: string | null
  delta?: { text: string; dir: -1 | 0 | 1 } | null
  sub: string
}) {
  return (
    <div className="min-w-0 rounded-card border border-line bg-surface px-4 py-3.5">
      <div className="text-sm text-ink-2">{label}</div>
      <div className="mt-1 flex items-baseline gap-2">
        <span className="fig text-4xl font-extrabold leading-none tracking-[-.02em]">{value ?? '—'}</span>
        {value !== null && delta && (
          <span
            className={cx(
              'fig text-sm font-bold',
              delta.dir > 0 ? 'text-ok-ink' : delta.dir < 0 ? 'text-bad-ink' : 'text-ink-2',
            )}
            title="Change against the period before"
          >
            {delta.text}
          </span>
        )}
      </div>
      <div className="mt-1.5 text-xs text-ink-2">{sub}</div>
    </div>
  )
}

/* --------------------------------------------------------------------- body --- */

function Body({ data }: { data: Insights }) {
  const visits = data.visits_per_active_member_per_month
  const active = data.active_members.value
  return (
    <>
      <div className="grid grid-cols-2 gap-3 md:grid-cols-4 wide:grid-cols-5">
        <KpiTile
          label="Members"
          value={data.members.value === null ? null : String(data.members.value)}
          delta={countDelta(data.members)}
          sub={`${data.joined_in_window} joined in this period`}
        />
        <KpiTile
          label="Came in and stamped"
          value={active === null ? null : String(active)}
          delta={countDelta(data.active_members)}
          sub={
            active === null
              ? 'no stamps to count yet'
              : visits === null
                ? 'different people'
                : `different people, ${visits.toFixed(1)} visits a month each`
          }
        />
        <KpiTile
          label="Stamps given"
          value={data.stamps.value === null ? null : String(data.stamps.value)}
          delta={countDelta(data.stamps)}
          sub={
            data.stamps.value === null
              ? 'no stamps to count yet'
              : data.stamps_per_week > 0 && data.stamps_per_week < 0.5
                ? 'fewer than one a week'
                : `about ${Math.round(data.stamps_per_week)} a week`
          }
        />
        <KpiTile
          label="Free drinks"
          value={data.free_drinks.value === null ? null : String(data.free_drinks.value)}
          delta={countDelta(data.free_drinks)}
          sub="cards filled and used"
        />
        <KpiTile
          label="Came back"
          value={data.came_back.value === null ? null : whole(data.came_back.value)}
          delta={shareDelta(data.came_back)}
          sub={
            data.came_back.value === null
              ? 'nobody joined before this period yet'
              : 'of older members visited in this period'
          }
        />
        <KpiTile
          label="Opted in to offers"
          value={data.opted_in_share === null ? null : whole(data.opted_in_share)}
          sub={
            data.opted_in_share === null
              ? 'no members yet'
              : `${data.opted_in_count} ${plural(data.opted_in_count, 'person', 'people')} you can message`
          }
        />
      </div>

      <div className="grid grid-cols-1 gap-3 md:grid-cols-3">
        <WeeklyChart data={data} field="new_members" title="New members" tone="brand" unit={['new member', 'new members']} />
        <WeeklyChart data={data} field="stamps" title="Stamps given" tone="brand" unit={['stamp', 'stamps']} />
        <WeeklyChart data={data} field="free_drinks" title="Free drinks" tone="ok" unit={['free drink', 'free drinks']} />
      </div>

      <div className="grid grid-cols-1 gap-3 md:grid-cols-2 wide:grid-cols-3">
        <SourcesCard data={data} />
        <HoursCard data={data} />
        <RegularsCard data={data} />
      </div>

      {data.alerts.length > 0 && (
        <WarnBox>
          <div className="font-bold">Needs a look</div>
          <ul className="mt-1 flex flex-col gap-1">
            {data.alerts.map((a, i) => (
              <li key={`${a.at}-${i}`} className="flex flex-wrap items-baseline justify-between gap-x-3 text-base">
                <span className="min-w-0">{a.detail}</span>
                <span className="fig flex-none text-xs text-ink-2">{ago(a.at)}</span>
              </li>
            ))}
          </ul>
        </WarnBox>
      )}
    </>
  )
}

function WeeklyChart({
  data,
  field,
  title,
  tone,
  unit,
}: {
  data: Insights
  field: 'new_members' | 'stamps' | 'free_drinks'
  title: string
  tone: 'brand' | 'ok'
  unit: [string, string]
}) {
  const [readout, setReadout] = useReadout()
  const weeks = data.weekly
  const total = weeks.reduce((a, w) => a + w[field], 0)
  const bars: ColumnDatum[] = weeks.map((w, i) => {
    const n = w[field]
    const when = i === weeks.length - 1 ? 'This week' : `Week of ${weekLabel(w.week_start)}`
    return { key: w.week_start, value: n, readout: `${when}: ${n} ${plural(n, unit[0], unit[1])}` }
  })
  return (
    <ChartCard title={title} corner={readout ?? total}>
      {weeks.length === 0 ? (
        <p className="py-6 text-center text-sm text-ink-2">No weeks in this period yet.</p>
      ) : (
        <Columns
          data={bars}
          tone={tone}
          label={`${title} by week, ${total} in all`}
          onHover={setReadout}
          firstLabel={weekLabel(weeks[0]!.week_start)}
          lastLabel="this week"
        />
      )}
    </ChartCard>
  )
}

function SourcesCard({ data }: { data: Insights }) {
  const rows = data.by_source.map((s) => ({
    key: s.source ?? '(none)',
    label: s.label,
    count: s.members,
    share: whole(s.share),
    fraction: s.share,
  }))
  return (
    <ChartCard title="Where members joined" sub="From the tag on each QR code.">
      {rows.length === 0 ? (
        <p className="py-4 text-sm text-ink-2">Nobody has joined yet.</p>
      ) : (
        <ShareRows rows={rows} label="Members by where they joined" />
      )}
    </ChartCard>
  )
}

function hourLabel(h: number): string {
  return h === 12 ? '12' : String(h > 12 ? h - 12 : h)
}

function HoursCard({ data }: { data: Insights }) {
  const [readout, setReadout] = useReadout()
  const peak = Math.max(0, ...data.hours.map((h) => h.stamps))
  const total = data.hours.reduce((a, h) => a + h.stamps, 0)
  const range = RANGES.find((r) => r.value === String(data.days))
  const bars: ColumnDatum[] = data.hours.map((h, i) => ({
    key: String(h.hour),
    value: h.stamps,
    strong: peak > 0 && h.stamps === peak,
    readout: `${String(h.hour).padStart(2, '0')}:00–${String(h.hour + 1).padStart(2, '0')}:00: ${h.stamps} ${plural(h.stamps, 'stamp')}`,
    // Every other hour, so twelve-plus labels never collide at iPad width.
    axis: i % 2 === 0 ? hourLabel(h.hour) : '',
  }))
  return (
    <ChartCard
      title="Busiest stamping hours"
      sub={`Stamps by hour of day, last ${range?.label ?? `${data.days} days`}.`}
      corner={readout ?? undefined}
    >
      {total === 0 ? (
        <p className="py-4 text-sm text-ink-2">No stamps in this period yet.</p>
      ) : (
        <Columns data={bars} tone="seq" height={88} label="Stamps by hour of day" onHover={setReadout} />
      )}
    </ChartCard>
  )
}

function RegularsCard({ data }: { data: Insights }) {
  const range = RANGES.find((r) => r.value === String(data.days))
  return (
    <ChartCard title="Regulars" sub={`Most stamps, last ${range?.label ?? `${data.days} days`}.`}>
      {data.regulars.length === 0 ? (
        <p className="py-4 text-sm text-ink-2">Nobody has stamped in this period yet.</p>
      ) : (
        <ol className="flex flex-col">
          {data.regulars.map((r) => (
            <li key={r.member_id} className="flex items-center gap-3 border-b border-line-row py-2 last:border-b-0">
              <span
                className="grid size-7 flex-none place-items-center rounded-full bg-brand-wash text-label font-bold text-brand-ink"
                aria-hidden="true"
              >
                {initials(r.first_name)}
              </span>
              <a
                href={href(`/loyalty/members/${r.member_id}`)}
                className="min-w-0 flex-1 truncate text-base font-semibold text-ink no-underline hover:text-brand-ink hover:underline"
              >
                {r.first_name}
              </a>
              <span className="fig flex-none text-sm text-ink-2">
                {r.stamps} {plural(r.stamps, 'stamp')}
              </span>
            </li>
          ))}
        </ol>
      )}
    </ChartCard>
  )
}
