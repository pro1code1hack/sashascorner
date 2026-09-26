/**
 * What the café took — the half of Money & P&L that did not exist until now.
 *
 * The owner's architecture sketch lists "payment reports" under Lightspeed; the
 * written brief never modelled them (spec §4.5 models `sale`, which is what was
 * *ordered*, not what was *settled*). So this screen could only ever show the
 * purchase side. `/api/takings` is the other half, read from a back-office CSV
 * export — never from the POS API, which has not been probed.
 *
 * Three things govern everything below:
 *
 *  - **`net_pence` is null and stays null.** One export omitted a discounts
 *    column, so the backend refuses to subtract the deductions it happens to
 *    hold: gross − refunds − fees, with discounts silently skipped, is a net
 *    that is too high and entirely plausible. That refusal is the finding, so it
 *    is rendered as a stated reason in place of the figure (invariant 8 applied
 *    to revenue), never as £0.00 and never as a bare dash.
 *  - **8 of 14 days reported is not a fortnight's takings.** Every total here is
 *    the sum over the days that reported; the rest are absent, not zero. The
 *    coverage is said above the figures, beside them, and again verbatim in the
 *    caveats.
 *  - **The `caveats[]` strings are backend-authored and printed word for word.**
 *    They name exactly which rows could not be summed. Paraphrasing them would
 *    lose the row counts, which are the only measure of how bad the gap is.
 *
 * Nothing here computes contribution. Takings over 8 reporting days minus a
 * draft order nobody has confirmed is not a profit figure, and the deductions
 * that would make it one are the ones that are missing.
 */
import { useQuery } from '@tanstack/react-query'
import { api } from '../../lib/api'
import { cmp, fromInt, sum, sharePct } from '../../lib/dec'
import { dayShort } from '../../lib/format'
import {
  Badge,
  Card,
  Cell,
  CopyBlock,
  DeltaPill,
  Empty,
  ErrorBox,
  Fig,
  Loading,
  Note,
  Panel,
  ScrollX,
  SectionLabel,
  StatCard,
  Table,
  Td,
  Th,
} from '../../components/ui'
import { Label } from '../../components/prim'
import type { TakingsResponse } from '../../lib/types'

/** A window that actually reported. `gross_pence` is null only when nothing did. */
type Reported = Omit<TakingsResponse, 'gross_pence'> & { gross_pence: number }
import { Field, N, P, PD } from './figures'

/** The window the fixture and the recorded response both cover. A fortnight is
 *  short enough that a café owner remembers the days, which is what makes the
 *  coverage gap legible rather than abstract. */
const WINDOW_DAYS = 14

/** A row of bordered stat cards. `minmax(0,1fr)` rather than `1fr`, and
 *  `min-w-0` on the children: both tracks otherwise floor at min-content and one
 *  wide figure pushes the whole page sideways. */
function CardRow({ children }: { children: React.ReactNode }) {
  return (
    <div className="grid grid-cols-[minmax(0,1fr)] gap-4 sm:grid-cols-2 lg:grid-cols-4 [&>*]:min-w-0">
      {children}
    </div>
  )
}

const METHOD: Record<string, string> = {
  CARD: 'Card',
  CASH: 'Cash',
  VOUCHER: 'Voucher',
  ACCOUNT: 'Account',
  OTHER: 'Other, unmapped',
}

/** `OTHER` exists in the model so an unrecognised method is kept and labelled
 *  rather than folded into CARD; an unknown string is titled rather than shown
 *  as a shouting enum. */
function methodLabel(m: string): string {
  return METHOD[m] ?? m.charAt(0) + m.slice(1).toLowerCase().replace(/_/g, ' ')
}

function andList(items: string[]): string {
  if (items.length <= 1) return items[0] ?? ''
  return `${items.slice(0, -1).join(', ')} and ${items[items.length - 1]}`
}

/** Why there is no net, in the words of what is actually absent. Rendered *in
 *  place of* the figure, so nobody can read a number off this card. */
function netMissingReason(t: TakingsResponse): string {
  const absent = [
    t.refunds_pence === null ? 'refunds' : null,
    t.fees_pence === null ? 'card fees' : null,
    t.discounts_pence === null ? 'discounts' : null,
  ].filter((s): s is string => s !== null)
  if (absent.length === 0) return 'net not computed'
  return `${andList(absent)} not reported on every day, so net is withheld`
}

