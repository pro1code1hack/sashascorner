/**
 * The reason this screen exists.
 *
 * `orderings_agree: false` means ranking the menu by margin % and ranking it by
 * margin per minute of staff time produce different menus. A 89%-margin
 * milkshake that takes two and a half minutes earns 150p a staff minute; a
 * 45%-margin muffin that takes thirty seconds earns 288p. One of those two
 * orderings is the one that pays the wages.
 *
 * The payload names the underrated side itself (`biggest_disagreements`, all of
 * them positive moves). The flattered side is taken from `ranked`, which
 * carries all 298 deltas — labelled as derived, because the difference between
 * "the API said this" and "this screen worked it out" is worth keeping.
 */
import { Badge, Card, Cell, Note, ScrollX, Table, Td, Th } from '../../components/ui'
import { Label } from '../../components/prim'
import { Move, P, Pct, Rank, Secs, Track, TrackKey } from './figures'
import { useWide } from './useWide'
import type { Derived } from './data'
import type { RankedRow } from '../../lib/types'

/** Whose finding this is: the API named one side, this screen derived the other. */
function Head({ title, derived }: { title: string; derived: boolean }) {
  return (
    <div className="mb-2 flex flex-wrap items-baseline justify-between gap-2">
      <h3 className="font-semibold text-ink">{title}</h3>
      <Badge tone={derived ? 'muted' : 'info'}>
        {derived ? 'derived from ranked[]' : 'named by the API'}
      </Badge>
    </div>
  )
}

function Side({
  title,
  lede,
  rows,
  total,
  derived,
}: {
  title: string
  lede: string
  rows: RankedRow[]
  total: number
  derived: boolean
}) {
  return (
    <div className="min-w-0">
      <Head title={title} derived={derived} />
      <Note>{lede}</Note>

      <ul className="mt-3">
        {rows.map((r) => (
          <li key={r.menu_item_id} className="border-b border-line py-2.5 last:border-b-0">
            <div className="flex flex-wrap items-baseline justify-between gap-x-3 gap-y-1">
              <div className="min-w-0 flex-1">
                <Cell top={r.label} wrap />
              </div>
              <Move n={r.rank_delta} />
            </div>

            <div className="mt-1.5 flex items-center gap-3">
              <Rank n={r.margin_rank} size="sm" />
              <Track from={r.margin_rank} to={r.margin_per_minute_rank} total={total} />
              <Rank n={r.margin_per_minute_rank} size="sm" active />
            </div>

            <div className="mt-1.5 flex flex-wrap items-baseline gap-x-4 gap-y-0.5">
              <span>
                <Label>margin </Label>
                <Pct v={r.margin_pct} size="sm" />
              </span>
              <span>
                <Label>per staff minute </Label>
                <P v={r.margin_per_minute_pence} suffix="/min" size="sm" />
              </span>
              <span>
                <Label>prep </Label>
                <Secs v={r.prep_seconds} estimate size="sm" />
              </span>
            </div>
          </li>
        ))}
      </ul>
    </div>
  )
}

/** The same twelve items as a table, for the wide screens where a column of
 *  ranks is easier to compare than a column of cards. */
function SideTable({ rows, total }: { rows: RankedRow[]; total: number }) {
  return (
    <ScrollX>
      <div className="min-w-[46rem]">
        <Table>
          <thead>
            <tr>
              <Th>Item</Th>
              <Th align="right">By margin %</Th>
              <Th align="right">Per minute</Th>
              <Th align="right">Move</Th>
              <Th>Where it sits</Th>
              <Th align="right">Margin</Th>
              <Th align="right">Per staff min</Th>
              <Th align="right">Prep</Th>
            </tr>
          </thead>
          <tbody>
            {rows.map((r) => (
              <tr key={r.menu_item_id}>
                <Td>
                  <div className="min-w-[11rem]">
                    <Cell top={r.label} wrap />
                  </div>
                </Td>
                <Td align="right">
                  <Rank n={r.margin_rank} />
                </Td>
                <Td align="right">
                  <Rank n={r.margin_per_minute_rank} active />
                </Td>
                <Td align="right">
                  <Move n={r.rank_delta} />
                </Td>
                <Td className="w-[10rem] min-w-[8rem]">
                  <Track from={r.margin_rank} to={r.margin_per_minute_rank} total={total} />
                </Td>
                <Td align="right">
                  <Pct v={r.margin_pct} />
                </Td>
                <Td align="right">
                  <P v={r.margin_per_minute_pence} suffix="/min" />
                </Td>
                <Td align="right">
                  <Secs v={r.prep_seconds} estimate />
                </Td>
              </tr>
            ))}
          </tbody>
        </Table>
      </div>
    </ScrollX>
  )
}

