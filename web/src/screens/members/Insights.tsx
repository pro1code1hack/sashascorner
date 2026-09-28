/**
 * Rewards › Insights (`#/rewards/insights`): the spec's 90-day success metrics (SPEC.md "Success
 * metrics"), from `GET /api/members/stats?days=90`.
 *
 * Numbers first, as a statement (label, what it means, the figure), not a KPI
 * card grid. A figure the server could not work out is `null` and is replaced
 * by the reason, never printed as 0.
 *
 * Charts (dataviz): new members, stamps and redemptions differ in scale by an
 * order of magnitude, so they are three small multiples on one shared time
 * axis, each with its own y-axis: never one chart with two scales. Each is a
 * single series in the brand colour (a single series needs no legend; the
 * title names it). Hover or focus a bar for its value; the table under the
 * charts is the table view.
 *
 * Targets (SPEC: "Exact targets are an open question", so the owner sets them):
 * each figure shows its target and how far along it is; "Set targets" edits them
 * (`PUT /api/members/targets`). A figure with no target shows none, never 0.
 *
 * Phase 3: a programme selector (every figure is one programme's), and the spec's
 * "repeat-visit rate, members vs non-members" from Lightspeed till customers -- or,
 * when no receipt names a customer, the server's reason in its place.
 */
import { useEffect, useMemo, useState } from 'react'
import type { ReactNode } from 'react'
import { useQueryClient } from '@tanstack/react-query'
import { Button, ErrorBox, Field, Input, Loading, PageBody, PageHeader, Pill, Segmented, cx } from '../../components/ui'
import { dayShort } from '../../lib/format'
import { MEMBERS_KEY, membersApi, useMembersStats, usePrograms, useTargets } from '../../lib/members-api'
import type { MembersStats, TargetRow, TargetUnit } from '../../lib/types/members'
import { Tip, barPath, niceMax, useWidth } from '../money/chartKit'
import { OutcomeLine, Panel, rate, share, sourceLabel, useWriteState } from './shared'

const DAYS = 90

export function Insights() {
  const programs = usePrograms().data?.programs ?? []
  const [prog, setProg] = useState('')
  const q = useMembersStats(DAYS, prog)
  const t = useTargets(prog)
  const current = programs.find((p) => (prog ? p.slug === prog : p.is_default))
  return (
    <>
      <PageHeader title="Insights" subtitle="How the card is doing over the last 90 days, against the targets you set." saved={q.isFetching ? 'Loading…' : undefined} />
      <PageBody className="bg-canvas">
        {programs.length > 1 && (
          <Segmented
            className="mb-5"
            label="Programme"
            showLabel
            value={prog}
            onChange={setProg}
            options={programs.map((p) => ({ value: p.is_default ? '' : p.slug, label: p.name }))}
          />
        )}
        {q.isError ? (
          <ErrorBox error={q.error} what="the member figures" />
        ) : !q.data ? (
          <Loading what="Working out the figures" />
        ) : (
          <>
            <Body
              s={q.data}
              targets={t.data?.targets ?? []}
              points={current?.kind === 'POINTS'}
              programName={programs.length > 1 ? current?.name : undefined}
            />
            <div className="mt-8">
              {t.isError && <ErrorBox error={t.error} what="the targets" />}
              {t.data && <TargetsEditor key={t.data.program_slug} rows={t.data.targets} program={prog} />}
            </div>
          </>
        )}
      </PageBody>
    </>
  )
}

interface Line {
  key: string
  /** The /api/members/targets metric this line is measured against. */
  metric?: string
  label: string
  what: string
  value: ReactNode
  big?: boolean
}

function Missing({ why }: { why: string }) {
  return <span className="text-right text-sm font-normal text-ink-2">{why}</span>
}

