/**
 * Orders. Spec §10, build order item three.
 *
 * The screen answers three questions in that order: what is about to be bought,
 * why that quantity, and what was deliberately NOT bought. The third is the one
 * no spreadsheet gives you and it is the reason `skipped[]` is on the page at
 * full size rather than behind a disclosure.
 *
 * Nothing here writes. `GET /api/orders/draft` computes the run on read
 * (`writes_nothing: true`) and the only thing that commits an order is the owner
 * tapping confirm in Telegram — invariant 1, which this screen cannot break
 * because it has no write path at all (see `orders/data.ts`). There is no
 * confirm affordance anywhere on it, deliberately.
 *
 * Layout, in the order the questions arrive:
 *
 *   1. the run, and what it costs                 — a row of bordered stat cards
 *   2. what runs out before anything can arrive   — the only urgent thing
 *   3. one card per supplier, all eight           — lines, terms, skips
 *   4. where a line moved supplier, and why       — sourcing
 *   5. the terms every figure above rests on      — six of eight invented
 *
 * The opening row is five separate bordered cards rather than one panel with
 * five columns. That is the reference's opening move, and the flat multi-column
 * band it replaces is what read as a table someone forgot to finish.
 */
import { useMemo, useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import {
  Badge,
  Card,
  Chip,
  DeltaPill,
  ErrorBox,
  Fig,
  Loading,
  Note,
  PageHeader,
  Panel,
  SectionLabel,
  StatCard,
  Tabs,
} from '../components/ui'
import { cmp, fromInt } from '../lib/dec'
import { moneyDec, plural, stamp } from '../lib/format'
import {
  LATEST_RUN,
  RUNS,
  fetchDraft,
  fetchSuppliers,
  prose,
  sumPence,
  type DraftOrders,
  type SupplierOrder,
} from './orders/data'
import { bucketNotes } from './orders/notes'
import { M } from './orders/figures'
import { Sourcing } from './orders/Sourcing'
import { SupplierCard } from './orders/SupplierCard'
import { TermsTable } from './orders/Terms'
import { Premium, Urgent } from './orders/Urgent'

/* ------------------------------------------------------------------ pieces */

/** A supplier with lines comes first, then one with something to explain, then
 *  the quiet ones. None of them is dropped: an empty card with a reason on it is
 *  the answer to "why is there nothing from Cakesmiths?". */
function rank(o: SupplierOrder): number {
  if (o.lines.length > 0) return 0
  if (o.skipped.length > 0 || !o.meets_minimum) return 1
  return 2
}

/**
 * Five bordered cards. Every track is `minmax(0,1fr)` — Tailwind's `grid-cols-n`
 * is exactly that, and a bare `1fr` is not: its min-content floor is what pushed
 * a long figure past the viewport edge and sliced it mid-digit.
 */
function StatBand({ children }: { children: React.ReactNode }) {
  return (
    <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-3 xl:grid-cols-5">{children}</div>
  )
}

/* ------------------------------------------------------------------ screen */

export function Orders() {
  const [run, setRun] = useState<string>(LATEST_RUN)
  const [open, setOpen] = useState<ReadonlySet<string>>(new Set())

  const draft = useQuery({
    queryKey: ['orders-draft', run],
    queryFn: () => fetchDraft(run),
  })
  const suppliers = useQuery({ queryKey: ['suppliers'], queryFn: fetchSuppliers })

  const toggle = (key: string) =>
    setOpen((prev) => {
      const next = new Set(prev)
      if (next.has(key)) next.delete(key)
      else next.add(key)
      return next
    })

  const d: DraftOrders | undefined = draft.data
  const view = useMemo(() => summarise(d), [d])
  const chosenRun = RUNS.find((r) => r.date === run)

  return (
    <>
      <PageHeader
        title="Orders"
        lede="What is about to be bought, why that quantity, and what was deliberately not bought. Every figure here is computed on read."
        right={
          <Tabs
            tabs={RUNS.map((r) => ({ key: r.date, label: r.label }))}
            active={run}
            onChange={setRun}
          />
        }
      />

      {/* The sentence that makes the page safe to explore. Not a Note: it is the
          one paragraph on the screen that must not be skimmed past. */}
      <div className="mb-7">
        <Panel tone="info" title="Nothing here is an order.">
          <span className="block max-w-[92ch]">
            The draft is recomputed every time it is read and writes nothing at all; the only
            thing that commits it is the owner confirming it in Telegram. Open every line, change
            the run, read every cap — none of it spends a penny.
            {d && d.writes_nothing !== true && (
              <span className="text-bad-ink">
                {' '}
                This response did not declare <code className="fig">writes_nothing</code>, which
                it should have. Treat it with suspicion.
              </span>
            )}
          </span>
        </Panel>
      </div>

      {draft.isPending && <Loading what="Computing the run" />}
      {draft.isError && <ErrorBox error={draft.error} />}

      {d && view && (
        /* `[&>*]:min-w-0` is load-bearing. A grid item's automatic minimum is
           its MIN-CONTENT, and min-content walks straight through the plain
           blocks inside a card down to the `min-w-[50rem]` table — so without it
           the widest table on the page sets the width of the page, and every
           figure on it slides out past the right edge. The scroller cannot stop
           that on its own: the zero-minimum rule applies to a scroll container
           that is itself the flex or grid item, and this one is three blocks
           deep inside one. */
        <div className="grid gap-6 [&>*]:min-w-0">
          {/* 1 — the run, and what it costs */}
          <div>
            <SectionLabel right={`computed ${stamp(d.computed_at)}`}>This run</SectionLabel>
            <p className="-mt-2 mb-3 text-[0.75rem] text-ink-4">
              ordered for {d.order_date} · tiers {d.tiers.join(' and ')} ·{' '}
              {d.reorder_cadence_days === null
                ? 'no reorder cadence is set'
                : `cadence ${d.reorder_cadence_days} d`}
              {chosenRun ? ` · showing ${chosenRun.note}` : ''}
            </p>

            <StatBand>
              <StatCard
                label="Draft spend"
                value={<M v={d.total_pence} size="xl" />}
                delta={
                  <DeltaPill
                    value={`${view.lineCount} ${plural(view.lineCount, 'line')}`}
                    tone="plain"
                  />
                }
                sub={`${view.suppliersWithLines} of ${d.suppliers.length} suppliers have something on them`}
              />
              <StatCard
                label="On invented terms"
                tone={view.placeholderSpendIsAll ? 'warn' : 'plain'}
                value={<Fig size="xl">{moneyDec(view.placeholderSpend)}</Fig>}
                delta={
                  view.placeholderSuppliersWithLines.length === 0 ? undefined : (
                    <DeltaPill
                      value={
                        view.placeholderSpendIsAll
                          ? 'all of it'
                          : `${view.placeholderSuppliersWithLines.length} of ${view.suppliersWithLines}`
                      }
                      tone="warn"
                    />
                  )
                }
                sub={
                  view.placeholderSuppliersWithLines.length === 0
                    ? 'nothing on this run comes from a supplier whose terms are guesses'
                    : `${view.placeholderSuppliersWithLines.join(', ')} — lead time, delivery days, cutoff and minimum were never confirmed`
                }
              />
              <StatCard
                label="Lines capped"
                tone={d.capped_line_count > 0 ? 'warn' : 'plain'}
                value={<Fig size="xl">{d.capped_line_count}</Fig>}
                delta={
                  d.capped_line_count > 0 ? (
                    <DeltaPill value="invariant 4" tone="warn" />
                  ) : undefined
                }
                sub={
                  d.capped_line_count === 0
                    ? 'no line was shortened by shelf life or a season this run'
                    : 'shelf life or a season shortened the window, so less was ordered on purpose'
                }
              />
              <StatCard
                label="Not ordered on purpose"
                value={<Fig size="xl">{view.skipCount}</Fig>}
                delta={
                  view.suppliersWithSkips > 0 ? (
                    <DeltaPill
                      value={`${view.suppliersWithSkips} ${plural(view.suppliersWithSkips, 'supplier')}`}
                      tone="plain"
                    />
                  ) : undefined
                }
                sub={`${plural(view.skipCount, 'decision')} with a cause on each, on the supplier cards below`}
              />
              <StatCard
                label="Retail premium"
                tone={d.emergency.length > 0 ? 'bad' : 'plain'}
                value={
                  <Premium
                    totalPremiumPence={d.emergency_total_premium_pence}
                    lines={d.emergency.length}
                  />
                }
                delta={
                  d.emergency.length > 0 ? (
                    <DeltaPill
                      value={`${d.emergency.length} ${plural(d.emergency.length, 'line')}`}
                      tone="bad"
                    />
                  ) : undefined
                }
                sub={
                  d.emergency.length === 0
                    ? 'nothing was routed to retail'
                    : `${d.emergency.length} ${plural(d.emergency.length, 'line')} bought at retail to bridge a gap`
                }
              />
            </StatBand>

            {/* The supplier totals are re-summed here as exact integer pence. If
                they disagree with the run's own total, one of the two is wrong
                and no amount of presentation should smooth that over. */}
            {view.totalDisagrees && (
              <div className="mt-3">
                <Panel tone="bad" title="The two totals disagree">
                  The suppliers&rsquo; own totals add up to {moneyDec(view.supplierTotal)}, which is
                  not the {moneyDec(fromInt(d.total_pence))} the run reports. One of the two is
                  wrong; do not confirm anything until it is known which.
                </Panel>
              </div>
            )}
          </div>

          {/* 2 — the only urgent thing on the page */}
          <Urgent
            emergency={d.emergency}
            notes={d.emergency_notes}
            totalPremiumPence={d.emergency_total_premium_pence}
          />

          {/* 3 — one card per supplier */}
          <div>
            <SectionLabel right={`${view.suppliersWithLines} with lines`}>
              {d.suppliers.length} draft orders
            </SectionLabel>
            <p className="-mt-2 mb-4 max-w-[92ch] text-[0.75rem] text-ink-4">
              One per supplier, each with its own terms, minimum and delivery date — never one
              basket. The ones with nothing on them are here for their reasons: a line re-sourced
              elsewhere, products that are a checklist rather than a forecast, or no products on
              file at all.
            </p>
            <div className="grid gap-4 [&>*]:min-w-0">
              {view.ordered.map((o) => (
                <SupplierCard
                  key={o.supplier.supplier_id}
                  o={o}
                  alreadyShown={view.alreadyShown}
                  open={open}
                  onToggle={toggle}
                />
              ))}
            </div>
          </div>

          {/* 4 — sourcing */}
          <Card
            title="Where a line changed supplier"
            subtitle="Cheaper per unit is not enough on its own: the switch must also leave every other supplier above its minimum."
          >
            <Sourcing choices={d.sourcing_choices} />
          </Card>

          {/* 5 — the terms all of the above rests on */}
          <Card
            title="The terms every figure above rests on"
            subtitle={`${d.placeholder_supplier_names.length} of ${d.suppliers.length} suppliers' terms are invented placeholders`}
            right={<Badge tone="warn">unconfirmed</Badge>}
          >
            <div className="flex flex-wrap gap-1">
              {d.placeholder_supplier_names.map((n) => (
                <Chip key={n} tone="warn">
                  {n}
                </Chip>
              ))}
            </div>
            {view.runNotes.length > 0 && (
              <div className="mt-4 grid gap-2">
                {view.runNotes.map((n) => (
                  <p key={n} className="max-w-[92ch] text-[0.75rem] leading-[16px] text-ink-2">
                    {prose(n)}
                  </p>
                ))}
              </div>
            )}

            {suppliers.data && (
              <details className="mt-4 border-t border-line pt-3">
                <summary className="cursor-pointer text-[0.75rem] text-ink-4 hover:text-ink-2">
                  all {suppliers.data.length} suppliers&rsquo; terms side by side
                </summary>
                <div className="mt-3">
                  <TermsTable suppliers={suppliers.data} />
                </div>
              </details>
            )}
            {suppliers.isError && (
              <Note tone="muted">
                <span className="mt-3 block">
                  The supplier list itself could not be read, so only the terms carried on each
                  draft above are available.
                </span>
              </Note>
            )}
          </Card>
        </div>
      )}
    </>
  )
}

/* ------------------------------------------------------------ derived view */

/**
 * Everything the screen needs that is not already a field.
 *
 * All money here is summed as exact integer pence through `lib/dec`; no float
 * touches it (invariant 11). Nothing is derived from a sentence — the prose
 * buckets only decide WHERE a note is printed, never what a figure is.
 */
function summarise(d: DraftOrders | undefined) {
  if (!d) return null

  const ordered = [...d.suppliers].sort(
    (a, b) =>
      rank(a) - rank(b) ||
      cmp(fromInt(b.total_pence), fromInt(a.total_pence)) ||
      a.supplier.name.localeCompare(b.supplier.name),
  )

  const withLines = d.suppliers.filter((o) => o.lines.length > 0)
  const placeholderOrders = withLines.filter((o) => o.supplier.terms_are_placeholders)
  const placeholderSpend = sumPence(placeholderOrders.map((o) => o.total_pence))
  const supplierTotal = sumPence(d.suppliers.map((o) => o.total_pence))

  /* Sentences already rendered as first-class objects elsewhere on the page. A
     skip, a sourcing choice and an emergency note are each shown in full where
     they belong, so printing them again inside a supplier's note list would be
     the same finding twice. */
  const alreadyShown = [
    ...d.suppliers.flatMap((o) => o.skipped.map((s) => s.reason)),
    ...d.sourcing_choices.map((c) => c.reason),
    ...d.emergency.map((e) => e.reason),
    ...d.emergency_notes,
  ]

  const runBuckets = bucketNotes(d.notes, [
    ...alreadyShown,
    /* The per-supplier placeholder notes are said on each card, in the terms
       panel, against the individual invented field. The run-level one is the
       only summary and belongs here. */
  ])

  return {
    ordered,
    alreadyShown,
    suppliersWithLines: withLines.length,
    lineCount: d.suppliers.reduce((n, o) => n + o.lines.length, 0),
    skipCount: d.suppliers.reduce((n, o) => n + o.skipped.length, 0),
    suppliersWithSkips: d.suppliers.filter((o) => o.skipped.length > 0).length,
    placeholderSpend,
    placeholderSuppliersWithLines: placeholderOrders.map((o) => o.supplier.name),
    placeholderSpendIsAll:
      placeholderOrders.length > 0 && cmp(placeholderSpend, fromInt(d.total_pence)) === 0,
    supplierTotal,
    totalDisagrees: cmp(supplierTotal, fromInt(d.total_pence)) !== 0,
    runNotes: [
      ...runBuckets.placeholder,
      /* `runBuckets.sourcing` is deliberately absent: the run-level sourcing
         summary restates, word for word, the choices already rendered in full
         on the sourcing card above it. */
      ...runBuckets.cap,
      ...runBuckets.minimum,
      ...runBuckets.topup,
      ...runBuckets.other,
    ],
  }
}
