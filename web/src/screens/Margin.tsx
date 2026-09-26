/**
 * Menu margin. Spec §10, build order 4: "velocity × margin, y-axis toggle to
 * margin-per-minute; the disagreement is the finding".
 *
 * The screen answers one question — what on this menu actually earns, once the
 * barista's time is counted — and it exists because the two honest answers
 * disagree. `orderings_agree: false` in the payload is not a warning; it is the
 * finding. A 89%-margin milkshake that takes 150 seconds returns 150p per staff
 * minute. A 45%-margin muffin that takes 30 returns 288p. Ranked by margin the
 * milkshake wins; ranked by the only resource that is actually scarce at 09:00
 * on a Saturday, it does not.
 *
 * Five rules the layout is built around:
 *
 *  - The loaded hourly rate is the assumption every figure inherits, so it sits
 *    in the opening row of stat cards at figure size, not in a footnote.
 *  - Items that cannot be costed or timed are shown as excluded WITH the reason.
 *    Twenty of 318 have no prep time and two have no cost at all; none of them
 *    appears as 0% (invariant 8).
 *  - Nothing here does float arithmetic. Percentages and money arrive as exact
 *    decimals — as strings, or as JSON numbers the API types as strings — and go
 *    through `lib/dec.ts` on the way to the screen. See `margin/data.ts`.
 *  - 298 rows are paged with the truncation printed, and the wide table scrolls
 *    inside itself.
 *  - There is deliberately no velocity × margin scatter. 289 of the 298 ranked
 *    items sold nothing in the window, so a scatter of them is one vertical
 *    stripe on the x axis: a chart that looks like analysis and carries none.
 *    The `Sold` column, sortable, is the honest version of the same fact.
 */
import { useMemo } from 'react'
import { useQuery } from '@tanstack/react-query'
import { api } from '../lib/api'
import { dayShort, plural, poundsOnly } from '../lib/format'
import {
  Badge,
  Card,
  Chip,
  ErrorBox,
  Loading,
  Note,
  PageHeader,
  Panel,
  SectionLabel,
  StatCard,
} from '../components/ui'
import { CostFig, Fig, Label } from '../components/prim'
import { Disagreement } from './margin/Disagreement'
import { ItemsTable } from './margin/ItemsTable'
import { Money, N, P, Pct } from './margin/figures'
import { count, derive, groupExcluded, joinRanks, type Derived } from './margin/data'
import type { MarginResponse, MenuRollup, UncostedItem } from '../lib/types'

/** A row of bordered stat cards. Never a bare `1fr`: an auto or `1fr` track has
 *  a min-content floor and one long figure pushes the whole page sideways. */
function CardRow({ children }: { children: React.ReactNode }) {
  return (
    <div className="grid grid-cols-[minmax(0,1fr)] gap-4 [&>*]:min-w-0 sm:grid-cols-2 lg:grid-cols-4">
      {children}
    </div>
  )
}

/** The unit or denominator that belongs beside a figure, set subordinate to it. */
function Unit({ children }: { children: React.ReactNode }) {
  return <span className="ml-1 text-[0.875rem] font-normal text-ink-3">{children}</span>
}

/* ------------------------------------------------------- what it assumes --- */

