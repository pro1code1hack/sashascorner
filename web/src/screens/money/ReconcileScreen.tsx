/**
 * Money → Reconcile: does the money that should arrive actually arrive?
 * (finance.md 1.4)
 *
 * A. Card takings vs the bank. An unrecorded payout is "not recorded", never
 *    assumed to have arrived (the design filled in "arrived = expected" for every
 *    past-due day, so every unchecked day read "ok"). Card figures that are
 *    themselves bank deposits (workbook, to March 2026) are not reconciled.
 * B. Delivery apps. A month nobody uploaded is "missing, not zero". No demo
 *    figures exist.
 * C. Cash counted vs the till, with a zero line the design forgot, nothing
 *    drawn for uncounted days, and an explanation field so a discrepancy can be
 *    cleared from the shell's banner.
 */
import { useEffect, useRef, useState } from 'react'
import type { ChangeEvent } from 'react'
import { Button, ErrorBox, Loading, PageBody, PageHeader, TBody, THead, Table, Td, Th, Tr, cx } from '../../components/ui'
import { OperatorNeeded } from '../../components/shell/Operator'
import { parseDec, rescale } from '../../lib/dec'
import { useLocation } from '../../lib/router'
import { useOperator } from '../../lib/operator'
import { financeWrite, useInvalidateFinance, useReconcile } from '../../lib/finance-api'
import type { CardRow, CashRow, ChannelUploadOut, DeliveryRow, ReconcileResponse } from '../../lib/types/finance'
import {
  Caveats,
  MonthBar,
  MoneyCell,
  SaveStatus,
  committed,
  fd,
  gbp,
  pctBp,
  useFinancePeriod,
  useSaveStatus,
} from './shared'

type Save = ReturnType<typeof useSaveStatus>

export function ReconcileScreen() {
  const { period, setPeriod, months } = useFinancePeriod()
  const q = useReconcile(period)
  const save = useSaveStatus()
  const loc = useLocation()
  const focusDate = loc.query.get('date')
  return (
    <>
      <PageHeader
        title="Reconcile"
        subtitle="does the money that should arrive actually arrive?"
        saved={<SaveStatus state={save.state} />}
      />
      <MonthBar period={period} months={months} onChange={setPeriod} />
      <PageBody>
        {q.isError ? (
          <ErrorBox error={q.error} what="the reconciliation" />
        ) : !q.data ? (
          <Loading what="Checking the money" />
        ) : (
          <div className="flex flex-col gap-7">
            <Caveats items={q.data.caveats} />
            <CardSection data={q.data} save={save} />
            <DeliverySection data={q.data} save={save} />
            <CashSection data={q.data} save={save} focusDate={focusDate} />
          </div>
        )}
      </PageBody>
    </>
  )
}

const H2 = 'text-xl font-extrabold tracking-[-.01em]'
const SMALL_INPUT =
  'fig h-7 rounded-control border bg-surface px-1 text-center text-base outline-none focus-visible:border-brand focus-visible:ring-3 focus-visible:ring-brand-wash'

/* ---------------------------------------------------------------- card --- */

function SettingInput({
  value,
  width,
  label,
  parse,
  onCommit,
}: {
  value: string
  width: string
  label: string
  parse: (t: string) => number | null
  onCommit: (v: number) => Promise<boolean>
}) {
  const [text, setText] = useState(value)
  const [bad, setBad] = useState(false)
  useEffect(() => setText(value), [value])
  return (
    <input
      aria-label={label}
      inputMode="decimal"
      value={text}
      onChange={(e) => setText(e.target.value)}
      onKeyDown={(e) => e.key === 'Enter' && e.currentTarget.blur()}
      onBlur={async () => {
        const v = parse(text)
        if (v === null) return setBad(true)
        setBad(false)
        if (text === value) return
        if (!(await onCommit(v))) setText(value)
      }}
      className={cx(SMALL_INPUT, width, bad ? 'border-alert' : 'border-line-strong')}
    />
  )
}

/** "1.75" -> 175 basis points, exactly. */
function percentToBp(t: string): number | null {
  const d = parseDec(t.trim().replace(/%$/, ''))
  if (d === null || d.u < 0n || d.s > 2) return null
  const bp = Number(rescale(d, 2).u)
  return bp <= 1000 ? bp : null
}