function Body({ s, targets, points = false, programName }: { s: MembersStats; targets: TargetRow[]; points?: boolean; programName?: string }) {
  const byMetric = new Map(targets.map((t) => [t.metric, t]))
  const repeatWhy = s.repeat_rate_reason ?? 'not enough till data'
  const lines: Line[] = [
    {
      key: 'members',
      metric: 'members_total',
      label: 'Members',
      what: `${s.members_new_in_window} joined in the last ${DAYS} days.`,
      value: s.members_total,
      big: true,
    },
    {
      key: 'optin',
      metric: 'opted_in_share',
      label: 'Opted in to messages',
      what: 'Share of members who ticked the marketing box.',
      value: share(s.opted_in_share),
    },
    {
      key: 'stampshare',
      metric: 'stamp_share_of_transactions',
      label: 'Transactions with a stamp',
      what: 'Stamps given ÷ till receipts, same days.',
      value: s.stamp_share_of_transactions === null ? <Missing why="no till receipts synced" /> : share(s.stamp_share_of_transactions),
    },
    {
      key: 'redemptions',
      metric: 'redemptions_per_week',
      label: 'Redemptions a week',
      what: 'Free drinks handed over, weekly average.',
      value: rate(s.redemptions_per_week),
    },
    {
      key: 'visits',
      metric: 'visits_per_member_per_month',
      label: 'Visits per member a month',
      what: 'Days with a stamp or reward, per member.',
      value: s.visits_per_member_per_month === null ? <Missing why="not enough history yet" /> : rate(s.visits_per_member_per_month),
    },
    {
      key: 'redeemed',
      metric: 'members_with_redemption_share',
      label: 'Members with a reward redeemed',
      what: 'Have filled a card and used it at least once.',
      value: share(s.members_with_redemption_share),
    },
    {
      key: 'campaign',
      metric: 'campaign_return_rate',
      label: 'Came back after a message',
      what: 'Campaign recipients stamped within 7 days.',
      value: s.campaign_return_rate === null ? <Missing why="no campaign sent yet" /> : share(s.campaign_return_rate),
    },
    {
      key: 'repeat-m',
      metric: 'repeat_rate_members',
      label: 'Came back: members',
      what: `Till customers who are members and visited on 2+ days (${s.repeat_customers_members ?? 0} seen).`,
      value: s.repeat_rate_members == null ? <Missing why={s.repeat_rate_non_members == null ? 'see the note below' : repeatWhy} /> : share(s.repeat_rate_members),
    },
    {
      key: 'repeat-n',
      label: 'Came back: everyone else',
      what: `Till customers who are not members, same rule (${s.repeat_customers_non_members ?? 0} seen).`,
      value: s.repeat_rate_non_members == null ? <Missing why={s.repeat_rate_members == null ? 'see the note below' : repeatWhy} /> : share(s.repeat_rate_non_members),
    },
  ]
  if (points) {
    for (const l of lines) if (l.key === 'stampshare') l.label = 'Transactions with points'
  }
  return (
    <div className="flex flex-col gap-8">
      <div className="grid gap-8 compact:grid-cols-[minmax(0,1.25fr)_minmax(0,1fr)]">
        <section aria-labelledby="stmt-h" className="min-w-0">
          <h2 id="stmt-h" className="text-2xl font-extrabold tracking-[-.01em]">
            The last {DAYS} days{programName ? `: ${programName}` : ''}
          </h2>
          <p className="mb-3 mt-1 text-base text-ink-2">How the card is doing, each figure against its target where you have set one.</p>
          <div role="table" aria-label={`Members figures, last ${DAYS} days`}>
            {lines.map((l) => (
              <div
                role="row"
                key={l.key}
                className={cx(
                  'grid grid-cols-[minmax(0,1fr)_auto] items-baseline gap-x-4 py-2',
                  l.big ? 'border-b-2 border-ink' : 'border-b-[1.5px] border-dashed border-line',
                )}
              >
                <span role="rowheader" className="min-w-0">
                  <span className={cx('block', l.big ? 'text-xl font-bold' : 'text-lg')}>{l.label}</span>
                  <span className="block text-sm text-ink-2">{l.what}</span>
                </span>
                <span role="cell" className="flex flex-col items-end gap-1">
                  <span className={cx('fig text-right', l.big ? 'text-2xl font-extrabold' : 'text-xl font-bold')}>{l.value}</span>
                  {l.metric && <TargetLine t={byMetric.get(l.metric)} />}
                </span>
              </div>
            ))}
          </div>
          <p className="mt-2 text-sm text-ink-2">
            {s.repeat_rate_members == null && s.repeat_rate_non_members == null
              ? s.repeat_rate_reason ?? 'Members against non-members needs till receipts that name a customer.'
              : `Repeat visits come from Lightspeed receipts with a customer attached: ${share(s.receipts_with_customer_share ?? null)} of till receipts in the window. If staff attach customers mostly for regulars, both figures lean high.`}
            {s.repeat_rate_members_by_scans != null && ` From scans alone, ${share(s.repeat_rate_members_by_scans)} of members who got a stamp came back on another day.`}
          </p>
        </section>
        <BySource s={s} />
      </div>
      <Daily s={s} />
    </div>
  )
}

