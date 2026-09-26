/**
 * Money & P&L. Sidebar hint: "where it went".
 *
 * There is no general ledger in this system and no endpoint that returns one, so
 * this screen does not present a P&L. It now shows both sides of the money the
 * data actually supports — what the cafe TOOK (`/api/takings`, from a back-office
 * payment export) and where it WENT (the purchase side and the waste) — and then
 * says plainly what a P&L would still need that nothing here holds.
 *
 * The takings half arrived late and is deliberately not joined to the purchase
 * half: takings over 8 reporting days, whose net the API withholds, minus a draft
 * order nobody has confirmed, is not a contribution figure. `money/Takings.tsx`
 * carries that reasoning.
 *
 * Four honesty rules do most of the work on the purchase side:
 *  - The draft total is a DRAFT. `writes_nothing` is true; nothing is committed
 *    until a human confirms it in Telegram (invariant 1). It is not spend.
 *  - Six of eight suppliers' terms were invented, so the minimums and fees the
 *    ordering maths is sized against are fiction for three quarters of the
 *    table. They are grouped under a heading that says so.
 *  - `meets_minimum: false` on a supplier with nothing needed is not a blocked
 *    order. Only a supplier with lines that fall short is.
 *  - Waste is the one figure here that is real money already gone, and it is
 *    null rather than zero when any expiring batch has no unit cost.
 */
import { useQuery } from '@tanstack/react-query'
import { api } from '../lib/api'
import { fromInt, isZero, parseDec, sum } from '../lib/dec'
import { poundsOnly, stamp } from '../lib/format'
import {
  Badge,
  Card,
  ErrorBox,
  Loading,
  PageHeader,
  Panel,
  SectionLabel,
  StatCard,
} from '../components/ui'
import { Label } from '../components/prim'
import { DraftSpend, Minimums } from './money/DraftSpend'
import { SupplierTerms } from './money/SupplierTerms'
import { Takings } from './money/Takings'
import { Field, N, P, PD, maybePence, prose } from './money/figures'

/** A row of bordered stat cards. Never a bare `1fr` or an implicit auto track:
 *  both have a min-content floor, and one wide figure then pushes the whole page
 *  sideways instead of the one block that is too wide. */
function CardRow({ children }: { children: React.ReactNode }) {
  return (
    <div className="grid grid-cols-[minmax(0,1fr)] gap-4 sm:grid-cols-2 lg:grid-cols-4">
      {children}
    </div>
  )
}

/** What a real P&L needs and this system does not hold. Written as the absence
 *  it is, with the nearest thing that does exist named, so nobody goes looking
 *  for a screen that is not missing but simply does not exist. */
const NOT_COVERED: { what: string; why: string }[] = [
  {
    what: 'Revenue reconciled against sales — takings themselves are now here',
    why: 'What the cafe took is read from a back-office payment export and shown at the top of this screen, so revenue is no longer absent. What is still absent is the join: takings are settled payments by day and method, Lightspeed sales are lines that deplete stock, and nothing matches one against the other. A till that is over or under would not show up anywhere.',
  },
  {
    what: 'Labour actuals',
    why: 'There is a loaded hourly rate and a prep time per drink, which together model what a drink costs to make. Nobody’s shift, rota or hours are recorded anywhere, so modelled labour cannot be reconciled against a wage bill.',
  },
  {
    what: 'Rent, utilities, insurance, overheads',
    why: 'Never entered. There is no table for them and no import that would populate one.',
  },
  {
    what: 'Channel commission and ad spend',
    why: 'Deliveroo and Just Eat take a cut and sell advertising, and both are real costs. The channels screen reads them from a portal export and shows them per channel; nothing joins that to this screen. There is no endpoint that puts purchasing and channel costs in one place, so neither figure is in any total here.',
  },
  {
    what: 'VAT, and invoices actually paid',
    why: 'Prices are held net of nothing in particular and there is no accounts-payable side: a purchase order that was confirmed, delivered and paid looks identical here to one that was drafted and ignored.',
  },
]

/**
 * A row count from `/api/health`, which is **null to an unauthenticated caller**
 * rather than zero. This screen sits behind the gate so they are normally there,
 * but "not disclosed" and "none" must never look the same.
 */
function Count({ n, one, many }: { n: number | null; one: string; many: string }) {
  if (n === null) return <span className="text-ink-5 italic">not disclosed</span>
  return (
    <>
      <span className="fig">{n.toLocaleString('en-GB')}</span> {n === 1 ? one : many}
    </>
  )
}