function bpToPercent(bp: number): string {
  const whole = Math.trunc(bp / 100)
  const frac = String(bp % 100).padStart(2, '0').replace(/0+$/, '')
  return frac ? `${whole}.${frac}` : String(whole)
}

const STATUS_TEXT: Record<CardRow['status'], string> = {
  not_yet_due: 'not yet',
  not_recorded: 'not recorded',
  ok: 'ok',
  mismatch: '',
  bank_basis: '—',
}

function CardSection({ data, save }: { data: ReconcileResponse; save: Save }) {
  const refresh = useInvalidateFinance()
  const [operator] = useOperator()
  const c = data.card
  const live = c.rows.length - c.bank_basis_days
  const setting = (body: { payout_lag_working_days?: number; card_fee_bp?: number }) =>
    committed(save, () => financeWrite.settings({ ...body, operator }), refresh)
  return (
    <section aria-labelledby="card-title">
      <div className="flex flex-wrap items-baseline gap-x-3.5 gap-y-1">
        <h2 id="card-title" className={H2}>
          Card takings vs the bank
        </h2>
        <span className="flex flex-wrap items-center gap-1 text-base text-ink-2">
          Paid out after
          <SettingInput
            value={String(data.payout_lag_working_days)}
            width="w-[38px]"
            label="Working days until payout"
            parse={(t) => (/^\d{1,2}$/.test(t.trim()) && Number(t) <= 10 ? Number(t) : null)}
            onCommit={(v) => setting({ payout_lag_working_days: v })}
          />
          working days, card fees about
          <SettingInput
            value={bpToPercent(data.card_fee_bp)}
            width="w-[52px]"
            label="Card fee percent"
            parse={percentToBp}
            onCommit={(v) => setting({ card_fee_bp: v })}
          />
          %
        </span>
      </div>
      <p className="mb-2 mt-0.5 text-base text-ink-2">
        {live === 0 && c.rows.length === 0
          ? 'No card takings this month.'
          : live === 0
            ? 'These card figures are bank deposits already; there is nothing to check them against.'
            : `${gbp(c.expected_total_pence)} expected after fees · ${gbp(c.arrived_total_pence)} arrived · ` +
              `${c.mismatched_days} ${c.mismatched_days === 1 ? 'day doesn’t' : 'days don’t'} match` +
              (c.not_recorded_days ? ` · ${c.not_recorded_days} not recorded` : '')}
      </p>
      {c.rows.length > 0 && (
        <div className="max-w-[980px]">
          <Table header="sentence" minWidth={680} label="Card payouts">
            <THead>
              <tr>
                <Th>Sold on</Th>
                <Th numeric>Card on till</Th>
                <Th>Due in bank</Th>
                <Th numeric width={120}>
                  Arrived £
                </Th>
                <Th numeric>Difference</Th>
                <Th className="relative">
                  <span className="sr-only">Note</span>
                </Th>
              </tr>
            </THead>
            <TBody>
              {c.rows.map((r) => (
                <Tr key={r.sold_on} flagged={r.status === 'mismatch'}>
                  <Td>{fd(r.sold_on)}</Td>
                  <Td numeric>{gbp(r.card_pence)}</Td>
                  <Td className={cx(r.status === 'bank_basis' && 'text-ink-2')}>
                    {r.status === 'bank_basis' ? '—' : fd(r.due_on)}
                  </Td>
                  <Td>
                    {r.status === 'bank_basis' ? (
                      <span className="block text-right text-ink-2">—</span>
                    ) : (
                      <MoneyCell
                        pence={r.arrived_pence}
                        placeholder={r.status === 'not_recorded' ? 'not recorded' : 'not yet'}
                        label={`Arrived for ${fd(r.sold_on)}`}
                        onCommit={(v) => committed(save, () => financeWrite.payout(r.sold_on, v, operator), refresh)}
                      />
                    )}
                  </Td>
                  <Td
                    numeric
                    alert={r.status === 'mismatch'}
                    className={cx(r.status !== 'mismatch' && r.status !== 'ok' && 'text-ink-2')}
                  >
                    {r.status === 'mismatch' && r.diff_pence !== null ? gbp(r.diff_pence) : STATUS_TEXT[r.status]}
                  </Td>
                  <Td className="min-w-[12rem] text-sm text-ink-2">
                    {r.status === 'not_recorded' ? '' : (r.note ?? '')}
                  </Td>
                </Tr>
              ))}
            </TBody>
          </Table>
        </div>
      )}
    </section>
  )
}

