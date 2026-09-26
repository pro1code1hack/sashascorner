/**
 * Channels. Spec §4.6 and §10's build order.
 *
 * Deliveroo and Just Eat are an advertising platform as much as a sales channel:
 * impressions, ranking and ad spend sit alongside commission, and none of it is
 * visible in the POS. This screen is where that becomes contribution.
 *
 * It has two states and both are live code:
 *
 *  - Fed. Two channels report from a CSV export. The figures are real and the
 *    coverage is partial — nine days of a 28-day window, and Just Eat's
 *    contribution covers seven of its nine — so every total on the screen says
 *    what it covers. A £915 gross over nine reporting days is not a monthly
 *    figure and nothing here is allowed to let it read as one.
 *  - Empty. A fresh deployment has no channel rows at all, and the empty state
 *    is kept in full because it is the first thing that deployment meets: the
 *    two commands that load data, and why there are commands instead of a sync
 *    button (the partner APIs are gated to certified POS integrators).
 *
 * The screen chooses between them on `performance.length`, and nothing is
 * invented on either side of that branch.
 */
import { useQuery } from '@tanstack/react-query'
import { api } from '../lib/api'
import { fromInt, sum } from '../lib/dec'
import type { Dec } from '../lib/dec'
import {
  Badge,
  Card,
  Chip,
  Empty,
  ErrorBox,
  Loading,
  PageHeader,
  Panel,
  SectionLabel,
  StatCard,
} from '../components/ui'
import { Label } from '../components/prim'
import { MD } from './channels/figures'
import { Findings, PerformanceTable } from './channels/Performance'
import { LoadIt, Matching, WhatLandsHere, WhyManual } from './channels/Setup'
import {
  channelName,
  day,
  prose,
  readFindings,
  readPerformance,
  windowDays,
} from './channels/shape'
import type { ChannelDay, ChannelPerformance } from './channels/shape'

/** A row of bordered stat cards. Never a bare `1fr` or an implicit auto track:
 *  both carry a min-content floor, and one wide figure then pushes the whole page
 *  sideways instead of the one block that is too wide. */
function CardRow({ children }: { children: React.ReactNode }) {
  return (
    <div className="grid grid-cols-[minmax(0,1fr)] gap-4 sm:grid-cols-2 lg:grid-cols-4">
      {children}
    </div>
  )
}

/** Sum one integer-pence field across channels, exactly, naming what had to be
 *  left out. A channel that did not report the field is excluded from the total
 *  and said so — it is not a zero. */
function aggregate(
  rows: readonly ChannelPerformance[],
  pick: (r: ChannelPerformance) => number | null,
): { value: Dec | null; excluded: string[] } {
  const present: Dec[] = []
  const excluded: string[] = []
  for (const r of rows) {
    const v = pick(r)
    if (v === null) excluded.push(channelName(r.channel))
    else present.push(fromInt(v))
  }
  return { value: present.length === 0 ? null : sum(present), excluded }
}

/**
 * The window as a day-by-day series, summed across channels.
 *
 * Only days that actually reported are included, and a day where no channel
 * reported the field is dropped rather than plotted as zero -- a sparkline that
 * dips to the floor on a missing export says "trade collapsed", which is a lie.
 * Returns nothing below two points: a shape drawn through one point is decoration.
 */
function series(rows: ChannelPerformance[], pick: (d: ChannelDay) => number | null): number[] {
  const byDate = new Map<string, number>()
  for (const r of rows) {
    for (const d of r.daily) {
      const v = pick(d)
      if (v === null) continue
      byDate.set(d.metric_date, (byDate.get(d.metric_date) ?? 0) + v)
    }
  }
  const dates = [...byDate.keys()].sort()
  return dates.length < 2 ? [] : dates.map((k) => byDate.get(k) as number)
}