export function Money() {
  const suppliers = useQuery({ queryKey: ['suppliers'], queryFn: () => api.suppliers() })
  const draft = useQuery({ queryKey: ['orders-draft'], queryFn: () => api.ordersDraft() })
  const today = useQuery({ queryKey: ['today'], queryFn: () => api.today() })
  const health = useQuery({ queryKey: ['health'], queryFn: () => api.health() })
  const meta = useQuery({ queryKey: ['meta'], queryFn: () => api.meta() })

  if (suppliers.isPending || draft.isPending) {
    return (
      <>
        <PageHeader title="Money &amp; P&amp;L" />
        <Loading what="Reading suppliers and the draft order" />
      </>
    )
  }
  if (suppliers.isError || draft.isError || !suppliers.data || !draft.data) {
    return (
      <>
        <PageHeader title="Money &amp; P&amp;L" />
        <ErrorBox error={suppliers.error ?? draft.error} />
      </>
    )
  }

  const d = draft.data
  const sups = suppliers.data
  const fees = sum(d.suppliers.map((o) => fromInt(o.delivery_fee_pence)))
  const withLines = d.suppliers.filter((o) => o.lines.length > 0).length
  const invented = sups.filter((s) => s.terms_are_placeholders)

  // Waste is the only real money on this screen. Both figures are null — not
  // zero — when an expiring batch has no unit cost, because a partial total
  // understates the waste it exists to warn about (invariant 8).
  const writeOffValue = today.data ? maybePence(today.data.expiry_write_offs_value_pence) : null
  const expiringValue = today.data ? maybePence(today.data.stock.expiring_value_pence) : null
  const wasteMissing = today.isSuccess ? 'a batch has no unit cost' : 'today did not load'
  // Exact, not a string comparison: "0", "0.00" and "0.000000" are all zero and
  // only a non-zero write-off earns the `bad` ink.
  const lostMoney = ((): boolean => {
    if (writeOffValue === null) return false
    const v = parseDec(writeOffValue)
    return v !== null && !isZero(v)
  })()

  return (
    <>
      <PageHeader
        title="Money &amp; P&amp;L"
        lede={
          <>
            What the café took, and where it went — as far as the data goes. Takings come from a
            back-office payment export and cover the days that reported, not a whole period; the
            other half is purchasing and waste. There is no ledger in this system, so nothing
            below is a profit and loss account.
          </>
        }
        right={
          <div className="text-right">
            <Badge tone="info">draft · writes nothing</Badge>
            <div className="mt-1">
              <Label>computed {stamp(d.computed_at)}</Label>
            </div>
          </div>
        }
      />

      <div className="grid grid-cols-[minmax(0,1fr)] gap-6">
        {/* Takings first: what came in, before what it cost. It is its own query
            and its own failure — an export nobody has loaded must not blank the
            purchase side, which is computed and always available. */}
        <Takings />

        <div>
          <SectionLabel right="two proposals, then two losses">
            Where it went: the money this purchase data supports
          </SectionLabel>
          <CardRow>
            <StatCard
              label="Draft order total"
              value={<P v={d.total_pence} size="xl" />}
              sub={`${withLines} of ${d.suppliers.length} suppliers have lines · nothing confirmed`}
            />
            <StatCard
              label="Delivery fees in it"
              value={<PD v={fees} size="xl" />}
              sub="waived wherever a free-delivery threshold was cleared"
            />
            <StatCard
              label="Written off to expiry"
              value={<P v={writeOffValue} size="xl" missing={wasteMissing} />}
              tone={lostMoney ? 'bad' : 'plain'}
              sub={
                today.data
                  ? `${today.data.expiry_write_offs_due} write-off${
                      today.data.expiry_write_offs_due === 1 ? '' : 's'
                    } due today · money already gone`
                  : 'money already gone'
              }
            />
            <StatCard
              label="Short-dated stock"
              value={<P v={expiringValue} size="xl" missing={wasteMissing} />}
              sub="value of batches close to expiry · waste unless it moves"
            />
          </CardRow>
          <div className="mt-4">
            <Panel title="Which two of those four are real">
              The first two are a proposal, not a payment: the draft is recomputed on every read
              and is only an order once somebody says yes in Telegram. The second two are the only
              money on this screen that has already been lost.
              {today.data && today.data.draft_order_total_pence === null && (
                <>
                  {' '}
                  The daily summary carries no draft figures of its own — it omits them unless a
                  full ordering run is requested — so the total above comes from a run made for
                  this screen.
                </>
              )}
            </Panel>
          </div>
        </div>

        <DraftSpend draft={d} />

        <Minimums draft={d} />

        <SupplierTerms suppliers={sups} />

        <Card
          title="Waste, valued"
          subtitle="The only figures here that are real money rather than a proposal."
          right={
            <Badge tone={lostMoney ? 'bad' : 'muted'}>
              {today.data ? `${today.data.expiry_write_offs_due} due` : 'not read'}
            </Badge>
          }
        >
          <div className="grid grid-cols-[minmax(0,1fr)] gap-5 sm:grid-cols-3">
            <Field label="Write-offs due today">
              <N v={today.data ? today.data.expiry_write_offs_due : null} size="lg" />
            </Field>
            <Field label="Their value">
              <P v={writeOffValue} size="lg" missing={wasteMissing} />
            </Field>
            <Field label="Short-dated stock at risk">
              <P v={expiringValue} size="lg" missing={wasteMissing} />
            </Field>
          </div>
          <div className="mt-4 grid grid-cols-[minmax(0,1fr)] gap-3 border-t border-line pt-4">
            <Panel title="Why this is the only honest waste figure in the building">
              An expiry write-off is the figure a paper system never produces: a batch reaching
              its effective expiry with stock left, priced at what was paid for it. It is also the
              reason drift has to be attributed — a gap explained by expiry means over-ordering,
              and a gap explained by measurement means a wrong recipe. The two have opposite
              fixes.
            </Panel>
            <Panel title="Why either figure can be absent">
              Either reads as absent rather than zero if any batch in the set has no unit cost. A
              partial total would understate exactly the waste it exists to warn about, so the API
              withholds it (invariant 8). 42 of 113 ingredient prices are estimates, which makes
              both figures approximate even when they are reported.
            </Panel>
            {today.data?.stock.notes.map((n) => (
              <Panel key={n} tone="warn">
                {prose(n)}
              </Panel>
            ))}
          </div>
        </Card>

        <Card title="Not covered — and it is not a small list">
          <div className="divide-y divide-line">
            {NOT_COVERED.map((r, i) => (
              <div key={r.what} className={i === 0 ? 'pb-3' : 'py-3 last:pb-0'}>
                <p className="text-[0.875rem] font-medium text-ink">{r.what}</p>
                <p className="mt-0.5 max-w-[80ch] text-[0.875rem] leading-[20px] text-ink-3">
                  {r.why}
                </p>
              </div>
            ))}
          </div>
          <div className="mt-4 grid grid-cols-[minmax(0,1fr)] gap-3 border-t border-line pt-4">
            <Panel tone="warn" title="So there is still no P&amp;L on this screen, deliberately">
              Takings have arrived, and they are the one thing that was missing entirely. They are
              not enough. A profit and loss account also needs labour actuals, overheads and the
              channel commission and ad spend — and this window&rsquo;s takings cover the days
              that reported, with a net the API withholds. Subtracting an unconfirmed draft order
              from a partial gross would produce a contribution figure that is wrong in both
              directions at once, so this screen does not compute one.
            </Panel>
            {meta.data && (
              <Panel title="The nearest thing to a labour figure">
                A loaded rate of{' '}
                <span className="fig">{poundsOnly(meta.data.loaded_hourly_rate_pence)}</span> per
                hour applied to each item&rsquo;s prep seconds. That is a modelled cost per drink
                used to rank the menu by margin per minute; it is not a wage bill and it is not in
                any total above.
              </Panel>
            )}
          </div>
        </Card>

        {/* System state, quietly. Not a dashboard tile: it answers "is anything
            even connected", and on the day the answer is no, it is the first
            thing worth knowing. */}
        <Card>
          {health.data ? (
            <p className="text-[0.75rem] leading-[20px] text-ink-3">
              API <span className={health.data.status === 'ok' ? 'text-ok-ink' : 'text-bad-ink'}>
                {health.data.status}
              </span>{' '}
              · <span className="fig">{health.data.database_dialect}</span> ·{' '}
              {health.data.auth_configured ? (
                'auth configured'
              ) : (
                <span className="text-bad-ink">auth not configured</span>
              )}{' '}
              ·{' '}
              <Count n={health.data.ingredients} one="ingredient" many="ingredients" /> ·{' '}
              <Count n={health.data.menu_items} one="menu item" many="menu items" /> ·{' '}
              <Count n={health.data.templates} one="template" many="templates" /> ·{' '}
              <Count n={health.data.movements} one="stock movement" many="stock movements" />
              {invented.length > 0 && (
                <>
                  {' '}
                  ·{' '}
                  <span className="text-warn-ink">
                    {invented.length} of {sups.length} suppliers on invented terms
                  </span>
                </>
              )}
            </p>
          ) : (
            <p className="text-[0.75rem] text-ink-3">
              System state could not be read.{' '}
              {health.isError ? 'The health endpoint did not answer.' : 'Still reading…'}
            </p>
          )}
        </Card>
      </div>
    </>
  )
}
