/**
 * The consequences pane: what the template currently costs and earns, one size
 * at a time.
 *
 * Two things about the shape. First, the size is a tab rather than a stack of
 * five sizes' worth of figures crammed into a 16rem column — switching size is a
 * change of *what you are looking at*, which is what the underline tabs mean
 * everywhere else in this application. Second, every grid track here is
 * `minmax(0,…)` and every figure is allowed the full width of its card: a bare
 * `1fr` has a min-content floor, and the floor of a ten-digit exact figure is
 * what sliced one of these numbers mid-digit in the previous build.
 *
 * The figure rules are unchanged and they outrank the layout:
 *  - a missing cost reads "no price", never 0 (invariant 8),
 *  - an estimate is marked by the dotted rule and never by colour — 42 of 113
 *    ingredient prices are estimates, so it is the ambient condition,
 *  - the cost keeps its exact tail, truncated at 2dp with the remainder printed
 *    subordinate, so no rounding hides anywhere.
 */
import { useMemo, useState } from 'react'
import { cmp, mustDec } from '../../lib/dec'
import { money, pct, pence, plural } from '../../lib/format'
import type { MenuItemRow, TemplateDetailResponse } from '../../lib/types'
import { EST, Exact, Fig } from '../../components/prim'
import { Note, Panel, SectionLabel, StatCard, TabsUnderline } from '../../components/ui'

interface SizeStat {
  size: string
  itemCount: number
  /** Exact decimal strings of pence; lo === hi when every item of the size
   *  resolves to the same cost, which is the normal case. */
  costLo: string | null
  costHi: string | null
  costMissing: number
  costEstimated: number
  marginLo: number | null
  marginHi: number | null
  trueLo: number | null
  trueHi: number | null
  mpmLo: string | null
  mpmHi: string | null
  prepSeconds: number | null
  prepOverridden: boolean
}

function minMaxDec(values: string[]): [string | null, string | null] {
  let lo: string | null = null
  let hi: string | null = null
  for (const v of values) {
    if (lo === null || cmp(mustDec(v), mustDec(lo)) < 0) lo = v
    if (hi === null || cmp(mustDec(v), mustDec(hi)) > 0) hi = v
  }
  return [lo, hi]
}

function minMaxNum(values: (number | null)[]): [number | null, number | null] {
  const nums = values.filter((v): v is number => v !== null)
  if (nums.length === 0) return [null, null]
  return [Math.min(...nums), Math.max(...nums)]
}

function sizeStats(detail: TemplateDetailResponse): SizeStat[] {
  return detail.template.sizes.map((size) => {
    const items = detail.items.filter((i) => i.size_code === size)
    const costed = items.filter((i) => !i.cost.is_missing && i.cost.pence !== null)
    const [costLo, costHi] = minMaxDec(costed.map((i) => i.cost.pence as string))
    const [marginLo, marginHi] = minMaxNum(items.map((i) => i.margin_pct))
    const [trueLo, trueHi] = minMaxNum(items.map((i) => i.true_margin_pct))
    const [mpmLo, mpmHi] = minMaxDec(
      items.map((i) => i.margin_per_minute_pence).filter((v): v is string => v !== null),
    )
    const templatePrep = detail.template.prep_seconds_by_size[size] ?? null
    return {
      size,
      itemCount: items.length,
      costLo,
      costHi,
      costMissing: items.length - costed.length,
      costEstimated: items.filter((i) => i.cost.is_estimate).length,
      marginLo,
      marginHi,
      trueLo,
      trueHi,
      mpmLo,
      mpmHi,
      prepSeconds: templatePrep,
      prepOverridden: items.some((i) => i.prep_source === 'MENU_ITEM'),
    }
  })
}

/**
 * The cost of one size. A single value keeps its exact tail; a spread prints
 * both ends and steps down one size so fourteen characters still fit the card
 * rather than being clipped by it.
 */
function CostValue({
  lo,
  hi,
  estimated,
}: {
  lo: string | null
  hi: string | null
  estimated: boolean
}) {
  if (lo === null || hi === null) return <Fig missing="no price" />
  const same = cmp(mustDec(lo), mustDec(hi)) === 0
  if (!same) {
    return (
      <span className={estimated ? EST : undefined}>
        <Fig size="lg">
          {money(lo)}
          <span className="text-ink-3">–</span>
          {money(hi)}
        </Fig>
      </span>
    )
  }
  const { head, tail } = pence(lo)
  return (
    <span className={estimated ? EST : undefined}>
      <Exact head={head} tail={tail} suffix="p" size="xl" weight="semibold" />
    </span>
  )
}