function Totals({
  rows,
  missing,
}: {
  rows: ChannelPerformance[]
  /** Why every figure is absent, when none has been loaded at all. */
  missing: string
}) {
  const gross = aggregate(rows, (r) => r.gross_pence)
  const commission = aggregate(rows, (r) => r.commission_pence)
  const ads = aggregate(rows, (r) => r.ad_spend_pence)
  const net = aggregate(rows, (r) => r.net_pence)

  const sub = (a: { excluded: string[] }, ok: string) =>
    a.excluded.length > 0 ? `excludes ${a.excluded.join(', ')} — not reported` : ok

  const grossDays = series(rows, (d) => d.gross_pence)
  const adDays = series(rows, (d) => d.ad_spend_pence)

  return (
    <CardRow>
      <StatCard
        label="Gross"
        value={<MD v={gross.value} missing={missing} />}
        sub={sub(gross, 'before commission and ads')}
        spark={grossDays}
        sparkTone="ok"
      />
      <StatCard
        label="Commission"
        value={<MD v={commission.value} missing={missing} />}
        sub={sub(commission, "the platform's cut")}
      />
      <StatCard
        label="Ad spend"
        value={<MD v={ads.value} missing={missing} />}
        spark={adDays}
        sparkTone="warn"
        sub={sub(ads, 'invisible in the POS')}
      />
      <StatCard
        label="Net contribution"
        value={<MD v={net.value} missing={missing} />}
        sub={sub(net, 'after commission and ads')}
      />
    </CardRow>
  )
}

