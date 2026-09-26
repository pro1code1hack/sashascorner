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
import { Body, EST, Fig, Label, Marg, Qty, Sheet } from '../components/prim'
import { CountBasis, Theoretical, TheoreticalStanding } from '../components/onhand'
import { Attribution, DriftMargin, trustLabel } from '../components/attribution'
import {
  Cell,
  ErrorBox,
  Loading,
  PageHeader,
  Panel,
  SectionLabel,
  toneText,
} from '../components/ui'
import { DriftHistory } from './DriftHistory'
import { ConfirmShelfLife } from './ConfirmShelfLife'

/**
 * The row grid needs ~840px (47.5rem of track plus five gaps). It used to switch
 * on at `md:` (768px), where only ~712px is available once the sidebar and page
 * padding are taken -- so the whole page scrolled sideways at 768 and 1024, and a
 * figure in the last column was cut mid-digit. It now switches at `xl:` (1280px),
 * below which the stacked layout is used. Every `xl:` in this file belongs to that
 * one switch; they move together or not at all.
 */
const GRID =
  'xl:grid xl:grid-cols-[minmax(9rem,1.35fr)_7.5rem_minmax(11rem,1.3fr)_5.5rem_5rem_minmax(9.5rem,1fr)] xl:items-start xl:gap-x-4'

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

/**
 * A column heading for the row grid. `<Th>` from the kit is a `<th>` and this is
 * not a `<table>` — the layout has to collapse to a stacked list below `xl`,
 * which a table cannot do — so it repeats the kit's heading treatment rather
 * than editing a shared file four agents are in. Sentence case, quiet grey.
 */
