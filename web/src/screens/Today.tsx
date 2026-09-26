/**
 * Today. The landing screen, and the only one with a single question to answer:
 * *is today normal, and if not, what do I do first?*
 *
 * Three things shape it.
 *
 *  1. The API already knows the answer. `/api/today` returns `alerts[]` — each
 *     with a severity the backend has actually classified and a sentence saying
 *     what to do — so this screen ORDERS and NAMES those alerts rather than
 *     re-deriving urgency from raw counts. Colour follows the API's severity,
 *     never this file's opinion of it.
 *  2. An absent problem is a line, not a tile. `unanchored`, `negative`,
 *     `unbatched` and `short_dated` are all 0 in the present data; four cards
 *     reading "0" is how the one card that matters stops being read.
 *  3. The draft-order figures are `null` until an ordering run has happened, and
 *     null is *not computed*, not zero. The null state says so and offers the
 *     run; the computed state shows the money. They look nothing alike.
 *
 * Every figure goes through `<Fig>`; money is formatted by `lib/format` from
 * integer pence or an exact decimal string, and nothing here does arithmetic on
 * a float (`isZero(parseDec(...))` is the only test applied to a money string).
 */
import { useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import { api, type TodayAlert, type TodaySummary } from '../lib/api'
import { isZero, parseDec } from '../lib/dec'
import { dayFull, money, plural, stamp } from '../lib/format'
import type { OrdersDraftResponse } from '../lib/types'
import {
  Badge,
  Button,
  Card,
  Chip,
  DeltaPill,
  ErrorBox,
  Fig,
  Loading,
  Note,
  PageHeader,
  Panel,
  ScrollX,
  SectionLabel,
  Stat,
  StatCard,
  StatRow,
  toneText,
  type Tone,
} from '../components/ui'
import { Label } from '../components/prim'

/* ------------------------------------------------------------------ types --
 * `TodaySummary` and `TodayAlert` are the shared types; nothing about the
 * payload is redeclared here. The one loose end left is `short_dated`, still
 * typed `unknown[]` while the endpoint sends "Whole milk (3d)" strings, so it
 * goes through `stringList` rather than being asserted.
 */

function stringList(v: unknown): string[] {
  return Array.isArray(v) ? v.filter((x): x is string => typeof x === 'string') : []
}

/* ------------------------------------------------------------- severity --- */

type Severity = TodayAlert['severity']

const SEVERITY_TONE: Record<Severity, Tone> = { act: 'bad', watch: 'warn', info: 'info' }
const SEVERITY_RANK: Record<Severity, number> = { act: 0, watch: 1, info: 2 }

function tone(severity: Severity): Tone {
  return SEVERITY_TONE[severity]
}

function rank(severity: Severity): number {
  return SEVERITY_RANK[severity]
}

/**
 * The imperative for an alert. The API's own `message` explains and is printed
 * verbatim below; this is only the line you read in the first second, so it is
 * a verb. An unrecognised kind still gets a heading rather than being dropped.
 */
function heading(a: TodayAlert): string {
  switch (`${a.kind}:${a.severity}`) {
    case 'drift:act':
      return 'Count these — drift is over 15%'
    case 'drift:watch':
      return 'A waste factor change is proposed'
    case 'expiry:act':
      return a.subject
        ? 'Sell these through before they date'
        : 'Write off what has already expired'
    case 'stock:act':
      return 'On-hand has gone negative'
    case 'ordering:watch':
      return 'Some items are routed to Tesco at retail'
    case 'ordering:info':
      return 'Some order lines were capped'
    case 'menu:info':
      return 'Take these off today’s menu'
    default:
      return `${a.kind.replace(/_/g, ' ')} — needs a look`
  }
}

/**
 * The names behind an alert, taken from the typed arrays wherever one exists.
 * `subject` is a comma-joined string, and splitting it would invent a boundary
 * inside any name that contains a comma; so it is only split as a last resort,
 * and then not at all — it stays one chip.
 */
function subjectsFor(a: TodayAlert, t: TodaySummary): string[] {
  if (a.kind === 'drift' && a.severity === 'act') return [...t.drift_forced_manual]
  if (a.kind === 'drift') return [...t.drift_tuning_band]
  if (a.kind === 'menu') return [...t.unavailable_menu_items]
  if (a.kind === 'expiry' && a.subject !== null) return stringList(t.short_dated)
  return a.subject === null ? [] : [a.subject]
}

/* ---------------------------------------------------------------- money --- */

/** True when a money string is present and not exactly zero. No float, no `>`. */
function hasValue(v: string | null | undefined): v is string {
  if (v === null || v === undefined) return false
  const d = parseDec(v)
  return d !== null && !isZero(d)
}

/* ------------------------------------------------------------------ notes --
 * The API's `notes` are prose, and three groups of them carry very different
 * weight. Classifying by content is crude, but the fallback is to print the
 * note under "Other notes" — nothing is ever silently dropped.
 */

interface NoteGroups {
  stockouts: string[]
  sourcing: string[]
  placeholders: string[]
  theoretical: string[]
  notComputed: string[]
  other: string[]
}

/**
 * Seven stockout notes end in the same sentence, word for word. Printing it
 * seven times buries the seven ingredient names, which are the part that
 * differs. So the shared tail is lifted out — but ONLY when every tail is
 * byte-identical, so nothing is ever summarised away or paraphrased.
 */
function foldShared(notes: string[]): { heads: string[]; shared: string | null } {
  if (notes.length < 2) return { heads: notes, shared: null }
  const split = notes.map((n) => {
    const i = n.indexOf('. ')
    return i === -1 ? null : { head: n.slice(0, i + 1), tail: n.slice(i + 2) }
  })
  const first = split[0]
  if (first === null || first === undefined) return { heads: notes, shared: null }
  for (const s of split) {
    if (s === null || s === undefined || s.tail !== first.tail) return { heads: notes, shared: null }
  }
  return { heads: split.map((s) => (s as { head: string }).head), shared: first.tail }
}

function groupNotes(notes: string[]): NoteGroups {
  const g: NoteGroups = {
    stockouts: [],
    sourcing: [],
    placeholders: [],
    theoretical: [],
    notComputed: [],
    other: [],
  }
  for (const n of notes) {
    if (/runs out in/.test(n)) g.stockouts.push(n)
    else if (n.startsWith('SOURCING:')) g.sourcing.push(n)
    else if (/INVENTED PLACEHOLDERS/.test(n)) g.placeholders.push(n)
    else if (/THEORETICAL/.test(n)) g.theoretical.push(n)
    else if (/with_orders/.test(n)) g.notComputed.push(n)
    else g.other.push(n)
  }
  return g
}

/* ----------------------------------------------------------------- draft --- */

/**
 * What an ordering run tells this screen. It arrives one of two ways: already on
 * `/api/today` when the day's run has happened, or from `/api/orders/draft` when
 * the reader asks for it here. Both are reads — `writes_nothing` is the draft's
 * own word for it, and nothing is ordered until Telegram (invariant 1).
 */
interface Draft {
  total: string | number
  suppliers: number | null
  capped: number | null
  emergency: number | null
  placeholders: string[]
  notes: NoteGroups
  computedAt: string | null
}

function draftFromToday(t: TodaySummary): Draft | null {
  if (t.draft_order_total_pence === null) return null
  return {
    total: t.draft_order_total_pence,
    suppliers: t.draft_order_supplier_count,
    capped: t.capped_line_count,
    emergency: t.emergency_line_count,
    placeholders: [],
    notes: groupNotes(t.notes),
    computedAt: t.as_of,
  }
}

function draftFromOrders(d: OrdersDraftResponse): Draft {
  return {
    total: d.total_pence,
    suppliers: d.suppliers.filter((s) => s.lines.length > 0).length,
    capped: d.capped_line_count,
    emergency: d.emergency.length,
    placeholders: d.placeholder_supplier_names,
    notes: groupNotes(d.notes),
    computedAt: d.computed_at,
  }
}

/* ----------------------------------------------------------------- bits --- */

/** The names behind an alert, as a set of chips. `<Chip>` rather than `<Badge>`:
 *  a badge is a status the backend classified, a chip is a name out of the data,
 *  and the two should not look the same. */
function Chips({ items, label }: { items: string[]; label: string }) {
  if (items.length === 0) return null
  return (
    <ul aria-label={label} className="flex flex-wrap gap-1 mt-2.5">
      {items.map((s) => (
        <li key={s}>
          <Chip>{s}</Chip>
        </li>
      ))}
    </ul>
  )
}

/** A coloured dot, so severity is not carried by hue alone. */
function Dot({ t }: { t: Tone }) {
  return (
    <span aria-hidden className={`${toneText(t)} leading-none`}>
      ●
    </span>
  )
}

function AlertCard({ a, t }: { a: TodayAlert; t: TodaySummary }) {
  const subjects = subjectsFor(a, t)
  const tn = tone(a.severity)
  return (
    <Card
      title={
        <span className="flex items-center gap-2">
          <Dot t={tn} />
          {heading(a)}
        </span>
      }
      right={<Badge tone={tn}>{a.severity}</Badge>}
    >
      <Chips items={subjects} label={`${a.kind} subjects`} />
      <Note>
        <span className={subjects.length > 0 ? 'block mt-2.5' : undefined}>{a.message}</span>
      </Note>
    </Card>
  )
}

/** One line for each kind of nothing. Four zeros do not deserve four tiles. */
function IntegrityLine({ t }: { t: TodaySummary }) {
  const s = t.stock
  const all: { n: number; label: string; tone: Tone }[] = [
    { n: s.unanchored, label: 'with no count behind them', tone: 'warn' },
    { n: s.negative, label: 'negative on hand', tone: 'bad' },
    { n: s.unbatched, label: 'holding stock outside any batch', tone: 'warn' },
    { n: s.short_dated, label: 'short-dated', tone: 'warn' },
  ]
  const flags = all.filter((f) => f.n > 0)

  if (flags.length === 0) {
    return (
      <Note>
        All <Fig size="sm">{s.ingredients}</Fig> tracked ingredients are anchored to a count,
        none is negative, none holds stock outside a batch and none is short-dated.
      </Note>
    )
  }
  return (
    <ul className="space-y-1">
      {flags.map((f) => (
        <li key={f.label} className="flex items-baseline gap-2">
          <Fig size="sm" tone={f.tone}>
            {f.n}
          </Fig>
          <Note tone={f.tone}>{f.label}</Note>
        </li>
      ))}
    </ul>
  )
}

function StockoutList({ notes }: { notes: string[] }) {
  const { heads, shared } = foldShared(notes)
  return (
    <>
      <ul className="mt-1.5 space-y-1">
        {heads.map((n) => (
          <li key={n}>
            <Note tone="plain">{n}</Note>
          </li>
        ))}
      </ul>
      {shared !== null && (
        <Note>
          <span className="block mt-1.5">{shared}</span>
        </Note>
      )}
    </>
  )
}

function DraftCard({
  draft,
  state,
  onCompute,
  error,
}: {
  draft: Draft | null
  state: 'idle' | 'loading' | 'error'
  onCompute: () => void
  error: unknown
}) {
  if (draft === null) {
    return (
      <Card title="Draft order" subtitle="Not computed for today">
        {state === 'loading' ? (
          <Loading what="Forecasting every tracked ingredient across eight suppliers" />
        ) : (
          <>
            <Fig missing="not computed yet" />
            {/* The API's own note here says "pass with_orders=true", which is an
                instruction to the caller — and this screen is the caller. The
                button below is that note, rendered. */}
            <Note>
              No ordering run has been made for today. A run forecasts every tracked
              ingredient across eight suppliers, which is seconds rather than milliseconds,
              so it does not happen on page load.
            </Note>
            {state === 'error' && (
              <div className="mt-3">
                <ErrorBox error={error} />
              </div>
            )}
            <div className="mt-4">
              <Button variant="primary" onClick={onCompute}>
                Compute the draft
              </Button>
            </div>
            <Note>
              <span className="block mt-2">
                It writes nothing. Confirmation happens in Telegram, one tap, by a person
                (invariant 1).
              </span>
            </Note>
          </>
        )}
      </Card>
    )
  }

  return (
    <Card
      title="Draft order"
      subtitle={draft.computedAt ? `computed ${stamp(draft.computedAt)}` : undefined}
      right={<Badge tone="ok">writes nothing</Badge>}
    >
      {/* Capped and emergency are kept apart. Summing them would need a null to
          stand in as 0, and the two mean opposite things anyway: a cap is the
          system working, a Tesco run is the cadence failing. */}
      <div className="grid gap-4 grid-cols-2 sm:grid-cols-3">
        <Stat
          label="Total"
          value={<Fig size="xl">{money(draft.total)}</Fig>}
          sub={
            draft.suppliers === null ? (
              <Fig missing="supplier split not reported" />
            ) : (
              <>
                across <Fig size="sm">{draft.suppliers}</Fig>{' '}
                {plural(draft.suppliers, 'supplier')}
              </>
            )
          }
        />
        <Stat
          label="Capped"
          value={
            draft.capped === null ? (
              <Fig missing="not reported" />
            ) : (
              <Fig size="xl">{draft.capped}</Fig>
            )
          }
          sub="by shelf life or season"
        />
        <Stat
          label="To Tesco"
          value={
            draft.emergency === null ? (
              <Fig missing="not reported" />
            ) : (
              <Fig size="xl" tone={draft.emergency > 0 ? 'warn' : 'plain'}>
                {draft.emergency}
              </Fig>
            )
          }
          sub="cannot wait for a delivery"
        />
      </div>

      {draft.placeholders.length > 0 && (
        <div className="mt-4">
          <Panel tone="warn" title="Built on supplier terms nobody has checked">
            <Fig size="sm" tone="warn">
              {draft.placeholders.length}
            </Fig>{' '}
            of <Fig size="sm">8</Fig> suppliers&rsquo; terms were invented, and every cover
            window above is built on them. Only Tesco and Amazon have terms anybody has
            checked.
            <Chips items={draft.placeholders} label="suppliers with placeholder terms" />
          </Panel>
        </div>
      )}

      {draft.notes.stockouts.length > 0 && (
        /* One panel rather than a coloured dot per line: the state is "these run
           out before anything can arrive", and it is one state. */
        <div className="mt-4">
          <Panel
            tone="bad"
            title={
              <>
                <Fig size="sm" tone="bad">
                  {draft.notes.stockouts.length}
                </Fig>{' '}
                {plural(draft.notes.stockouts.length, 'gap', 'gaps')} the next delivery
                cannot bridge
              </>
            }
          >
            <StockoutList notes={draft.notes.stockouts} />
          </Panel>
        </div>
      )}

      {draft.notes.sourcing.length > 0 && (
        <div className="mt-4 space-y-1.5">
          {draft.notes.sourcing.map((n) => (
            <Note key={n}>{n}</Note>
          ))}
        </div>
      )}

      <Note>
        <span className="block mt-4">
          The lines themselves, their cap reasons and everything deliberately skipped are on
          Orders.
        </span>
      </Note>
    </Card>
  )
}

/* ---------------------------------------------------------------- screen --- */

export function Today() {
  const today = useQuery({ queryKey: ['today'], queryFn: () => api.today() })
  const health = useQuery({ queryKey: ['health'], queryFn: () => api.health() })
  const [askedForDraft, setAskedForDraft] = useState(false)
  const orders = useQuery({
    queryKey: ['orders', 'draft'],
    queryFn: () => api.ordersDraft(),
    enabled: askedForDraft,
  })

  if (today.isPending) {
    return (
      <>
        <PageHeader title="Today" />
        <Loading what="Reading today" />
      </>
    )
  }
  if (today.isError || !today.data) {
    return (
      <>
        <PageHeader title="Today" />
        <ErrorBox error={today.error} />
      </>
    )
  }

  const t = today.data
  const alerts = [...t.alerts].sort((a, b) => rank(a.severity) - rank(b.severity))
  const standing = t.data_quality
  const notes = groupNotes(t.notes)

  const acting = alerts.filter((a) => a.severity === 'act')
  const watching = alerts.filter((a) => a.severity === 'watch')

  const draft =
    draftFromToday(t) ?? (orders.data !== undefined ? draftFromOrders(orders.data) : null)
  const draftState: 'idle' | 'loading' | 'error' = orders.isError
    ? 'error'
    : askedForDraft && orders.isPending
      ? 'loading'
      : 'idle'

  const writeOffValue = t.expiry_write_offs_value_pence
  /* Nullable for the same reason the write-off total is: an unpriced batch makes
     the sum unknowable, which is not zero. The shared type now says so. */
  const expiringValue = t.stock.expiring_value_pence

  /* A stockout the next delivery cannot bridge is not in `alerts` — it arrives
     as an ordering note — so the verdict counts it explicitly rather than
     saying "1 thing to act on" above a list of seven. */
  const gaps = draft?.notes.stockouts.length ?? 0
  const verdictTone: Tone =
    acting.length > 0 || gaps > 0 ? 'bad' : watching.length > 0 ? 'warn' : 'ok'
  const verdict =
    (acting.length > 0
      ? `${acting.length} ${plural(acting.length, 'thing')} to act on today.`
      : watching.length > 0
        ? `Nothing needs acting on. ${watching.length} ${plural(watching.length, 'thing')} to keep an eye on.`
        : 'Today is normal.') +
    (gaps > 0
      ? ` ${gaps} ${plural(gaps, 'ingredient')} will run out before anything can arrive.`
      : '')
  const first = acting[0] ?? watching[0] ?? alerts[0] ?? null

  const weekday = new Intl.DateTimeFormat('en-GB', {
    weekday: 'long',
    timeZone: 'UTC',
  }).format(new Date(t.local_date))

  return (
    <>
      <PageHeader
        title="Today"
        lede={
          <>
            {weekday} {dayFull(t.local_date)}. Every stock figure here is theoretical; a
            physical count is the only source of truth.
          </>
        }
        right={<Label>read {stamp(t.as_of)}</Label>}
      />

      {/* The verdict, and the one thing to do first. */}
      <Card className="mb-5">
        <div className="flex flex-wrap items-baseline gap-x-3 gap-y-1">
          <Dot t={verdictTone} />
          <p className={`text-[1rem] font-semibold ${toneText(verdictTone)}`}>{verdict}</p>
        </div>
        {first !== null && (
          <p className="text-ink-2 mt-1.5">
            <span className="text-ink-3">First: </span>
            {heading(first)}
            {subjectsFor(first, t).length > 0 && (
              <span className="text-ink">: {subjectsFor(first, t).join(', ')}</span>
            )}
            .
          </p>
        )}
      </Card>

      {/* The few figures worth a glance before the lists.
          Four separate bordered cards rather than one four-column panel: a row
          of cards is the reference's opening move, and a single panel divided
          into columns reads as a table somebody abandoned (DESIGN-LAW pattern
          1). No sparkline on any of them -- `/api/today` carries no series at
          all, and a shape drawn through one point would be decoration. Where a
          card carries a `<DeltaPill>` it is a second figure the backend has
          itself classified, never a tint for its own sake. */}
      <div className="mb-5">
        <StatRow>
          <StatCard
            label="Expired, not written off"
            /* Zero here is the good state, and it is quiet: muted ink, so the
               eye moves past it to whatever is not zero. */
            tone={hasValue(writeOffValue) ? 'bad' : 'muted'}
            value={
              writeOffValue === null ? (
                <Fig missing="value unknown — a batch is unpriced" />
              ) : (
                money(writeOffValue)
              )
            }
            delta={
              t.expiry_write_offs_due > 0 ? (
                <DeltaPill
                  tone="bad"
                  value={`${t.expiry_write_offs_due} ${plural(
                    t.expiry_write_offs_due,
                    'batch',
                    'batches',
                  )}`}
                />
              ) : undefined
            }
            sub={
              t.expiry_write_offs_due > 0
                ? 'past expiry with stock left'
                : 'Nothing has expired unsold.'
            }
          />
          <StatCard
            label="Cannot be made"
            tone={t.unavailable_menu_items.length > 0 ? 'warn' : 'plain'}
            value={t.unavailable_menu_items.length}
            sub={
              t.unavailable_menu_items.length > 0
                ? 'menu items to pull for today'
                : 'the whole menu is makeable'
            }
          />
          <StatCard
            label="Drift over 15%"
            tone={t.drift_forced_manual.length > 0 ? 'bad' : 'plain'}
            value={t.drift_forced_manual.length}
            delta={
              t.drift_tuning_band.length > 0 ? (
                <DeltaPill tone="warn" value={`+${t.drift_tuning_band.length} in band`} />
              ) : undefined
            }
            sub="auto-order forced off; the band is 10–15% drift"
          />
          <StatCard
            label="Draft order"
            value={draft === null ? <Fig missing="not computed yet" /> : money(draft.total)}
            delta={
              draft !== null && draft.suppliers !== null ? (
                <DeltaPill
                  tone="plain"
                  value={`${draft.suppliers} ${plural(draft.suppliers, 'supplier')}`}
                />
              ) : undefined
            }
            sub={
              draft === null ? (
                'No ordering run today.'
              ) : draft.suppliers === null ? (
                <Fig missing="supplier split not reported" />
              ) : (
                'nothing ordered yet'
              )
            }
          />
        </StatRow>
      </div>

      <div className="grid gap-4 lg:grid-cols-[minmax(0,1.5fr)_minmax(0,1fr)] items-start">
        {/* Attention, in the API's own order of severity. */}
        <div className="space-y-5 min-w-0">
          {/* Sentence case. Tracked-out capitals are banned outright: they cost
              legibility at 11px and are the tell of the rejected build. */}
          <SectionLabel
            right={
              alerts.length > 0
                ? `${alerts.length} ${plural(alerts.length, 'flag')}, most urgent first`
                : undefined
            }
          >
            Needs attention
          </SectionLabel>

          {alerts.length === 0 ? (
            <Note>Nothing is flagged for today. No drift, no expiry, no menu gap.</Note>
          ) : (
            alerts.map((a, i) => <AlertCard key={`${a.kind}:${a.severity}:${i}`} a={a} t={t} />)
          )}

          {hasValue(expiringValue) && (
            <Panel tone="warn">
              <Fig size="sm" tone="warn">
                {money(expiringValue)}
              </Fig>{' '}
              of stock is inside its short-dated window — still sellable, and this is the
              window in which it can be sold rather than binned.
            </Panel>
          )}
        </div>

        <div className="space-y-5 min-w-0">
          <SectionLabel>Ordering</SectionLabel>
          <DraftCard
            draft={draft}
            state={draftState}
            error={orders.error}
            onCompute={() => setAskedForDraft(true)}
          />

          <Card title="Standing conditions" subtitle="True every day, not news today">
            <div className="space-y-3">
              <div>
                <Label>stock integrity</Label>
                <div className="mt-1">
                  <IntegrityLine t={t} />
                </div>
              </div>
              <div>
                <Label>auto-ordering</Label>
                <Note>
                  <Fig size="sm">{t.auto_order_enabled_count}</Fig> of{' '}
                  <Fig size="sm">{t.stock.ingredients}</Fig> tracked ingredients have earned
                  it through two counts under 10% drift.{' '}
                  {t.stock.forced_manual > 0 && (
                    <>
                      <Fig size="sm" tone="bad">
                        {t.stock.forced_manual}
                      </Fig>{' '}
                      {plural(t.stock.forced_manual, 'is', 'are')} held manual.
                    </>
                  )}
                </Note>
              </div>
              {standing.map((a, i) => (
                <div key={`${a.kind}:${i}`}>
                  <Label>{a.kind.replace(/_/g, ' ')}</Label>
                  <Note>{a.message}</Note>
                </div>
              ))}
              {notes.theoretical.map((n) => (
                <Note key={n}>{n}</Note>
              ))}
            </div>
          </Card>

          {notes.other.length > 0 && (
            <Card title="Other notes">
              <div className="space-y-2">
                {notes.other.map((n) => (
                  <Note key={n}>{n}</Note>
                ))}
              </div>
            </Card>
          )}

          <Card title="System">
            {health.data ? (
              <ScrollX>
                <div className="flex flex-wrap gap-x-5 gap-y-2 text-[0.75rem] text-ink-3">
                  <span>
                    <Label>ingredients</Label>{' '}
                    {health.data.ingredients === null ? (
                      <Fig missing="not disclosed" />
                    ) : (
                      <Fig size="sm">{health.data.ingredients}</Fig>
                    )}
                  </span>
                  <span>
                    <Label>menu items</Label> {health.data.menu_items === null ? (
                      <Fig missing="not disclosed" />
                    ) : (
                      <Fig size="sm">{health.data.menu_items}</Fig>
                    )}
                  </span>
                  <span>
                    <Label>templates</Label> {health.data.templates === null ? (
                      <Fig missing="not disclosed" />
                    ) : (
                      <Fig size="sm">{health.data.templates}</Fig>
                    )}
                  </span>
                  <span>
                    <Label>movements</Label>{' '}
                    {health.data.movements === null ? (
                      <Fig missing="not disclosed" />
                    ) : (
                      <Fig size="sm">{health.data.movements.toLocaleString('en-GB')}</Fig>
                    )}
                  </span>
                  <span>
                    <Label>store</Label> {health.data.database_dialect}
                  </span>
                  <span>
                    <Label>status</Label>{' '}
                    <span className={toneText(health.data.status === 'ok' ? 'ok' : 'warn')}>
                      {health.data.status}
                    </span>
                  </span>
                </div>
              </ScrollX>
            ) : (
              <Note>
                {health.isError
                  ? 'The health endpoint did not answer, so the figures behind this screen are unverified.'
                  : 'Checking…'}
              </Note>
            )}
            <Note>
              <span className="block mt-2">
                <Fig size="sm">{t.stock.ingredients}</Fig> of those ingredients are
                stock-tracked; the rest carry a recipe and a cost but no on-hand figure.
              </span>
            </Note>
          </Card>
        </div>
      </div>
    </>
  )
}