function Band({ full, derived }: { full: MarginResponse; derived: Derived }) {
  const notRanked = full.items_costed - full.rankable_count
  return (
    <div>
      <SectionLabel
        right={
          <Badge tone={full.orderings_agree ? 'muted' : 'warn'}>
            {full.orderings_agree ? 'the two orderings agree' : 'the two orderings disagree'}
          </Badge>
        }
      >
        The window, and what every figure below assumes
      </SectionLabel>

      <CardRow>
        <StatCard
          label="Window"
          value={
            <>
              {full.window_days}
              <Unit>days</Unit>
            </>
          }
          sub={`${dayShort(full.since)} – ${dayShort(full.until)}`}
        />
        <StatCard
          label="Loaded labour rate"
          value={
            <>
              {poundsOnly(full.loaded_hourly_rate_pence)}
              <Unit>/hr</Unit>
            </>
          }
          tone={full.rate_is_set ? 'plain' : 'bad'}
          sub={
            full.rate_is_set
              ? 'set in config — subtracted from every margin on this screen'
              : 'not set — every labour figure below is a guess'
          }
        />
        <StatCard
          label="Ranked"
          value={
            <>
              {full.rankable_count}
              <Unit>of {full.items_costed}</Unit>
            </>
          }
          sub={`${notRanked} excluded, ${full.uncosted_items.length} with no cost at all — listed below, never as zero`}
        />
        <StatCard
          label="Per staff minute"
          value={<P v={full.menu.margin_per_minute_pence} size="xl" missing="no aggregate" />}
          sub="whole menu: contribution ÷ staff minutes worked"
        />
      </CardRow>

      <div className="mt-4 grid grid-cols-[minmax(0,1fr)] gap-4 [&>*]:min-w-0 lg:grid-cols-2">
        <Panel title="What the ranking is, and is not">
          Every money figure in the table is <strong className="text-ink">per unit</strong>, so the
          ranking is per-unit economics rather than volume:{' '}
          <span className="fig">{derived.unsoldCount}</span> of{' '}
          <span className="fig">{full.rankable_count}</span> ranked items sold nothing at all, and{' '}
          <span className="fig">{derived.soldCount}</span> carried the whole window. Sort by{' '}
          <em>Sold</em> to read it the other way round — and that lopsidedness is why there is no
          velocity scatter here: it would be one vertical stripe.
        </Panel>
        <Panel title="What these figures inherit">
          All <span className="fig">{derived.prepEstimateCount}</span> prep times are estimates,
          and <span className="fig">{full.estimated_cost_item_count}</span> of{' '}
          <span className="fig">{full.rankable_count}</span> costs come from estimated ingredient
          prices (<span className="fig">{full.invoice_cost_item_count}</span> from invoices).
          Margin per minute is only as good as the prep estimates; an estimated cost is marked
          with a dotted rule wherever it appears.
        </Panel>
      </div>

      {derived.negativeAfterLabour.length > 0 && (
        <div className="mt-4">
          <Panel
            tone="bad"
            title={`${derived.negativeAfterLabour.length} ${plural(
              derived.negativeAfterLabour.length,
              'item',
            )} ${derived.negativeAfterLabour.length === 1 ? 'loses' : 'lose'} money once labour is counted`}
          >
            <ul className="grid gap-1">
              {derived.negativeAfterLabour.map((it) => (
                <li
                  key={it.menu_item_id}
                  className="flex flex-wrap items-baseline gap-x-3 gap-y-0.5"
                >
                  <span className="font-medium text-ink">{it.label}</span>
                  <span>
                    <Label>margin </Label>
                    <Pct v={it.margin_pct} size="sm" />
                  </span>
                  <span>
                    <Label>after labour </Label>
                    <Pct v={it.true_margin_pct} size="sm" tone="bad" />
                  </span>
                  <span>
                    <Label>price </Label>
                    <Money v={it.price_pence} size="sm" />
                  </span>
                  <span>
                    <Label>prep </Label>
                    <Fig size="sm">{it.prep_seconds ?? '—'} s</Fig>
                  </span>
                </li>
              ))}
            </ul>
            <p className="mt-2 text-bad-ink">
              Priced below the cost of the time it takes. Either the price or the prep time is
              wrong — both are worth checking before the menu is reprinted.
            </p>
          </Panel>
        </div>
      )}
    </div>
  )
}

/* --------------------------------------------------------- whole menu ------ */