/* -------------------------------------------------------------- sources --- */

function BySource({ s }: { s: MembersStats }) {
  const rows = [...s.by_source].sort((a, b) => b.members - a.members)
  const total = rows.reduce((n, r) => n + r.members, 0)
  const max = Math.max(1, ...rows.map((r) => r.members))
  return (
    <section aria-labelledby="src-h" className="min-w-0">
      <h2 id="src-h" className="text-xl font-extrabold tracking-[-.01em]">
        Where members joined
      </h2>
      <p className="mb-3 mt-1 text-sm text-ink-2">From the QR code’s <span className="fig">?src=</span> tag, all members.</p>
      {rows.length === 0 ? (
        <p className="text-base text-ink-2">No members yet.</p>
      ) : (
        <ol>
          {rows.map((r) => (
            <li
              key={r.source ?? '—'}
              className="grid grid-cols-[minmax(0,8.5rem)_minmax(0,1fr)_2.5rem_3.5rem] items-center gap-3 border-b-[1.5px] border-dashed border-line py-1.5 text-md"
            >
              <span className={cx('truncate', r.source === null && 'text-ink-2')}>{sourceLabel(r.source)}</span>
              <span className="h-2 rounded-full bg-line-soft" aria-hidden="true">
                <span className="block h-2 rounded-full bg-brand" style={{ width: `${(r.members / max) * 100}%` }} />
              </span>
              <span className="fig text-right font-bold">{r.members}</span>
              <span className="fig text-right text-sm text-ink-2">{total ? `${Math.round((r.members / total) * 100)}%` : '—'}</span>
            </li>
          ))}
        </ol>
      )}
    </section>
  )
}

/* ---------------------------------------------------------------- daily --- */

type Grain = 'day' | 'week'
type Metric = 'new_members' | 'stamps' | 'redemptions'
const METRICS: { key: Metric; label: string }[] = [
  { key: 'new_members', label: 'New members' },
  { key: 'stamps', label: 'Stamps given' },
  { key: 'redemptions', label: 'Rewards redeemed' },
]

interface Bucket {
  key: string
  label: string
  long: string
  v: Record<Metric, number>
}

function mondayOf(iso: string): string {
  const d = new Date(`${iso}T12:00:00Z`)
  d.setUTCDate(d.getUTCDate() - ((d.getUTCDay() + 6) % 7))
  return d.toISOString().slice(0, 10)
}

function bucketize(daily: MembersStats['daily'], grain: Grain): Bucket[] {
  const m = new Map<string, Bucket>()
  for (const d of [...daily].sort((a, b) => a.date.localeCompare(b.date))) {
    const key = grain === 'day' ? d.date : mondayOf(d.date)
    let b = m.get(key)
    if (!b) {
      const at = `${key}T12:00:00Z`
      b = { key, label: dayShort(at), long: grain === 'day' ? dayShort(at) : `Week of ${dayShort(at)}`, v: { new_members: 0, stamps: 0, redemptions: 0 } }
      m.set(key, b)
    }
    b.v.new_members += d.new_members
    b.v.stamps += d.stamps
    b.v.redemptions += d.redemptions
  }
  return [...m.values()]
}