/* ------------------------------------------------------------ delivery --- */

function DeliverySection({ data, save }: { data: ReconcileResponse; save: Save }) {
  const refresh = useInvalidateFinance()
  const [operator] = useOperator()
  const fileRef = useRef<HTMLInputElement | null>(null)
  const [channel, setChannel] = useState<'DELIVEROO' | 'JUST_EAT'>('DELIVEROO')
  const [report, setReport] = useState<ChannelUploadOut | null>(null)

  async function onFile(e: ChangeEvent<HTMLInputElement>) {
    const file = e.target.files?.[0]
    e.target.value = ''
    if (!file) return
    const text = await file.text()
    const r = await save.run(() => financeWrite.upload(channel, file.name, text))
    if (r.kind === 'ok') {
      setReport(r.data)
      await refresh()
    }
  }

  function put(row: DeliveryRow, field: 'gross_pence' | 'commission_pence' | 'ads_pence', v: number | null) {
    return committed(
      save,
      () =>
        financeWrite.statement(row.month, row.channel, {
          gross_pence: row.gross_pence,
          commission_pence: row.commission_pence,
          ads_pence: row.ads_pence,
          [field]: v,
          operator,
        }),
      refresh,
    )
  }

  return (
    <section aria-labelledby="delivery-title">
      <div className="flex flex-wrap items-center gap-x-3.5 gap-y-2">
        <h2 id="delivery-title" className={H2}>
          Delivery apps: what they took vs what you kept
        </h2>
        <span className="flex items-center gap-2">
          <select
            aria-label="Which app the export is from"
            value={channel}
            onChange={(e) => setChannel(e.target.value as 'DELIVEROO' | 'JUST_EAT')}
            className="h-8 rounded-control border border-line-strong bg-surface px-1 text-sm"
          >
            <option value="DELIVEROO">Deliveroo</option>
            <option value="JUST_EAT">Just Eat</option>
          </select>
          <Button size="sm" variant="outline" onClick={() => fileRef.current?.click()}>
            Upload a CSV export
          </Button>
          <input ref={fileRef} type="file" accept=".csv,text/csv" className="sr-only" tabIndex={-1} onChange={(e) => void onFile(e)} aria-label="CSV export file" />
        </span>
      </div>
      <p className="mt-1 text-sm text-ink-2">
        The partner portal's daily export (no API access). Or type a month's figures below.
      </p>
      {report && (
        <div role="status" className="mt-2 rounded-card border border-line px-3 py-2 text-sm">
          {report.unmapped.length > 0 ? (
            <>
              <strong>Refused: the columns were not recognised, and nothing was guessed.</strong>
              <pre className="mt-1 whitespace-pre-wrap text-xs text-ink-2">{report.unmapped.join('\n')}</pre>
            </>
          ) : (
            <>
              {report.inserted + report.updated} days read ({report.inserted} new, {report.updated} updated),{' '}
              {report.rejected.length} rejected.
              <Caveats items={[...report.rejected, ...report.notes]} className="mt-1" />
            </>
          )}
        </div>
      )}
      {data.delivery.length === 0 ? (
        <p className="mt-2 text-base text-ink-2">No delivery-app figures for any month yet.</p>
      ) : (
        <div className="mt-2 max-w-[980px]">
          <Table header="sentence" minWidth={720} label="Delivery apps by month">
            <THead>
              <tr>
                <Th>Month</Th>
                <Th>App</Th>
                <Th numeric width={110}>
                  Customers paid
                </Th>
                <Th numeric width={110}>
                  Commission
                </Th>
                <Th numeric width={110}>
                  Ads
                </Th>
                <Th numeric>You kept</Th>
                <Th numeric>Kept</Th>
              </tr>
            </THead>
            <TBody>
              {data.delivery.map((r) => (
                <Tr key={`${r.month}-${r.channel}`}>
                  <Td className={cx(!r.present && 'text-ink-2')}>{r.label}</Td>
                  <Td className={cx(!r.present && 'text-ink-2')}>{r.channel_label}</Td>
                  {(['gross_pence', 'commission_pence', 'ads_pence'] as const).map((f) => (
                    <Td key={f}>
                      <MoneyCell
                        pence={r[f]}
                        placeholder={r.present ? 'not reported' : 'missing'}
                        label={`${r.channel_label} ${r.label} ${f.replace('_pence', '')}`}
                        onCommit={(v) => put(r, f, v)}
                      />
                    </Td>
                  ))}
                  <Td numeric title={r.kept_pence === null && r.present ? 'commission or ads not reported' : undefined} className={cx(r.kept_pence === null && 'text-ink-2')}>
                    {r.kept_pence === null ? (r.present ? '—' : 'not uploaded') : gbp(r.kept_pence)}
                  </Td>
                  <Td numeric className="text-ink-2">
                    {r.kept_bp === null ? '—' : pctBp(r.kept_bp)}
                  </Td>
                </Tr>
              ))}
            </TBody>
          </Table>
          <p className="mt-1 text-sm text-ink-2">Blank months are missing, not zero.</p>
        </div>
      )}
    </section>
  )
}