const DEDUCTION_MISSING = 'not reported on every day'

/* ------------------------------------------------------------ by method --- */

function ByMethod({ t }: { t: Reported }) {
  const rows = Object.entries(t.by_method_pence).sort((a, b) => b[1] - a[1])
  const total = sum(rows.map(([, v]) => fromInt(v)))
  // The same reconciliation the draft table does: if the methods do not add up
  // to the gross the API reported, the screen says so rather than quietly
  // showing its own sum as if it were the answer.
  const reconciles = cmp(total, fromInt(t.gross_pence)) === 0

  return (
    <Card
      title="Gross by method"
      subtitle={`How the money arrived, over the ${t.days_reported} day${
        t.days_reported === 1 ? '' : 's'
      } that reported.`}
      right={
        <Badge tone="warn">{`${t.days_reported} of ${t.window_days} day${
          t.window_days === 1 ? '' : 's'
        }`}</Badge>
      }
    >
      <ScrollX>
        <div className="min-w-[20rem]">
          <Table>
            <thead>
              <tr>
                <Th>Method</Th>
                <Th align="right">Gross</Th>
                <Th align="right">Share of gross</Th>
              </tr>
            </thead>
            <tbody>
              {rows.map(([method, pence]) => {
                const share = sharePct(pence, t.gross_pence)
                return (
                  <tr key={method}>
                    <Td>
                      <Cell wrap top={methodLabel(method)} />
                    </Td>
                    <Td align="right">
                      <P v={pence} />
                    </Td>
                    <Td align="right">
                      {share === null ? (
                        <Fig missing="no gross to divide by" />
                      ) : (
                        <Fig size="sm" tone="muted">
                          {share}
                        </Fig>
                      )}
                    </Td>
                  </tr>
                )
              })}
              <tr>
                <Td>
                  <Cell top="Total taken" sub="every method, as the API summed it" wrap />
                </Td>
                <Td align="right">
                  <P v={t.gross_pence} />
                </Td>
                <Td align="right">
                  <Label>{reconciles ? 'reconciles' : 'does not reconcile'}</Label>
                </Td>
              </tr>
            </tbody>
          </Table>
        </div>
      </ScrollX>

      {!reconciles && (
        <div className="mt-4">
          <Panel tone="bad" title="The methods do not add up to the gross">
            The per-method figures sum to <PD v={total} /> and the window&rsquo;s gross is{' '}
            <P v={t.gross_pence} />. One of the two is wrong and this screen cannot tell which, so
            neither should be used until the export is re-read.
          </Panel>
        </div>
      )}

      <div className="mt-2">
        <Note>
          Shares are rounded to a tenth of a percent and need not sum to exactly 100.
        </Note>
      </div>
      <div className="mt-4 grid grid-cols-[minmax(0,1fr)] gap-5 border-t border-line pt-4 sm:grid-cols-2 [&>*]:min-w-0">
        <Field label="Transactions in the window">
          <N v={t.transactions} size="lg" missing="not reported on every row" />
        </Field>
        <Field label="Window read">
          <Label>
            {dayShort(t.since)} — {dayShort(t.until)}
          </Label>
        </Field>
      </div>
      {t.transactions === null && (
        <div className="mt-2">
          <Note>
            With no transaction count there is no average spend per transaction either — a mean
            over the rows that did report a count would be an average of a different café.
          </Note>
        </div>
      )}

      <div className="mt-4 border-t border-line pt-4">
        <Note>{t.source_note}</Note>
      </div>
    </Card>
  )
}

/* ------------------------------------------------------------- populated --- */