function Daily({ s }: { s: MembersStats }) {
  const [grain, setGrain] = useState<Grain>('week')
  const buckets = useMemo(() => bucketize(s.daily, grain), [s.daily, grain])
  const [hover, setHover] = useState<number | null>(null)
  const [showTable, setShowTable] = useState(false)
  return (
    <section aria-labelledby="daily-h" className="min-w-0">
      <div className="mb-3 flex flex-wrap items-center gap-x-4 gap-y-2">
        <h2 id="daily-h" className="text-xl font-extrabold tracking-[-.01em]">
          Over time
        </h2>
        <span className="flex-1" />
        <Segmented<Grain>
          label="Group by"
          value={grain}
          onChange={setGrain}
          options={[
            { value: 'day', label: 'Day' },
            { value: 'week', label: 'Week' },
          ]}
        />
      </div>
      {s.daily.length === 0 ? (
        <p className="text-base text-ink-2">Nothing in this window yet.</p>
      ) : (
        <div className="grid gap-6 compact:grid-cols-3" onMouseLeave={() => setHover(null)}>
          {METRICS.map((m) => (
            <MiniBars key={m.key} title={m.label} metric={m.key} buckets={buckets} grain={grain} hover={hover} setHover={setHover} />
          ))}
        </div>
      )}
      <button type="button" className="mt-3 text-sm text-ink-2 underline underline-offset-2 hover:text-ink" aria-expanded={showTable} onClick={() => setShowTable((v) => !v)}>
        {showTable ? 'Hide the table' : 'Show as a table'}
      </button>
      {showTable && (
        <div className="scroll-x mt-2 max-h-96 overflow-y-auto rounded-card border border-line bg-surface">
          <table className="w-full text-base">
            <caption className="sr-only">Per {grain}</caption>
            <thead>
              <tr className="text-label font-bold uppercase tracking-[.06em] text-ink-3">
                <th scope="col" className="sticky top-0 bg-surface px-3 py-2 text-left">
                  {grain === 'day' ? 'Day' : 'Week of'}
                </th>
                {METRICS.map((m) => (
                  <th key={m.key} scope="col" className="sticky top-0 bg-surface px-3 py-2 text-right">
                    {m.label}
                  </th>
                ))}
              </tr>
            </thead>
            <tbody>
              {[...buckets].reverse().map((b) => (
                <tr key={b.key} className="border-t border-line">
                  <td className="fig px-3 py-1">{b.label}</td>
                  {METRICS.map((m) => (
                    <td key={m.key} className="fig px-3 py-1 text-right">
                      {b.v[m.key]}
                    </td>
                  ))}
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </section>
  )
}

const H = 150
const PAD = { top: 8, right: 4, bottom: 22, left: 30 }

function MiniBars({
  title,
  metric,
  buckets,
  grain,
  hover,
  setHover,
}: {
  title: string
  metric: Metric
  buckets: Bucket[]
  grain: Grain
  hover: number | null
  setHover: (i: number | null) => void
}) {
  const [ref, width] = useWidth<HTMLDivElement>()
  const values = buckets.map((b) => b.v[metric])
  const sum = values.reduce((a, b) => a + b, 0)
  const peak = Math.max(0, ...values)
  // Counts: whole-number ticks, at least up to 4.
  const nm = niceMax(Math.max(4, peak))
  const step = Math.max(1, Math.round(nm.step))
  const max = Math.max(step, Math.ceil(nm.max / step) * step)
  const plotW = Math.max(0, width - PAD.left - PAD.right)
  const plotH = H - PAD.top - PAD.bottom
  const slot = buckets.length ? plotW / buckets.length : 0
  const barW = Math.max(1.5, Math.min(24, slot - 2))
  const y = (v: number) => PAD.top + plotH - (v / max) * plotH
  const ticks = Array.from({ length: Math.floor(max / step) + 1 }, (_, i) => i * step)
  const every = Math.max(1, Math.ceil(buckets.length / Math.max(1, Math.floor(plotW / 56))))
  const hb = hover !== null ? buckets[hover] : undefined
  return (
    <figure className="min-w-0">
      <figcaption className="mb-1 flex items-baseline justify-between gap-2">
        <span className="text-md font-bold">{title}</span>
        <span className="fig text-sm text-ink-2">
          <b className="text-ink">{sum}</b> in {DAYS} days
        </span>
      </figcaption>
      <div ref={ref} className="relative w-full">
        {width > 0 && (
          <svg width={width} height={H} role="group" aria-label={`${title} per ${grain}`}>
            {ticks.map((t) => (
              <g key={t}>
                <line x1={PAD.left} x2={width - PAD.right} y1={y(t)} y2={y(t)} stroke="var(--color-line)" strokeWidth={t === 0 ? 1.5 : 1} />
                <text x={PAD.left - 6} y={y(t)} dy="0.32em" textAnchor="end" className="fill-ink-3 text-label">
                  {t}
                </text>
              </g>
            ))}
            {buckets.map((b, i) => {
              const v = b.v[metric]
              const x = PAD.left + i * slot + (slot - barW) / 2
              return v > 0 ? (
                <path
                  key={b.key}
                  d={barPath(x, y(v), barW, y(0) - y(v), Math.min(4, barW / 2))}
                  fill="var(--color-brand)"
                  opacity={hover === null || hover === i ? 1 : 0.4}
                />
              ) : null
            })}
            {buckets.map((b, i) =>
              i % every === 0 ? (
                <text key={b.key} x={PAD.left + i * slot + slot / 2} y={H - 6} textAnchor="middle" className="fill-ink-3 text-label">
                  {b.label}
                </text>
              ) : null,
            )}
            {buckets.map((b, i) => (
              <rect
                key={b.key}
                x={PAD.left + i * slot}
                y={PAD.top}
                width={slot}
                height={plotH}
                fill="transparent"
                tabIndex={0}
                role="img"
                aria-label={`${b.long}: ${b.v[metric]}`}
                onMouseEnter={() => setHover(i)}
                onFocus={() => setHover(i)}
                onBlur={() => setHover(null)}
              />
            ))}
          </svg>
        )}
        {hb && hover !== null && (
          <Tip x={PAD.left + hover * slot + slot / 2} width={width}>
            <div className="font-bold text-ink">{hb.long}</div>
            <div className="flex justify-between gap-3">
              <span className="text-ink-2">{title}</span>
              <span className="fig font-bold">{hb.v[metric]}</span>
            </div>
          </Tip>
        )}
      </div>
    </figure>
  )
}

/* -------------------------------------------------------------- targets --- */

function fmtValue(v: number, unit: TargetUnit): string {
  if (unit === 'share') return `${(v * 100).toFixed(v * 100 >= 10 || Number.isInteger(v * 100) ? 0 : 1)}%`
  if (unit === 'count') return String(Math.round(v))
  return v.toFixed(1)
}

/** Under a figure: "target 150 · 12% of the way", with a thin meter. Nothing when no target. */
function TargetLine({ t }: { t: TargetRow | undefined }) {
  if (!t || t.target === null) return <span className="text-xs text-ink-3">no target</span>
  const pct = t.progress === null ? null : Math.max(0, Math.min(1, t.progress))
  return (
    <span className="flex items-center gap-2 text-xs text-ink-2">
      {t.met ? <Pill tone="ok">Met</Pill> : null}
      <span className="fig">target {fmtValue(t.target, t.unit)}</span>
      {pct !== null && !t.met && (
        <span className="flex items-center gap-1.5">
          <span className="block h-1.5 w-16 rounded-full bg-line-soft" aria-hidden="true">
            <span className="block h-1.5 rounded-full bg-brand" style={{ width: `${pct * 100}%` }} />
          </span>
          <span className="fig">{Math.round(pct * 100)}%</span>
        </span>
      )}
    </span>
  )
}

/** The text a person types for a target: shares as a percentage. */
function toText(v: number | null, unit: TargetUnit): string {
  if (v === null) return ''
  if (unit === 'share') return String(Math.round(v * 1000) / 10)
  return String(v)
}

/** Parsed input: `null` = cleared, `undefined` = not a number. */
function fromText(text: string, unit: TargetUnit): number | null | undefined {
  const t = text.trim().replace(/%$/, '').trim()
  if (t === '') return null
  if (!/^\d{1,7}(\.\d{1,2})?$/.test(t)) return undefined
  const n = Number(t)
  if (unit === 'share') return n <= 100 ? Math.round(n * 10) / 1000 : undefined
  if (unit === 'count') return Number.isInteger(n) ? n : undefined
  return n
}

const UNIT_HINT: Record<TargetUnit, string> = { count: 'members', share: '%', rate: 'a number, like 1.5' }

function TargetsEditor({ rows, program }: { rows: TargetRow[]; program: string }) {
  const qc = useQueryClient()
  const [open, setOpen] = useState(false)
  const [text, setText] = useState<Record<string, string>>({})
  const w = useWriteState()
  const reset = () => setText(Object.fromEntries(rows.map((r) => [r.metric, toText(r.target, r.unit)])))
  // A refetch after saving replaces the starting point.
  useEffect(reset, [rows])
  const parsed = rows.map((r) => ({ r, v: fromText(text[r.metric] ?? '', r.unit) }))
  const bad = parsed.filter((x) => x.v === undefined)
  const changes = Object.fromEntries(parsed.filter((x) => x.v !== undefined && x.v !== x.r.target).map((x) => [x.r.metric, x.v as number | null]))
  const n = Object.keys(changes).length
  const set = rows.filter((r) => r.target !== null).length
  return (
    <Panel
      title={`Targets for the first ${DAYS} days`}
      id="targets-h"
      right={
        !open && (
          <Button variant="outline" size="sm" onClick={() => setOpen(true)}>
            {set === 0 ? 'Set targets' : 'Change targets'}
          </Button>
        )
      }
    >
      {!open ? (
        <p className="text-base text-ink-2">
          {set === 0
            ? 'No targets yet. The spec left them open: set what success looks like, and each figure above shows how far along it is.'
            : `${set} of ${rows.length} figures have a target. Shown under each figure above.`}
        </p>
      ) : (
        <form
          onSubmit={async (e) => {
            e.preventDefault()
            if (bad.length || n === 0) return
            const r = await w.run(() => membersApi.saveTargets({ program: program || null, targets: changes }), () => `Saved ${n} ${n === 1 ? 'target' : 'targets'}.`)
            if (r) {
              await qc.invalidateQueries({ queryKey: [...MEMBERS_KEY, 'targets'] })
              setOpen(false)
            }
          }}
        >
          <p className="mb-3 text-sm text-ink-2">Leave a box empty for no target. Percentages are of members or receipts, as the figure says.</p>
          <div className="grid gap-x-5 gap-y-3 sm:grid-cols-2 wide:grid-cols-4">
            {parsed.map(({ r, v }) => (
              <Field key={r.metric} label={r.label} hint={UNIT_HINT[r.unit]} error={v === undefined ? (r.unit === 'share' ? '0 to 100.' : r.unit === 'count' ? 'A whole number.' : 'A number.') : undefined}>
                <div className="w-28">
                  <Input numeric value={text[r.metric] ?? ''} onChange={(e) => setText((t) => ({ ...t, [r.metric]: e.target.value }))} placeholder="none" />
                </div>
              </Field>
            ))}
          </div>
          <div className="mt-4 flex flex-wrap items-center gap-2">
            <Button type="submit" variant="primary" size="sm" disabled={bad.length > 0 || n === 0} pending={w.busy} pendingLabel="Saving…">
              Save targets
            </Button>
            <Button
              variant="secondary"
              size="sm"
              disabled={w.busy}
              onClick={() => {
                reset()
                setOpen(false)
              }}
            >
              Cancel
            </Button>
            <span className="text-sm text-ink-2">{n === 0 ? 'Nothing changed yet.' : `${n} to save.`}</span>
          </div>
        </form>
      )}
      <OutcomeLine outcome={w.outcome} className="mt-2" />
    </Panel>
  )
}