function Whole({ agg, windowDays }: { agg: MenuRollup; windowDays: number }) {
  return (
    <div>
      <SectionLabel
        right={
          agg.is_complete ? (
            <Badge tone="ok">all items included</Badge>
          ) : (
            <Badge tone="warn">
              covers {agg.items_included} of {agg.items_total} items
            </Badge>
          )
        }
      >
        The whole menu, once — window totals over {windowDays} days, not per unit
      </SectionLabel>

      <CardRow>
        <StatCard
          label="Revenue"
          value={<Money v={agg.revenue_pence_total} size="xl" />}
          sub={`${count(agg.units_total) ?? '—'} units sold`}
        />
        <StatCard
          label="Ingredient cost"
          value={<Money v={agg.ingredient_cost_pence_total} size="xl" />}
          sub="estimated prices included, flagged per item"
        />
        <StatCard
          label="Labour"
          value={<Money v={agg.labour_cost_pence_total} size="xl" />}
          sub="prep time at the loaded hourly rate"
        />
        <StatCard
          label="Labour share of revenue"
          value={<Pct v={agg.labour_share_of_revenue_pct} size="xl" />}
          sub="prep time only — not the rota"
        />
      </CardRow>

      <div className="mt-4">
        <CardRow>
          <StatCard
            label="Contribution"
            value={<Money v={agg.contribution_pence_total} size="xl" />}
            sub="revenue less ingredient cost"
          />
          <StatCard
            label="After labour"
            value={<Money v={agg.true_margin_pence_total} size="xl" />}
            sub="contribution less prep labour"
          />
          <StatCard
            label="Per staff minute"
            value={<P v={agg.margin_per_minute_pence} size="xl" />}
            sub="contribution ÷ minutes of prep"
          />
          <StatCard
            label="Staff time"
            value={<N v={agg.staff_hours} unit="hours" dp={1} size="xl" />}
            sub="prep only — summed over every unit sold, not the rota"
          />
        </CardRow>
      </div>

      <div className="mt-4 grid grid-cols-[minmax(0,1fr)] gap-4 [&>*]:min-w-0 lg:grid-cols-2">
        <Panel title="As the backend states it">{agg.summary}</Panel>
        {!agg.is_complete && (
          <Panel tone="warn" title={`Covers ${agg.items_included} of ${agg.items_total} items`}>
            These totals cover the {agg.items_included} items that have both a cost and a prep
            time. The other {agg.items_total - agg.items_included} are left out rather than
            counted as zero, so revenue and contribution here are both understated by whatever
            those items did.
          </Panel>
        )}
      </div>
    </div>
  )
}

/* ------------------------------------------------------- not ranked -------- */