export function Channels() {
  const q = useQuery({ queryKey: ['channels'], queryFn: () => api.channels() })

  if (q.isPending) {
    return (
      <>
        <PageHeader title="Channels" />
        <Loading what="Reading channel metrics" />
      </>
    )
  }
  if (q.isError || !q.data) {
    return (
      <>
        <PageHeader title="Channels" />
        <ErrorBox error={q.error} />
      </>
    )
  }

  const d = q.data
  const performance = readPerformance(d.performance)
  const findings = readFindings(d.findings)
  const days = windowDays(d.since, d.until)
  const empty = performance.length === 0

  /** Reporting days, read off the rows. Never averaged into one number: two
   *  channels that reported different day counts do not share a denominator. */
  const reportedDays = [...new Set(performance.map((r) => r.days))].sort((a, b) => a - b)
  const coverage =
    reportedDays.length === 0
      ? null
      : reportedDays.length === 1
        ? `${reportedDays[0]} of ${days ?? '?'} days reported`
        : `${reportedDays[0]}–${reportedDays[reportedDays.length - 1]} of ${days ?? '?'} days reported`

  return (
    <>
      <PageHeader
        title="Channels"
        lede={
          <>
            Deliveroo and Just Eat, after commission and after ad spend. A delivery platform sells
            for you and advertises to you, and the POS shows neither charge.
          </>
        }
        right={
          <div className="text-right">
            <div className="fig text-[0.875rem] text-ink-2">
              {day(d.since)} — {day(d.until)}
            </div>
            <div className="mt-1.5 flex flex-wrap items-center justify-end gap-1">
              <Label>{days === null ? 'window not read' : `${days} day window`}</Label>
              {d.channels_present.length === 0 ? (
                <Chip tone="muted">no channel reporting</Chip>
              ) : (
                d.channels_present.map((c) => <Chip key={c}>{channelName(c)}</Chip>)
              )}
            </div>
          </div>
        }
      />

      <div className="grid grid-cols-[minmax(0,1fr)] gap-6">
        <div>
          <SectionLabel right={coverage ?? undefined}>
            {empty ? 'What would be here' : 'The window, across every reporting channel'}
          </SectionLabel>
          <Totals rows={performance} missing={empty ? 'no export loaded' : 'not reported'} />
          <div className="mt-4">
            {empty ? (
              <Panel title="Four figures with nothing behind them">
                They are blank rather than zero because zero would read as a month with no delivery
                orders, which is a different business from a month nobody exported.
              </Panel>
            ) : (
              <Panel tone="warn" title="These are reporting-day totals, not monthly ones">
                The window is <span className="fig">{days ?? '—'}</span> days long and the export
                covers <span className="fig">{reportedDays.join(' and ')}</span> of them per
                channel. Every figure above is the sum over the days that reported; the rest are
                absent, not zero, so none of this can be read as a month. The per-channel rows
                below carry the field-by-field coverage.
              </Panel>
            )}
          </div>
        </div>

        {empty ? (
          <>
            <Card title="No channel metrics in this window" right={<Badge tone="muted">0 rows</Badge>}>
              <Empty title={`Nothing reported between ${day(d.since)} and ${day(d.until)}`}>
                No source has written a channel row for this window. The demo seed writes none, so
                this is an empty table rather than a bad month — and until something is imported
                there is no contribution, no ROAS and no ranking to read.
              </Empty>
              <div className="grid grid-cols-[minmax(0,1fr)] gap-6 border-t border-line pt-5 lg:grid-cols-2 lg:gap-8">
                <div className="min-w-0">
                  <SectionLabel>Load it</SectionLabel>
                  <LoadIt />
                </div>
                <div className="min-w-0 border-t border-line pt-5 lg:border-l lg:border-t-0 lg:pt-0 lg:pl-8">
                  <SectionLabel>Why an export and not a sync</SectionLabel>
                  <WhyManual />
                </div>
              </div>
            </Card>

            <Card
              title="What lands here once it is fed"
              subtitle="Three questions this screen answers, and nothing else does."
            >
              <WhatLandsHere />
            </Card>
          </>
        ) : (
          <>
            <PerformanceTable rows={performance} windowDays={days} />
            {findings.length > 0 && <Findings findings={findings} />}
          </>
        )}

        {/* Provenance, and the API's own words. Rendered verbatim: the reason
            this screen says what it says is a fact about the world, not our
            summary of one. */}
        <Card
          title="Provenance"
          subtitle="Who reported, how it arrived, and what the importer refused to guess."
        >
          <div className="grid grid-cols-[minmax(0,1fr)] gap-5 sm:grid-cols-2">
            <div className="min-w-0">
              <SectionLabel>Channels reporting</SectionLabel>
              {d.channels_present.length === 0 ? (
                <p className="text-[0.875rem] text-ink-2">
                  None. Deliveroo and Just Eat are configured as sales channels; neither has
                  written a metric row.
                </p>
              ) : (
                <div className="flex flex-wrap gap-1">
                  {d.channels_present.map((c) => (
                    <Chip key={c}>{channelName(c)}</Chip>
                  ))}
                </div>
              )}
            </div>
            <div className="min-w-0">
              <SectionLabel>Sources on file</SectionLabel>
              {d.sources_present.length === 0 ? (
                <p className="text-[0.875rem] text-ink-2">
                  None. Every row would record whether it came from a hand export or a browser
                  agent, because the two are not equally trustworthy.
                </p>
              ) : (
                <div className="flex flex-wrap gap-1">
                  {d.sources_present.map((s) => (
                    <Chip key={s}>{s.replace(/_/g, ' ').toLowerCase()}</Chip>
                  ))}
                </div>
              )}
            </div>
          </div>

          {(!empty || d.notes.length > 0) && (
            <div className="mt-5 grid grid-cols-[minmax(0,1fr)] gap-3 border-t border-line pt-4">
              {/* Only once something has actually been imported: on a fresh
                  deployment there is no import run to have skipped anything,
                  and a note about one would be a fact about nothing. */}
              {!empty && <Matching />}
              {d.notes.length > 0 && (
                <>
                  <SectionLabel>Reported by the API</SectionLabel>
                  {d.notes.map((n) => (
                    <Panel key={n}>{prose(n)}</Panel>
                  ))}
                </>
              )}
            </div>
          )}
        </Card>
      </div>
    </>
  )
}