function PctValue({ lo, hi }: { lo: number | null; hi: number | null }) {
  if (lo === null) return <Fig missing="not costed" />
  if (lo === hi) return <Fig size="xl">{pct(lo)}</Fig>
  return (
    <Fig size="lg">
      {pct(lo)}
      <span className="text-ink-3">–</span>
      {pct(hi)}
    </Fig>
  )
}

function MoneyRange({ lo, hi }: { lo: string | null; hi: string | null }) {
  if (lo === null) return <Fig missing="no prep time" />
  const spread = hi !== null && cmp(mustDec(lo), mustDec(hi)) !== 0
  if (!spread) return <Fig size="xl">{money(lo)}</Fig>
  return (
    <Fig size="lg">
      {money(lo)}
      <span className="text-ink-3">–</span>
      {money(hi as string)}
    </Fig>
  )
}

export function Consequences({ detail }: { detail: TemplateDetailResponse }) {
  const stats = useMemo(() => sizeStats(detail), [detail])
  const [picked, setPicked] = useState<string | null>(null)
  /* Falls back rather than being reset: switching template changes the size
     ladder, and a stale selection would otherwise show an empty pane. */
  const s = stats.find((x) => x.size === picked) ?? stats[0]

  const worst = useMemo(() => {
    let w: MenuItemRow | null = null
    for (const i of detail.items) {
      if (i.true_margin_pct === null) continue
      if (w === null || i.true_margin_pct < (w.true_margin_pct ?? Infinity)) w = i
    }
    return w
  }, [detail])

  if (!s) return null

  return (
    <div className="min-w-0">
      <SectionLabel right={`${detail.items.length} ${plural(detail.items.length, 'item')}`}>
        Consequences
      </SectionLabel>

      <TabsUnderline
        size="sm"
        active={s.size}
        onChange={setPicked}
        tabs={stats.map((x) => ({
          key: x.size,
          label: (
            <span className="inline-flex items-baseline gap-1">
              <span>Size {x.size}</span>
              {x.costMissing > 0 && (
                <span className="text-bad-ink" aria-label="has items with no price">
                  ·
                </span>
              )}
            </span>
          ),
        }))}
      />

      <div className="mt-3 text-[0.6875rem] text-ink-4">
        <span className="fig">{s.itemCount}</span> {plural(s.itemCount, 'item')} at this size
      </div>

      <div className="mt-3 grid min-w-0 gap-3 sm:grid-cols-2 xl:grid-cols-1">
        <StatCard
          label="Ingredient cost"
          value={<CostValue lo={s.costLo} hi={s.costHi} estimated={s.costEstimated > 0} />}
          sub={
            s.costEstimated > 0
              ? `${s.costEstimated} of ${s.itemCount} priced from estimates`
              : undefined
          }
        />
        <StatCard
          label="Margin"
          value={<PctValue lo={s.marginLo} hi={s.marginHi} />}
          sub="before labour"
        />
        <StatCard
          label="Margin after labour"
          value={<PctValue lo={s.trueLo} hi={s.trueHi} />}
          sub="£14.50/hr loaded"
        />
        <StatCard
          label="Per staff minute"
          value={<MoneyRange lo={s.mpmLo} hi={s.mpmHi} />}
          sub="contribution ÷ prep minutes"
        />
        <StatCard
          label="Prep"
          value={
            s.prepSeconds === null ? (
              <Fig missing="not timed" />
            ) : (
              <span className={detail.template.prep_seconds_is_estimate ? EST : undefined}>
                <Fig size="xl">
                  {s.prepSeconds}
                  <span className="text-ink-3 text-[0.65em]"> s</span>
                </Fig>
              </span>
            )
          }
          sub={
            [
              detail.template.prep_seconds_is_estimate ? 'estimated' : null,
              s.prepOverridden ? 'one item sets its own' : null,
            ]
              .filter(Boolean)
              .join(' · ') || undefined
          }
        />
      </div>

      {s.costMissing > 0 && (
        <div className="mt-3">
          <Note tone="bad">
            {s.costMissing} of {s.itemCount} have no price and are excluded from the figures
            above.
          </Note>
        </div>
      )}

      {worst && (
        <div className="mt-3 grid min-w-0 gap-3">
          <StatCard
            label="Lowest margin after labour"
            value={<Fig size="xl">{pct(worst.true_margin_pct)}</Fig>}
            sub={
              <>
                <span className="text-ink-3">
                  {worst.name} {worst.size_code}
                </span>{' '}
                · any size · on {pct(worst.margin_pct)} before labour · {worst.prep_seconds ?? '—'} s prep
                {worst.prep_source === 'MENU_ITEM' ? ', set on the item not the template' : ''}
              </>
            }
          />
        </div>
      )}

      {detail.warnings.map((w) => (
        <div key={w} className="mt-3">
          <Panel tone="muted">{w}</Panel>
        </div>
      ))}
    </div>
  )
}