function Excluded({
  excluded,
  uncosted,
  itemsTotal,
}: {
  excluded: string[][]
  uncosted: UncostedItem[]
  itemsTotal: number
}) {
  const groups = useMemo(() => groupExcluded(excluded), [excluded])
  if (excluded.length + uncosted.length === 0) return null

  const parts: string[] = []
  if (excluded.length > 0) {
    parts.push(`${excluded.length} of ${itemsTotal} costed items are in neither ordering`)
  }
  if (uncosted.length > 0) {
    parts.push(
      `${uncosted.length} ${plural(uncosted.length, 'item has', 'items have')} no cost at all`,
    )
  }

  return (
    <Card
      title="Not ranked, and why"
      subtitle={`${parts.join(', and ')}. They are listed here rather than sorted to the bottom as zeroes, and they are in none of the totals on this screen.`}
    >
      <div className="grid grid-cols-[minmax(0,1fr)] gap-6 [&>*]:min-w-0 lg:grid-cols-2">
        {excluded.length > 0 && (
          <div className="min-w-0">
            <SectionLabel>Excluded from the ranking</SectionLabel>
            <Note>
              An item with no prep time has no margin per minute, and a margin per minute of zero
              would be a lie rather than a gap.
            </Note>
            {groups.map((g) => (
              <div key={g.reason} className="mt-3">
                <div className="flex flex-wrap items-baseline gap-2">
                  <Badge tone="warn">{g.reason}</Badge>
                  <Label>
                    {g.labels.length} {plural(g.labels.length, 'item')}
                  </Label>
                </div>
                <div className="mt-2 flex flex-wrap gap-1">
                  {g.labels.map((label) => (
                    <Chip key={label}>{label}</Chip>
                  ))}
                </div>
              </div>
            ))}
          </div>
        )}

        {uncosted.length > 0 && (
          <div className="min-w-0">
            <SectionLabel>No cost at all</SectionLabel>
            <Note>
              These have no row in <code className="fig">menu_item_cost</code>: the cost is
              unknown, not zero, so there is no margin to show and they are in no total on this
              screen (invariant 8).
            </Note>
            <ul className="mt-3 grid gap-3">
              {uncosted.map((u) => (
                <li key={u.menu_item_id}>
                  <Panel
                    title={
                      <>
                        {u.name} <span className="font-normal text-ink-3">{u.size_code}</span>
                      </>
                    }
                    right={
                      <span className="flex items-baseline gap-3">
                        <span>
                          <Label>price </Label>
                          <Money v={u.price_pence} size="sm" />
                        </span>
                        <span>
                          <Label>cost </Label>
                          <CostFig cost={u.cost} size="sm" />
                        </span>
                      </span>
                    }
                  >
                    {u.reason}
                    {!u.active && (
                      <div className="mt-2">
                        <Badge tone="muted">not on sale</Badge>
                      </div>
                    )}
                  </Panel>
                </li>
              ))}
            </ul>
          </div>
        )}
      </div>
    </Card>
  )
}

/* ------------------------------------------------------------ assumptions -- */

function Warnings({ warnings }: { warnings: string[] }) {
  if (warnings.length === 0) return null
  return (
    <Card
      title="What the API says about its own figures"
      subtitle={`${warnings.length} ${plural(warnings.length, 'caveat')}, in the API's own words.`}
    >
      <ul className="grid gap-2">
        {warnings.map((w) => (
          <li key={w} className="flex gap-2 text-[0.875rem] leading-[16px] text-ink-2">
            <span aria-hidden className="text-ink-5">
              —
            </span>
            <span className="min-w-0">{w}</span>
          </li>
        ))}
      </ul>
    </Card>
  )
}

/* ------------------------------------------------------------------ screen - */

export function Margin() {
  const q = useQuery({ queryKey: ['margin'], queryFn: () => api.margin() })

  const model = useMemo(() => {
    if (!q.data) return null
    const full = q.data
    const byPct = joinRanks(full.by_margin_pct, full.ranked)
    const byMin = joinRanks(full.by_margin_per_minute, full.ranked)
    return { full, byPct, byMin, derived: derive(full, byPct) }
  }, [q.data])

  return (
    <>
      <PageHeader
        title="Menu margin"
        lede="What actually earns, once the barista's time is counted. Margin % and margin per staff minute rank this menu differently — that disagreement is the point of the screen."
      />

      {q.isPending && <Loading what="Loading margins" />}
      {q.error && <ErrorBox error={q.error} />}

      {model && (
        <div className="grid grid-cols-[minmax(0,1fr)] gap-6 [&>*]:min-w-0">
          <Band full={model.full} derived={model.derived} />

          <Disagreement
            derived={model.derived}
            agree={model.full.orderings_agree}
            total={model.full.rankable_count}
          />

          <ItemsTable
            byMarginPct={model.byPct}
            byPerMinute={model.byMin}
            rankableCount={model.full.rankable_count}
            windowDays={model.full.window_days}
          />

          {model.full.menu && (
            <Whole agg={model.full.menu} windowDays={model.full.window_days} />
          )}

          <Excluded
            excluded={model.full.excluded}
            uncosted={model.full.uncosted_items}
            itemsTotal={model.full.items_costed}
          />

          <Warnings warnings={model.full.warnings} />
        </div>
      )}
    </>
  )
}
