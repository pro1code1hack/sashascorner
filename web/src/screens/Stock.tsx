/**
 * Stock. Spec §10.2.
 *
 * The screen's whole job is that a theoretical figure and a counted one are
 * never confusable — see `components/onhand.tsx` for the three mechanisms —
 * and that drift is split into measurement error versus expiry loss, which have
 * opposite fixes.
 */
import { useMemo, useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import {
  createColumnHelper,
  getCoreRowModel,
  getSortedRowModel,
  useReactTable,
  type SortingState,
} from '@tanstack/react-table'
import { api } from '../lib/api'
import { cmp, mustDec, toFixed } from '../lib/dec'
import { dayFull, dayShort, daysFrom, money, moneyDec, pct, plural, stamp } from '../lib/format'
import { expiryLossSinceCount } from '../lib/expiry'
import type { Batch, StockRow } from '../lib/types'
import { Body, Fig, Label, Marg, Qty, Sheet } from '../components/prim'
import { CountBasis, Theoretical, TheoreticalStanding } from '../components/onhand'
import { Attribution, DriftMargin, trustLabel } from '../components/attribution'
import { Page, ScreenTitle } from '../components/shell'
import { DriftHistory } from './DriftHistory'

const GRID =
  'md:grid md:grid-cols-[minmax(9rem,1.35fr)_7.5rem_minmax(11rem,1.3fr)_5.5rem_5rem_minmax(9.5rem,1fr)] md:items-start md:gap-x-4'

const col = createColumnHelper<StockRow>()

/** Quantities are compared as exact decimals, never parsed to a float: the old
 *  TEXT-storage bug (ARCHITECTURE §8E) was exactly this kind of comparison
 *  going wrong quietly. */
function qtyCompare(a: string, b: string): number {
  return cmp(mustDec(a), mustDec(b))
}

const columns = [
  col.accessor((r) => r.name, { id: 'name', sortingFn: 'alphanumeric' }),
  col.accessor((r) => r.on_hand.qty, {
    id: 'on_hand',
    sortingFn: (a, b) => qtyCompare(a.original.on_hand.qty, b.original.on_hand.qty),
  }),
  col.accessor((r) => r.drift.drift_pct, {
    id: 'drift',
    sortingFn: (a, b) =>
      Math.abs(a.original.drift.drift_pct ?? -1) - Math.abs(b.original.drift.drift_pct ?? -1),
  }),
  col.accessor((r) => r.run_out?.days ?? null, {
    id: 'run_out',
    sortingFn: (a, b) => {
      const x = a.original.run_out?.days
      const y = b.original.run_out?.days
      // A withheld run-out sorts last: it is not "soon", it is unknown, and
      // putting it first would read as urgency the data cannot support.
      if (x == null && y == null) return 0
      if (x == null) return 1
      if (y == null) return -1
      return qtyCompare(x, y)
    },
  }),
  col.accessor((r) => r.soonest_expiry_days, {
    id: 'expiry',
    sortingFn: (a, b) =>
      (a.original.soonest_expiry_days ?? 1e9) - (b.original.soonest_expiry_days ?? 1e9),
  }),
]

const SORTS: { id: string; label: string; desc: boolean }[] = [
  { id: 'name', label: 'name', desc: false },
  { id: 'drift', label: 'drift', desc: true },
  { id: 'run_out', label: 'runs out', desc: false },
  { id: 'expiry', label: 'expiry', desc: false },
  { id: 'on_hand', label: 'on hand', desc: true },
]

export function Stock() {
  const stock = useQuery({ queryKey: ['stock', 'A'], queryFn: () => api.stock('A') })
  const today = useQuery({ queryKey: ['today'], queryFn: () => api.today() })
  const [sorting, setSorting] = useState<SortingState>([{ id: 'drift', desc: true }])
  const [openRow, setOpenRow] = useState<number | null>(null)
  const [openAttr, setOpenAttr] = useState<Set<number>>(new Set())

  const rows = stock.data?.rows ?? []
  const table = useReactTable({
    data: rows,
    columns,
    state: { sorting },
    onSortingChange: setSorting,
    getCoreRowModel: getCoreRowModel(),
    getSortedRowModel: getSortedRowModel(),
  })

  const loss = useMemo(() => expiryLossSinceCount(rows), [rows])

  if (stock.isPending) {
    return (
      <Page>
        <p className="text-muted mt-8">Reading stock&hellip;</p>
      </Page>
    )
  }
  if (stock.isError || !stock.data) {
    return (
      <Page>
        <p className="text-flag mt-8">
          Stock could not be read. {(stock.error as Error | undefined)?.message}
        </p>
      </Page>
    )
  }

  const s = stock.data.summary
  const shortDated = rows.filter((r) => r.is_short_dated)
  const batches: { row: StockRow; batch: Batch }[] = rows.flatMap((r) =>
    r.batches.map((b) => ({ row: r, batch: b })),
  )
  batches.sort((a, b) => (a.batch.days_left ?? 1e9) - (b.batch.days_left ?? 1e9))

  return (
    <Page>
      <ScreenTitle
        title="Stock"
        sub={
          <Label>
            tier A · {stock.data.rows.length} tracked ingredients · as of{' '}
            {stamp(stock.data.as_of)}
          </Label>
        }
      />

      <Sheet className="mb-6">
        <Marg>invariant 6</Marg>
        <Body>
          <TheoreticalStanding basis={stock.data.basis} />
        </Body>
      </Sheet>

      {/* The summary is one run-in sentence with the figures set inline. It was
          a four-tile KPI row in the first draft; see DESIGN.md §7. */}
      <Sheet className="mb-7">
        <Marg>the week</Marg>
        <Body>
          <p className="max-w-[78ch] leading-relaxed">
            <Fig weight={500}>{s.ingredients}</Fig> ingredients tracked at tier A.{' '}
            <Fig weight={500}>{s.auto_order_enabled}</Fig> have earned auto-ordering,{' '}
            <Fig weight={500}>{s.forced_manual}</Fig>{' '}
            {plural(s.forced_manual, 'is', 'are')} refused it outright, and{' '}
            <Fig weight={500}>{rows.filter((r) => r.drift.trust_status === 'drifting').length}</Fig>{' '}
            sit in the tuning band.{' '}
            <Fig weight={500}>{rows.filter((r) => r.drift.trust_status === null).length}</Fig>{' '}
            have no drift reading at all, so nothing is known about them either way — that is
            not the same as being trusted, and the fix is to count them twice.{' '}
            {s.short_dated === 0 ? (
              <>Nothing is short-dated. </>
            ) : (
              <>
                <Fig weight={500}>{s.short_dated}</Fig> short-dated.{' '}
              </>
            )}
            {s.unanchored > 0 && (
              <span className="text-flag">
                <Fig weight={500}>{s.unanchored}</Fig> figures have no count behind them at
                all.{' '}
              </span>
            )}
            {s.negative > 0 && (
              <span className="text-flag">
                <Fig weight={500}>{s.negative}</Fig> are negative.{' '}
              </span>
            )}
          </p>

          {/* Expiry write-offs as a running cost. Two different figures, named
              apart, because they answer different questions. */}
          <div className="rule-t mt-3 max-w-[78ch] pt-2">
            <p className="leading-relaxed">
              Expiry has cost{' '}
              <Fig weight={500}>{moneyDec(loss.total)}</Fig> since the last count of
              each ingredient —{' '}
              {loss.affected === 0 ? (
                <>nothing expired in any counted window.</>
              ) : (
                <>
                  {loss.affected} {plural(loss.affected, 'ingredient')} wrote stock off,{' '}
                  {loss.costed} of them priced.
                </>
              )}
              {loss.uncosted.length > 0 && (
                <span className="text-flag">
                  {' '}
                  {loss.uncosted.length} excluded from that total for having no price:{' '}
                  {loss.uncosted.join(', ')}.
                </span>
              )}
            </p>
            {today.data && (
              <p className="text-muted mt-1 leading-relaxed">
                Separately, <Fig weight={500}>{today.data.expiry_write_offs_due}</Fig>{' '}
                {plural(today.data.expiry_write_offs_due, 'batch', 'batches')} are due to be
                written off today
                {today.data.expiry_write_offs_due > 0 ? (
                  <>, worth {money(today.data.expiry_write_offs_value_pence)}</>
                ) : null}
                . That is today&rsquo;s bill; the figure above is the bill already paid.
              </p>
            )}
          </div>
        </Body>
      </Sheet>

      {/* sort control — text, not a select, and it says which way */}
      <Sheet>
        <Marg>sort</Marg>
        <Body className="rule-b flex flex-wrap items-baseline gap-x-4 gap-y-1 pb-2">
          {SORTS.map((o) => {
            const active = sorting[0]?.id === o.id
            return (
              <button
                key={o.id}
                type="button"
                className={
                  active
                    ? 'fig text-[length:var(--text-micro)] font-[500] underline underline-offset-4'
                    : 'fig text-muted hover:text-ink text-[length:var(--text-micro)]'
                }
                onClick={() =>
                  setSorting([
                    active ? { id: o.id, desc: !(sorting[0]?.desc ?? o.desc) } : { id: o.id, desc: o.desc },
                  ])
                }
              >
                {o.label}
                {active ? (sorting[0]?.desc ? ' ↓' : ' ↑') : ''}
              </button>
            )
          })}
        </Body>
      </Sheet>

      {/* column headers, desktop only; on a phone every cell carries its own */}
      <Sheet>
        <Marg>{''}</Marg>
        <Body className={`${GRID} hidden md:grid border-rule-strong border-b pt-2 pb-1`}>
          <Label>ingredient</Label>
          <Label className="text-right">on hand · theoretical</Label>
          <Label className="border-rule-strong border-l-2 pl-3">
            basis · the count behind it
          </Label>
          <Label className="text-right">drift</Label>
          <Label className="text-right">expiry</Label>
          <Label>runs out</Label>
        </Body>
      </Sheet>

      {table.getRowModel().rows.map((r, i) => {
        const row = r.original
        const d = row.drift
        const flagged = d.trust_status === 'drifting' || d.trust_status === 'excluded'
        const showAttr = flagged || openAttr.has(row.ingredient_id)
        const ro = row.run_out
        const withheld = ro !== null && (ro.forecast.qty == null || ro.forecast.is_low_confidence)
        return (
          <Sheet
            key={row.ingredient_id}
            className="settle rule-b py-3"
          >
            <div className="marg" style={{ ['--i' as string]: i }}>
              <DriftMargin drift={d} />
            </div>
            <Body style={{ ['--i' as string]: i }}>
              <div className={GRID}>
                {/* 1. name */}
                <div>
                  <div className="leading-tight font-[600]">{row.name}</div>
                  <Label>
                    {row.unit} · {row.shelf_life.storage.toLowerCase()}
                    {row.shelf_life.usable_days !== null && (
                      <>
                        {' '}
                        · usable {row.shelf_life.usable_days} d
                        {row.shelf_life.shelf_life_days !== null &&
                        row.shelf_life.transit_buffer_days > 0 ? (
                          <>
                            {' '}
                            ({row.shelf_life.shelf_life_days} − {row.shelf_life.transit_buffer_days}{' '}
                            transit)
                          </>
                        ) : null}
                      </>
                    )}
                  </Label>
                </div>

                {/* 2. the theoretical figure, left of the heavy rule */}
                <div className="mt-1 md:mt-0 md:text-right">
                  <Label className="md:hidden">on hand · theoretical </Label>
                  <Theoretical oh={row.on_hand} />
                </div>

                {/* 3. the basis, right of the heavy rule, never a bare number */}
                <div className="border-rule-strong mt-1 border-t border-dotted pt-1 md:mt-0 md:border-t-0 md:border-l-2 md:pt-0 md:pl-3">
                  <CountBasis oh={row.on_hand} />
                </div>

                {/* 4. drift + trust */}
                <div className="mt-2 md:mt-0 md:text-right">
                  <Label className="md:hidden">drift </Label>
                  <Fig weight={500} className={flagged ? 'text-flag' : ''}>
                    {d.drift_pct === null ? 'no reading' : pct(d.drift_pct, { sign: true })}
                  </Fig>
                  <div className="md:mt-[1px]">
                    <button
                      type="button"
                      className={`fig text-[length:var(--text-micro)] underline decoration-dotted underline-offset-2 ${trustLabel(d).className}`}
                      onClick={() =>
                        setOpenRow(openRow === row.ingredient_id ? null : row.ingredient_id)
                      }
                      aria-expanded={openRow === row.ingredient_id}
                    >
                      {trustLabel(d).text}
                      {d.observation_count > 0 ? ` · ${d.observation_count} counts` : ''}
                    </button>
                  </div>
                </div>

                {/* 5. soonest expiry */}
                <div className="mt-1 md:mt-0 md:text-right">
                  <Label className="md:hidden">soonest expiry </Label>
                  {row.soonest_expiry_days === null ? (
                    <Fig className="text-faint">—</Fig>
                  ) : (
                    <Fig className={row.is_short_dated ? 'text-flag' : ''}>
                      {row.soonest_expiry_days}
                      <span className="text-muted"> d</span>
                    </Fig>
                  )}
                </div>

                {/* 6. runs out. Invariant 9: when the forecast is
                       low-confidence there is no qty in the payload, so the
                       reason is rendered IN the figure's place. */}
                <div className="mt-1 md:mt-0">
                  <Label className="md:hidden">runs out </Label>
                  {ro === null ? (
                    <Fig className="text-faint">not computed</Fig>
                  ) : withheld ? (
                    <div>
                      <Fig weight={500} className="text-muted">
                        withheld
                      </Fig>
                      <p className="text-muted mt-[2px] text-[length:var(--text-micro)] leading-snug">
                        {ro.forecast.reasons[0] ?? ro.note ?? 'the forecast is low-confidence'}
                      </p>
                    </div>
                  ) : (
                    <div>
                      <Fig weight={500}>{dayShort(ro.on)}</Fig>
                      <div>
                        <Label>
                          in {daysFrom(ro.days)} d · {ro.daily_rate_qty ? (
                            <>
                              {toFixed(mustDec(ro.daily_rate_qty), 2)} {row.unit}/day
                            </>
                          ) : null}
                        </Label>
                      </div>
                    </div>
                  )}
                </div>
              </div>

              {/* attribution: shown for anything off-trust, available on the
                  rest by asking */}
              {showAttr ? (
                <Attribution drift={d} unit={row.unit} />
              ) : d.attribution || d.trust_status === null ? (
                <button
                  type="button"
                  className="fig text-faint hover:text-ink mt-1 text-[length:var(--text-micro)]"
                  onClick={() =>
                    setOpenAttr((prev) => {
                      const n = new Set(prev)
                      n.add(row.ingredient_id)
                      return n
                    })
                  }
                >
                  {d.trust_status === null
                    ? '+ why there is no reading'
                    : '+ what the drift is made of'}
                </button>
              ) : null}

              {openRow === row.ingredient_id && <DriftHistory ingredientId={row.ingredient_id} />}
            </Body>
          </Sheet>
        )
      })}

      {/* ---------------------------------------------------- open batches --- */}
      <Sheet className="mt-10">
        <Marg>batches</Marg>
        <Body>
          <h3 className="border-rule-strong border-b pb-1 text-[length:var(--text-prose)] font-[600]">
            Open batches
          </h3>
          {batches.length === 0 ? (
            <p className="text-muted mt-2">No batches are open.</p>
          ) : (
            batches.map(({ row, batch }) => (
              <div
                key={batch.batch_id}
                className="rule-b grid grid-cols-[1fr_auto] gap-x-4 py-[6px] md:grid-cols-[minmax(9rem,1.3fr)_4rem_7rem_7rem_6rem_5rem_6rem] md:items-baseline"
              >
                <span className="font-[600] md:font-[400]">{row.name}</span>
                <Fig size="sub" className="text-faint text-right md:text-left">
                  #{batch.batch_id}
                </Fig>
                <span className="md:text-right">
                  <Qty value={batch.qty_remaining} unit={batch.unit} size="sub" weight={500} />
                </span>
                <Label>received {dayShort(batch.received_at)}</Label>
                <Label>
                  {batch.effective_expiry
                    ? `expires ${dayFull(batch.effective_expiry)}`
                    : 'no expiry set'}
                </Label>
                <span className="md:text-right">
                  {batch.days_left === null ? (
                    <Label>—</Label>
                  ) : (
                    <Fig
                      size="sub"
                      weight={batch.days_left <= 3 ? 500 : 400}
                      className={batch.days_left <= 3 ? 'text-flag' : 'text-muted'}
                    >
                      {batch.days_left} d left
                    </Fig>
                  )}
                </span>
                <span className="md:text-right">
                  {batch.value_pence === null ? (
                    <Label>no price</Label>
                  ) : (
                    <Fig size="sub">{money(batch.value_pence)}</Fig>
                  )}
                </span>
              </div>
            ))
          )}

          <h3 className="mt-6 border-rule-strong border-b pb-1 text-[length:var(--text-prose)] font-[600]">
            Short-dated
          </h3>
          {shortDated.length === 0 ? (
            <p className="text-muted mt-2 max-w-[74ch] leading-relaxed">
              Nothing is short-dated today. A batch appears here once three days or fewer
              remain on it — which on milk is most of its life, because milk&rsquo;s usable
              window is only five days to begin with.
            </p>
          ) : (
            shortDated.map((r) => (
              <div key={r.ingredient_id} className="rule-b py-[6px]">
                <span className="text-flag font-[600]">{r.name}</span>{' '}
                <Label>
                  {r.soonest_expiry_days} d left on the soonest batch · {r.batches.length}{' '}
                  {plural(r.batches.length, 'batch', 'batches')} open
                </Label>
              </div>
            ))
          )}

          <p className="text-muted mt-6 max-w-[74ch] leading-relaxed">
            All 113 shelf lives in this database are <span className="est">ESTIMATE</span>{' '}
            defaults, and a shelf life caps order size: a wrong one either wastes stock or
            causes a stockout. Milk&rsquo;s usable window is five days — seven days&rsquo;
            shelf life less a two-day transit buffer — and that cap is what sizes the milk
            order. The perishables that actually move are the ones worth confirming first.
          </p>
        </Body>
      </Sheet>
    </Page>
  )
}