/* ---------------------------------------------------------------- cash --- */

function CashChart({ rows, tolerance }: { rows: CashRow[]; tolerance: number }) {
  const counted = rows.filter((r) => r.diff_pence !== null)
  if (counted.length === 0) return null
  const vmax = Math.max(100, ...counted.map((r) => Math.abs(r.diff_pence ?? 0)))
  const n = rows.length
  const W = 300
  const H = 90
  const mid = H / 2
  const gap = n > 20 ? 1.5 : 4
  // A lone day must not become a full-width slab: bars are at most 12 units wide.
  const bw = Math.min(12, (W - gap * (n - 1)) / n)
  return (
    <>
      <svg role="img" aria-label="Cash counted minus the till, per day" viewBox={`0 0 ${W} ${H}`} preserveAspectRatio="none" className="block h-[90px] w-full max-w-[980px]">
        <line x1={0} x2={W} y1={mid} y2={mid} className="stroke-line-strong" strokeWidth={1} vectorEffect="non-scaling-stroke" />
        {rows.map((r, i) => {
          if (r.diff_pence === null) return null // not counted: nothing drawn
          const d = r.diff_pence
          const h = Math.max(1, (Math.abs(d) / vmax) * (mid - 2))
          return (
            <rect key={r.date} x={i * (bw + gap)} y={d >= 0 ? mid - h : mid} width={bw} height={h} className={Math.abs(d) > tolerance ? 'fill-alert' : 'fill-brand'}>
              <title>{`${fd(r.date)}: ${gbp(d)}`}</title>
            </rect>
          )
        })}
      </svg>
      <p className="mb-2.5 mt-0.5 text-xs text-ink-2">bars above the line: more cash than the till says; below: less</p>
    </>
  )
}