const HIDING = 'Margin % is hiding these'
const FLATTERING = 'Margin % is flattering these'
const HIDING_LEDE =
  'Low margin %, fast to make. Each earns more per minute of the barista\u2019s time than its margin suggests, so a menu ordered by margin % pushes it down.'
const FLATTERING_LEDE =
  'High margin %, slow to make. The margin is real, but it arrives a minute and a half at a time \u2014 and the barista cannot make two at once.'

/** Wide: two tables, because a column of ranks is what makes a move comparable. */
function Sides({ derived, total }: { derived: Derived; total: number }) {
  return (
    <div className="mt-4 grid grid-cols-[minmax(0,1fr)] gap-7 [&>*]:min-w-0">
      <div className="min-w-0">
        <Head title={HIDING} derived={false} />
        <Note>{HIDING_LEDE}</Note>
        <div className="mt-2">
          <SideTable rows={derived.underrated} total={total} />
        </div>
      </div>
      <div className="min-w-0">
        <Head title={FLATTERING} derived />
        <Note>{FLATTERING_LEDE}</Note>
        <div className="mt-2">
          <SideTable rows={derived.flattered} total={total} />
        </div>
      </div>
    </div>
  )
}

/** Narrow: the same two sets as rows, one item per block. */
function SideCards({ derived, total }: { derived: Derived; total: number }) {
  return (
    <div className="mt-4 grid grid-cols-[minmax(0,1fr)] gap-7 [&>*]:min-w-0">
      <Side
        title={HIDING}
        lede={HIDING_LEDE}
        rows={derived.underrated}
        total={total}
        derived={false}
      />
      <Side
        title={FLATTERING}
        lede={FLATTERING_LEDE}
        rows={derived.flattered}
        total={total}
        derived
      />
    </div>
  )
}

export function Disagreement({
  derived,
  agree,
  total,
}: {
  derived: Derived
  agree: boolean
  total: number
}) {
  const wide = useWide()

  if (agree) {
    return (
      <Card title="The two orderings agree">
        <Note>
          Ranking by margin % and by margin per minute put the menu in the same order this
          window, so either reading gives the same answer. That is unusual — it normally means
          prep times are near-identical across the menu.
        </Note>
      </Card>
    )
  }

  return (
    <Card
      title="The two orderings disagree"
      subtitle="How far the same menu pulls apart when the barista's time is counted."
      right={<Badge tone="warn">orderings_agree: false</Badge>}
    >
      {/* The sentence sits in the body rather than the card header: at 375px a
          shrink-0 badge leaves the header about 180px for prose, and a
          three-clause sentence set in a 180px column is not readable. */}
      <div className="mb-3">
        <Note>
          Across <span className="fig">{total}</span> ranked items,{' '}
          <span className="fig">{derived.betterPerMinute}</span> rank at least 50 places better per
          minute of staff time than by margin %, and{' '}
          <span className="fig">{derived.worsePerMinute}</span> rank at least 50 places worse. The
          largest single move is <span className="fig">{derived.maxMove}</span> places.
        </Note>
      </div>

      <TrackKey />

      {wide ? <Sides derived={derived} total={total} /> : <SideCards derived={derived} total={total} />}
    </Card>
  )
}