function Head({
  children,
  right = false,
  className = '',
}: {
  children: React.ReactNode
  right?: boolean
  className?: string
}) {
  return (
    <span
      className={`text-[0.6875rem] font-medium text-ink-4 ${right ? 'text-right' : ''} ${className}`}
    >
      {children}
    </span>
  )
}

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
  const [openShelf, setOpenShelf] = useState<number | null>(null)

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
      <>
        <PageHeader title="Stock" />
        <Loading what="Reading stock" />
      </>
    )
  }
  if (stock.isError || !stock.data) {
    return (
      <>
        <PageHeader title="Stock" />
        <ErrorBox error={stock.error} />
      </>
    )
  }

  /* One sort at a time, and clicking the column already sorted reverses it.
     Shared by the compact control below xl and the column headers above it, so
     the two can never disagree about which way the list is pointing. */
  const applySort = (id: string, desc: boolean) => {
    const active = sorting[0]?.id === id
    setSorting([active ? { id, desc: !(sorting[0]?.desc ?? desc) } : { id, desc }])
  }

  const s = stock.data.summary
  const shortDated = rows.filter((r) => r.is_short_dated)
  const estimatedLives = rows.filter((r) => r.shelf_life.source === 'ESTIMATE').length
  const batches: { row: StockRow; batch: Batch }[] = rows.flatMap((r) =>
    r.batches.map((b) => ({ row: r, batch: b })),
  )
  batches.sort((a, b) => (a.batch.days_left ?? 1e9) - (b.batch.days_left ?? 1e9))

  return (
    <>
      <PageHeader
        title="Stock"
        lede={<>Tier A · {stock.data.rows.length} tracked ingredients.</>}
        right={<Label>as of {stamp(stock.data.as_of)}</Label>}
      />

      {/* Invariant 6, said once and plainly at the top rather than in a tooltip.
          A panel because it is the one paragraph on this screen that must not be
          skimmed past: everything below it is a projection, not a measurement. */}
      <Sheet className="mb-6">
        <Marg>invariant 6</Marg>
        <Body>
          <Panel tone="info">
            <TheoreticalStanding basis={stock.data.basis} />
          </Panel>
        </Body>
      </Sheet>

      {/* The summary is one run-in sentence with the figures set inline. It was
          a four-tile KPI row in the first draft; see DESIGN.md §7. */}
      <Sheet className="mb-7">
        <Marg>the week</Marg>
        <Body>
          <p className="max-w-[78ch] leading-[20px]">
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
              <span className="text-bad-ink">
                <Fig weight={500}>{s.unanchored}</Fig> figures have no count behind them at
                all.{' '}
              </span>
            )}
            {s.negative > 0 && (
              <span className="text-bad-ink">
                <Fig weight={500}>{s.negative}</Fig> are negative.{' '}
              </span>
            )}
          </p>

          {/* Expiry write-offs as a running cost. Two different figures, named
              apart, because they answer different questions. */}
          <div className="border-t border-line mt-3 max-w-[78ch] pt-2">
            <p className="leading-[20px]">
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
                <span className="text-bad-ink">
                  {' '}
                  {loss.uncosted.length} excluded from that total for having no price:{' '}
                  {loss.uncosted.join(', ')}.
                </span>
              )}
            </p>
            {today.data && (
              <p className="text-ink-3 mt-1 leading-[20px]">
                Separately, <Fig weight={500}>{today.data.expiry_write_offs_due}</Fig>{' '}
                {plural(today.data.expiry_write_offs_due, 'batch', 'batches')} are due to be
                written off today
                {today.data.expiry_write_offs_due > 0 ? (
                  today.data.expiry_write_offs_value_pence === null ? (
                    <>, worth an unknown amount — a batch in that set has no price</>
                  ) : (
                    <>, worth {money(today.data.expiry_write_offs_value_pence)}</>
                  )
                ) : null}
                . That is today&rsquo;s bill; the figure above is the bill already paid.
              </p>
            )}
          </div>
        </Body>
      </Sheet>

      {/* Sort. Text, not a select, and it says which way it is pointing. It
          stays visible at every width because below xl there are no column
          headers at all -- every cell carries its own label instead. */}
      <Sheet>
        <Marg>sort</Marg>
        <Body className="flex flex-wrap items-baseline gap-x-4 gap-y-1 border-b border-line pb-2">
          {SORTS.map((o) => {
            const active = sorting[0]?.id === o.id
            return (
              <button
                key={o.id}
                type="button"
                aria-pressed={active}
                className={`fig text-[0.6875rem] transition-colors ${
                  active
                    ? 'font-medium text-ink underline underline-offset-4'
                    : 'text-ink-4 hover:text-ink-2'
                }`}
                onClick={() => applySort(o.id, o.desc)}
              >
                {o.label}
                {active ? (sorting[0]?.desc ? ' \u2193' : ' \u2191') : ''}
              </button>
            )
          })}
        </Body>
      </Sheet>

      {/* Column headers, desktop only; on a phone every cell carries its own.
          Sentence case in quiet grey -- tracked-out capitals are banned -- with
          every figure column right-aligned over its figures. */}
      <Sheet>
        <Marg>{''}</Marg>
        <Body className={`${GRID} hidden xl:grid border-line-2 border-b pt-2 pb-1.5`}>
          <Head>Ingredient</Head>
          <Head right>On hand · theoretical</Head>
          <Head className="border-line-2 border-l-2 pl-3">Basis · the count behind it</Head>
          <Head right>Drift</Head>
          <Head right>Expiry</Head>
          <Head>Runs out</Head>
        </Body>
      </Sheet>

      {table.getRowModel().rows.map((r) => {
        const row = r.original
        const d = row.drift
        const flagged = d.trust_status === 'drifting' || d.trust_status === 'excluded'
        const showAttr = flagged || openAttr.has(row.ingredient_id)
        const ro = row.run_out
        const withheld = ro !== null && (ro.forecast.qty == null || ro.forecast.is_low_confidence)
        const estimatedLife = row.shelf_life.source === 'ESTIMATE'
        return (
          <Sheet key={row.ingredient_id} className="border-b border-line py-3">
            <div>
              <DriftMargin drift={d} />
            </div>
            <Body>
              <div className={GRID}>
                {/* 1. name, as the reference's first column: the identity over a
                       quieter second line. Two departures from <Cell> as it
                       ships, both for the same reason -- it truncates, and this
                       column is 180px wide at 1280:
                         - the shelf-life arithmetic sits on a third line of its
                           own rather than in `sub`, so "usable 5 d (7 − 2
                           transit)" cannot be clipped to a half-number;
                         - `whitespace-normal` is forced back on, so "Coffee
                           beans (house blend)" wraps instead of becoming
                           "Coffee beans (house …". An ingredient name is the
                           row's identity; an ellipsis in it is data loss, and
                           the data law outranks the component. */}
                <div className="min-w-0 [&_.truncate]:whitespace-normal" title={row.name}>
                  <Cell top={row.name} sub={`${row.unit} · ${row.shelf_life.storage.toLowerCase()}`} />
                  {/* The shelf-life line, and whether anybody has checked it. An
                      ESTIMATE is marked by the dotted rule, never by colour:
                      a hundred of the database's shelf lives are estimates, so
                      it is the ambient condition and not an alarm -- the same
                      rule the design law states for estimated prices. */}
                  {row.shelf_life.usable_days !== null ? (
                    <span className="mt-0.5 block text-[0.6875rem] leading-[16px] text-ink-5">
                      <span className={estimatedLife ? EST : undefined}>
                        usable {row.shelf_life.usable_days} d
                        {row.shelf_life.shelf_life_days !== null &&
                        row.shelf_life.transit_buffer_days > 0 ? (
                          <>
                            {' '}
                            ({row.shelf_life.shelf_life_days} −{' '}
                            {row.shelf_life.transit_buffer_days} transit)
                          </>
                        ) : null}
                      </span>
                      {estimatedLife && <span className="text-ink-4"> · estimate</span>}
                    </span>
                  ) : (
                    <span className="mt-0.5 block text-[0.6875rem] leading-[16px] text-ink-5">
                      no shelf life recorded
                    </span>
                  )}
                  <button
                    type="button"
                    aria-expanded={openShelf === row.ingredient_id}
                    onClick={() =>
                      setOpenShelf(openShelf === row.ingredient_id ? null : row.ingredient_id)
                    }
                    className="fig mt-1 text-[0.6875rem] text-ink-4 underline decoration-dotted underline-offset-2 transition-colors hover:text-ink-2"
                  >
                    {openShelf === row.ingredient_id
                      ? 'close'
                      : estimatedLife
                        ? '+ confirm shelf life'
                        : '+ update shelf life'}
                  </button>
                </div>

                {/* 2. the theoretical figure, left of the heavy rule */}
                <div className="mt-1 xl:mt-0 xl:text-right">
                  <Label className="xl:hidden">on hand · theoretical </Label>
                  <Theoretical oh={row.on_hand} />
                </div>

                {/* 3. the basis, right of the heavy rule, never a bare number */}
                <div className="border-line-2 mt-1 border-t border-dotted pt-1 xl:mt-0 xl:border-t-0 xl:border-l-2 xl:pt-0 xl:pl-3">
                  <CountBasis oh={row.on_hand} />
                </div>

                {/* 4. drift + trust */}
                <div className="mt-2 xl:mt-0 xl:text-right">
                  <Label className="xl:hidden">drift </Label>
                  <Fig weight={500} className={flagged ? 'text-bad-ink' : ''}>
                    {d.drift_pct === null ? 'no reading' : pct(d.drift_pct, { sign: true })}
                  </Fig>
                  <div className="xl:mt-[1px]">
                    <button
                      type="button"
                      className={`fig text-[0.6875rem] underline decoration-dotted underline-offset-2 ${toneText(trustLabel(d).tone)}`}
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
                <div className="mt-1 xl:mt-0 xl:text-right">
                  <Label className="xl:hidden">soonest expiry </Label>
                  {row.soonest_expiry_days === null ? (
                    <Fig className="text-ink-4">—</Fig>
                  ) : (
                    <Fig className={row.is_short_dated ? 'text-bad-ink' : ''}>
                      {row.soonest_expiry_days}
                      <span className="text-ink-3"> d</span>
                    </Fig>
                  )}
                </div>

                {/* 6. runs out. Invariant 9: when the forecast is
                       low-confidence there is no qty in the payload, so the
                       reason is rendered IN the figure's place. */}
                <div className="mt-1 xl:mt-0">
                  <Label className="xl:hidden">runs out </Label>
                  {ro === null ? (
                    <Fig className="text-ink-4">not computed</Fig>
                  ) : withheld ? (
                    <div>
                      <Fig weight={500} className="text-ink-3">
                        withheld
                      </Fig>
                      <p className="text-ink-3 mt-[2px] text-[0.6875rem] leading-[16px]">
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
                  className="fig text-ink-4 hover:text-ink mt-1 text-[0.6875rem]"
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

              {openShelf === row.ingredient_id && (
                <ConfirmShelfLife row={row} onClose={() => setOpenShelf(null)} />
              )}

              {openRow === row.ingredient_id && <DriftHistory ingredientId={row.ingredient_id} />}
            </Body>
          </Sheet>
        )
      })}

      {/* ---------------------------------------------------- open batches --- */}
      <Sheet className="mt-10">
        <Marg>batches</Marg>
        <Body>
          <SectionLabel right={`${batches.length} ${plural(batches.length, 'batch', 'batches')}`}>
            Open batches
          </SectionLabel>
          {batches.length === 0 ? (
            <p className="text-ink-3 mt-2">No batches are open.</p>
          ) : (
            batches.map(({ row, batch }) => (
              <div
                key={batch.batch_id}
                className="border-b border-line grid grid-cols-[minmax(0,1fr)_auto] gap-x-4 py-[6px] xl:grid-cols-[minmax(9rem,1.3fr)_4rem_7rem_7rem_6rem_5rem_6rem] xl:items-baseline"
              >
                <span className="font-[600] xl:font-[400]">{row.name}</span>
                <Fig size="sub" className="text-ink-4 text-right xl:text-left">
                  #{batch.batch_id}
                </Fig>
                <span className="xl:text-right">
                  <Qty value={batch.qty_remaining} unit={batch.unit} size="sub" weight={500} />
                </span>
                <Label>received {dayShort(batch.received_at)}</Label>
                <Label>
                  {batch.effective_expiry
                    ? `expires ${dayFull(batch.effective_expiry)}`
                    : 'no expiry set'}
                </Label>
                <span className="xl:text-right">
                  {batch.days_left === null ? (
                    <Label>—</Label>
                  ) : (
                    <Fig
                      size="sub"
                      weight={batch.days_left <= 3 ? 500 : 400}
                      className={batch.days_left <= 3 ? 'text-bad-ink' : 'text-ink-3'}
                    >
                      {batch.days_left} d left
                    </Fig>
                  )}
                </span>
                <span className="xl:text-right">
                  {batch.value_pence === null ? (
                    <Label>no price</Label>
                  ) : (
                    <Fig size="sub">{money(batch.value_pence)}</Fig>
                  )}
                </span>
              </div>
            ))
          )}

          <SectionLabel>Short-dated</SectionLabel>
          {shortDated.length === 0 ? (
            <p className="text-ink-3 mt-2 max-w-[74ch] leading-[20px]">
              Nothing is short-dated today. A batch appears here once three days or fewer
              remain on it — which on milk is most of its life, because milk&rsquo;s usable
              window is only five days to begin with.
            </p>
          ) : (
            shortDated.map((r) => (
              <div key={r.ingredient_id} className="border-b border-line py-[6px]">
                <span className="text-bad-ink font-[600]">{r.name}</span>{' '}
                <Label>
                  {r.soonest_expiry_days} d left on the soonest batch · {r.batches.length}{' '}
                  {plural(r.batches.length, 'batch', 'batches')} open
                </Label>
              </div>
            ))
          )}

          {/* What is left to do, counted from the data rather than asserted. It
              is deliberately not coloured: nearly every shelf life in the
              database is an estimate, so it is the ambient condition and an
              alarm on all of it would be an alarm on nothing. */}
          <p className="text-ink-3 mt-6 max-w-[74ch] leading-[20px]">
            <Fig weight={500}>{estimatedLives}</Fig> of these{' '}
            <Fig weight={500}>{rows.length}</Fig> tier A shelf lives are still{' '}
            <span className={EST}>ESTIMATE</span> defaults, as is nearly every one of the
            hundred-odd in the database — seeded by a script, never checked with anybody. A
            shelf life caps order size: a wrong one either wastes stock or causes a stockout.
            Milk&rsquo;s usable window is five days — seven days&rsquo; shelf life less a
            two-day transit buffer — and that cap is what sizes the milk order. The
            perishables that actually move are the ones worth confirming first, and{' '}
            <span className="text-ink-2">confirm shelf life</span> on any row above records
            what the pack or the supplier says.
          </p>
        </Body>
      </Sheet>
    </>
  )
}