function Loaded({ t }: { t: Reported }) {
  const coverage = `${t.days_reported} of ${t.window_days} day${
    t.window_days === 1 ? '' : 's'
  } reported`
  const grossShareRefunds = t.refunds_pence === null ? null : sharePct(t.refunds_pence, t.gross_pence)
  const grossShareFees = t.fees_pence === null ? null : sharePct(t.fees_pence, t.gross_pence)
  const over = `over ${t.days_reported} reporting day${t.days_reported === 1 ? '' : 's'}`

  return (
    <>
      <div>
        <SectionLabel right={`${dayShort(t.since)} — ${dayShort(t.until)}`}>
          What the café took
        </SectionLabel>

        {/* Coverage first, above the figures rather than under them: every total
            below is a sum over the days that reported, and read as a fortnight
            it understates trade by nearly half. */}
        <div className="mb-3 flex flex-wrap items-center gap-2">
          <Badge tone="warn">{coverage}</Badge>
          <Label>
            {t.days_reported} reporting day{t.days_reported === 1 ? '' : 's'}, not a{' '}
            {t.window_days}-day period — the rest are absent, not zero
          </Label>
        </div>

        <CardRow>
          <StatMoney
            label="Gross takings"
            v={t.gross_pence}
            sub={`settled payments ${over} · before refunds, fees and discounts`}
          />
          <StatMoney
            label="Refunds"
            v={t.refunds_pence}
            missing={DEDUCTION_MISSING}
            delta={grossShareRefunds === null ? null : `${grossShareRefunds} of gross`}
            sub={`refunded ${over} · a deduction from gross`}
          />
          <StatMoney
            label="Card fees"
            v={t.fees_pence}
            missing={DEDUCTION_MISSING}
            delta={grossShareFees === null ? null : `${grossShareFees} of gross`}
            sub={`charged by the acquirer ${over}`}
          />
          <StatMoney
            label="Net takings"
            v={t.net_pence}
            missing={netMissingReason(t)}
            sub="not computed from the deductions that did report"
          />
        </CardRow>

        <div className="mt-4 grid grid-cols-[minmax(0,1fr)] gap-3">
          {t.net_pence === null && (
            <Panel tone="warn" title="Net is withheld, and that is the finding">
              One export omitted a discounts column. Gross minus the deductions that did report
              would be a net that is <em>too high</em> and entirely plausible — the worst kind of
              wrong, because nothing about it looks wrong. So the API returns{' '}
              <code className="fig">null</code> rather than a partial subtraction, and this card
              shows the reason in place of a figure. Re-export the day that is missing its
              discounts and the net appears on its own.
            </Panel>
          )}
          {t.caveats.length > 0 && (
            <Panel
              tone="warn"
              title="What could not be summed, in the backend's own words"
              right={`${t.caveats.length} caveat${t.caveats.length === 1 ? '' : 's'}`}
            >
              <ul className="grid grid-cols-[minmax(0,1fr)] gap-1.5">
                {t.caveats.map((c) => (
                  <li key={c} className="flex gap-2">
                    <span aria-hidden="true" className="text-warn-ink">
                      ·
                    </span>
                    <span className="min-w-0">{c}</span>
                  </li>
                ))}
              </ul>
            </Panel>
          )}
        </div>
      </div>

      <ByMethod t={t} />
    </>
  )
}

/** The kit's stat card, wrapped once so all four of these can render a reason in
 *  place of a number rather than a figure beside one. No sparkline: `/api/takings`
 *  answers for a window, not a series, and a shape drawn through one point is
 *  decoration — worse, a day nobody reported would dip it to the floor and read
 *  as trade collapsing. */
function StatMoney({
  label,
  v,
  missing,
  sub,
  delta,
}: {
  label: string
  v: number | null
  missing?: string
  sub: string
  delta?: string | null
}) {
  return (
    <StatCard
      label={label}
      value={<P v={v} size="xl" missing={missing ?? 'not computed'} />}
      delta={v !== null && delta ? <DeltaPill value={delta} tone="muted" /> : undefined}
      sub={sub}
    />
  )
}

/* ----------------------------------------------------------------- empty --- */

/**
 * Nothing imported. The API answers a window with no payment days by summing
 * nothing — gross 0, deductions 0, net 0 — and every one of those zeros is a
 * lie about trading. `days_reported === 0` is the only honest reading, so the
 * figures are blanked with a reason and the two commands that fill them are
 * printed as text you can select.
 */