function CashSection({ data, save, focusDate }: { data: ReconcileResponse; save: Save; focusDate: string | null }) {
  const refresh = useInvalidateFinance()
  const [operator] = useOperator()
  const c = data.cash
  const tol = data.cash_tolerance_pence
  useEffect(() => {
    if (!focusDate) return
    // Scroll the page body only: scrollIntoView would also scroll the shell's
    // frame, which is height-locked and must not move.
    const el = document.getElementById(`cash-${focusDate}`)
    let box: HTMLElement | null = el?.parentElement ?? null
    const scrolls = (b: HTMLElement) =>
      /auto|scroll/.test(getComputedStyle(b).overflowY) && b.scrollHeight > b.clientHeight
    while (box && !scrolls(box)) box = box.parentElement
    if (el && box) {
      const top = el.getBoundingClientRect().top - box.getBoundingClientRect().top + box.scrollTop
      box.scrollTo({ top: Math.max(0, top - box.clientHeight / 2) })
    }
  }, [focusDate, c.rows.length])
  return (
    <section aria-labelledby="cash-title">
      <h2 id="cash-title" className={H2}>
        Cash: counted vs the till
      </h2>
      <p className="mb-2 mt-0.5 text-base text-ink-2">
        {c.days_with_cash === 0
          ? ''
          : `${c.days_with_cash} ${c.days_with_cash === 1 ? 'day' : 'days'} with cash · net difference ${gbp(c.net_diff_pence)} · ` +
            `${c.days_out} ${c.days_out === 1 ? 'day' : 'days'} over ${gbp(tol)} out`}
      </p>
      {operator === null && c.rows.length > 0 && (
        <div className="mb-2">
          <OperatorNeeded what="record a cash count" />
        </div>
      )}
      {c.rows.length === 0 ? (
        <p className="py-2.5 text-base text-ink-2">No cash taken this month.</p>
      ) : (
        <>
          <CashChart rows={c.rows} tolerance={tol} />
          <div className="max-w-[980px]">
            <Table header="sentence" minWidth={640} label="Cash counts">
              <THead>
                <tr>
                  <Th>Day</Th>
                  <Th numeric>Till says</Th>
                  <Th numeric width={120}>
                    Counted £
                  </Th>
                  <Th numeric>Difference</Th>
                  <Th className="relative">
                    <span className="sr-only">Note</span>
                  </Th>
                </tr>
              </THead>
              <TBody>
                {c.rows.map((r) => {
                  const out = r.status === 'out'
                  return (
                    <Tr key={r.date} flagged={out && r.explanation === null} selected={focusDate === r.date && !out}>
                      <Td>
                        <span id={`cash-${r.date}`}>{fd(r.date)}</span>
                      </Td>
                      <Td numeric>{r.till_pence === null ? '—' : gbp(r.till_pence)}</Td>
                      <Td>
                        <MoneyCell
                          pence={r.counted_pence}
                          placeholder="not counted"
                          disabled={operator === null}
                          label={`Cash counted on ${fd(r.date)}`}
                          onCommit={(v) => committed(save, () => financeWrite.cashCount(r.date, v, operator), refresh)}
                        />
                      </Td>
                      <Td numeric alert={out} strong={out} className={cx(!out && 'text-ink-2')}>
                        {r.diff_pence === null
                          ? r.status === 'no_till_figure'
                            ? 'no till figure'
                            : '—'
                          : r.diff_pence === 0
                            ? 'spot on'
                            : gbp(r.diff_pence)}
                      </Td>
                      <Td>
                        {out ? (
                          <Explain row={r} save={save} operator={operator} tolerance={tol} />
                        ) : (
                          <span className="text-sm text-ink-2">{r.counted_by ? `counted by ${r.counted_by}` : ''}</span>
                        )}
                      </Td>
                    </Tr>
                  )
                })}
              </TBody>
            </Table>
          </div>
        </>
      )}
    </section>
  )
}

function Explain({ row, save, operator, tolerance }: { row: CashRow; save: Save; operator: string | null; tolerance: number }) {
  const refresh = useInvalidateFinance()
  const [text, setText] = useState(row.explanation ?? '')
  useEffect(() => setText(row.explanation ?? ''), [row.explanation])
  const explained = row.explanation !== null
  return (
    <input
      aria-label={`Why cash was out on ${fd(row.date)}`}
      title={explained && row.explained_by ? `explained by ${row.explained_by}` : undefined}
      placeholder={`over ${gbp(tolerance)} out: why? (e.g. float not topped up)`}
      value={text}
      disabled={operator === null}
      onChange={(e) => setText(e.target.value)}
      onKeyDown={(e) => e.key === 'Enter' && e.currentTarget.blur()}
      onBlur={() => {
        const t = text.trim()
        if (!t || t === row.explanation || operator === null) return
        void committed(save, () => financeWrite.explainCash(row.date, t, operator), refresh)
      }}
      className={cx(
        'h-7 w-full min-w-0 rounded-control border bg-surface px-1.5 text-base outline-none placeholder:text-ink-2 focus-visible:border-brand focus-visible:ring-3 focus-visible:ring-brand-wash',
        explained ? 'border-line-strong' : 'border-alert',
      )}
    />
  )
}