function NotLoaded({ t }: { t: TakingsResponse }) {
  const why = 'no payment export loaded'
  return (
    <>
      <div>
        <SectionLabel right={`${dayShort(t.since)} — ${dayShort(t.until)}`}>
          What the café took
        </SectionLabel>
        <div className="mb-3 flex flex-wrap items-center gap-2">
          <Badge tone="muted">
            0 of {t.window_days} day{t.window_days === 1 ? '' : 's'} reported
          </Badge>
          <Label>nothing has been imported for this window</Label>
        </div>
        <CardRow>
          <StatMoney label="Gross takings" v={null} missing={why} sub="settled payments" />
          <StatMoney label="Refunds" v={null} missing={why} sub="a deduction from gross" />
          <StatMoney label="Card fees" v={null} missing={why} sub="charged by the acquirer" />
          <StatMoney label="Net takings" v={null} missing={why} sub="gross less every deduction" />
        </CardRow>
        <div className="mt-4">
          <Panel title="Blank rather than zero">
            The API sums an empty window to zero on every line. Zero would read as{' '}
            {t.window_days} day{t.window_days === 1 ? '' : 's'} in which the café took nothing,
            which is a different business from {t.window_days} day
            {t.window_days === 1 ? '' : 's'} nobody exported — so the cards say which one this
            is.
          </Panel>
        </div>
      </div>

      <Card
        title="No takings recorded in this window"
        right={<Badge tone="muted">0 days</Badge>}
      >
        <Empty title={`Nothing between ${dayShort(t.since)} and ${dayShort(t.until)}`}>
          Takings are not read from the POS. They come from a payment report exported out of the
          back office, and until one is imported there is no gross, no card and cash split and no
          net on this screen.
        </Empty>
        <div className="grid grid-cols-[minmax(0,1fr)] gap-6 border-t border-line pt-5 lg:grid-cols-2 lg:gap-8 [&>*]:min-w-0">
          <div>
            <SectionLabel>Load it</SectionLabel>
            <div className="grid grid-cols-[minmax(0,1fr)] gap-2">
              <CopyBlock>uv run cafeops payments import --commit</CopyBlock>
              <Note>
                Dry run is the default, so the plain command tells you what it would record and
                writes nothing. <code className="fig">--commit</code> is the one that writes.
              </Note>
              <CopyBlock>export CAFEOPS_PAYMENTS_CSV_DIR=/path/to/back-office-exports</CopyBlock>
              <Note>
                Without it the importer reads the sample exports that ship with the repo — enough
                to prove the pipeline and this screen, and not your trading. Point it at the
                directory the back office writes to, then run the import again.
              </Note>
            </div>
          </div>
          <div className="border-t border-line pt-5 lg:border-l lg:border-t-0 lg:pt-0 lg:pl-8">
            <SectionLabel>Why an export and not a sync</SectionLabel>
            <div className="grid grid-cols-[minmax(0,1fr)] gap-3">
              <div className="flex flex-wrap items-center gap-2">
                <Badge tone="info">no endpoint probed</Badge>
                <Label>not a missing feature</Label>
              </div>
              <Note>{t.source_note}</Note>
              <Note>
                Every row records the source it came from, so a hand export is never averaged with
                a POS figure as if the two were equally trustworthy.
              </Note>
            </div>
          </div>
        </div>
      </Card>
    </>
  )
}

/* ------------------------------------------------------------------ root --- */

export function Takings({ days = WINDOW_DAYS }: { days?: number }) {
  const q = useQuery({ queryKey: ['takings', days], queryFn: () => api.takings(days) })

  if (q.isPending) {
    return (
      <div>
        <SectionLabel>What the café took</SectionLabel>
        <Card>
          <Loading what="Reading the payment export" />
        </Card>
      </div>
    )
  }
  if (q.isError || !q.data) {
    return (
      <div>
        <SectionLabel>What the café took</SectionLabel>
        <ErrorBox error={q.error} />
      </div>
    )
  }
  // `gross_pence` is null when nothing reported, which is the same condition as
  // `days_reported === 0` -- checking both is what lets the loaded branch treat it
  // as a number without a cast. A window with no export is not a window in which
  // the cafe took nothing.
  const d = q.data
  if (d.days_reported === 0 || d.gross_pence === null) return <NotLoaded t={d} />
  return <Loaded t={{ ...d, gross_pence: d.gross_pence }} />
}
