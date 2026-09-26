/**
 * Every ranked item, in whichever of the two orderings the owner is reading.
 *
 * Four decisions worth stating:
 *
 *  - The ordering toggle switches between the API's OWN two orderings
 *    (`by_margin_pct`, `by_margin_per_minute`) rather than re-sorting one of
 *    them, so what is on screen is what the backend ranked. It is a
 *    `<TabsUnderline>` and not a pill filter because it changes what the
 *    section is *about* — the two orderings are two different menus — where the
 *    name box below merely narrows one list. Clicking a column header sorts
 *    within that set and says so, and "back to the ranking" undoes it.
 *  - 298 rows are paged, not silently truncated. The count shown and the count
 *    withheld are both printed: a quiet top-20 reads as the whole menu.
 *  - Below `lg` the table becomes a list of rows. A thirteen-column table on a
 *    375px screen can only be read by scrolling the item name out of sight,
 *    which is not reading.
 *  - Column heads are the kit's `<SortTh>`. This file used to carry its own
 *    copy, in tracked-out capitals, because the kit had none; it has one now.
 */
import { useMemo, useState } from 'react'
import {
  Badge,
  Button,
  Card,
  Cell,
  Note,
  ScrollX,
  SortTh,
  Table,
  TabsUnderline,
  Td,
} from '../../components/ui'
import { CostFig, Label } from '../../components/prim'
import { Money, Move, N, P, Pct, Rank, Secs } from './figures'
import { useWide } from './useWide'
import {
  DEFAULT_DIR,
  isNegative,
  isZeroValue,
  sortItems,
  type Dir,
  type Item,
  type Ordering,
  type SortKey,
} from './data'

const PAGE = 50

/** The item's own second line: size, and where its prep time came from. */
function itemSub(it: Item): string {
  const parts = [it.size_code]
  if (it.template_name) parts.push(it.template_name)
  if (it.prep_source === 'TEMPLATE_SIZE') parts.push('prep from template')
  return parts.join(' · ')
}

function Rows({ items, ordering }: { items: Item[]; ordering: Ordering }) {
  return (
    <>
      {items.map((it) => {
        const loses = isNegative(it.true_margin_pct)
        return (
          <tr key={it.menu_item_id}>
            <Td>
              <div className="min-w-[10rem] max-w-[18rem]">
                <Cell top={it.name} sub={itemSub(it)} wrap />
              </div>
            </Td>
            <Td align="right">
              <N v={it.units_sold} tone={isZeroValue(it.units_sold) ? 'muted' : 'plain'} />
            </Td>
            <Td align="right">
              <Money v={it.price_pence} />
            </Td>
            <Td align="right">
              <CostFig cost={it.cost} showSource={false} />
            </Td>
            <Td align="right">
              <Secs v={it.prep_seconds} estimate={it.prep_is_estimate} />
            </Td>
            <Td align="right">
              <P v={it.labour_cost_pence} />
            </Td>
            <Td align="right">
              <Pct v={it.margin_pct} />
            </Td>
            <Td align="right">
              <Pct v={it.true_margin_pct} tone={loses ? 'bad' : 'plain'} />
              {loses && (
                <div className="mt-0.5">
                  <Label tone="bad">loses money after labour</Label>
                </div>
              )}
            </Td>
            <Td align="right">
              <P v={it.contribution_pence} />
            </Td>
            <Td align="right">
              <P v={it.margin_per_minute_pence} suffix="/min" weight="semibold" />
            </Td>
            <Td align="right">
              <Rank n={it.margin_rank} active={ordering === 'margin_pct'} />
            </Td>
            <Td align="right">
              <Rank n={it.mpm_rank} active={ordering === 'margin_per_minute'} />
            </Td>
            <Td align="right">
              <Move n={it.rank_delta} size="sm" />
            </Td>
          </tr>
        )
      })}
    </>
  )
}

/** The same rows below `lg`, where a table cannot be read. */
function Cards({ items, ordering }: { items: Item[]; ordering: Ordering }) {
  return (
    <ul>
      {items.map((it) => {
        const loses = isNegative(it.true_margin_pct)
        return (
          <li key={it.menu_item_id} className="border-b border-line py-3 last:border-b-0">
            <div className="flex items-start justify-between gap-3">
              <Cell top={it.name} sub={itemSub(it)} wrap />
              <div className="shrink-0 text-right">
                <P v={it.margin_per_minute_pence} suffix="/min" weight="semibold" />
                <div className="mt-0.5">
                  <Rank
                    n={ordering === 'margin_pct' ? it.margin_rank : it.mpm_rank}
                    size="sm"
                    active
                  />
                </div>
              </div>
            </div>
            <dl className="mt-2 grid grid-cols-2 gap-x-4 gap-y-1 sm:grid-cols-3">
              <div className="min-w-0">
                <dt>
                  <Label>margin</Label>
                </dt>
                <dd>
                  <Pct v={it.margin_pct} size="sm" />
                </dd>
              </div>
              <div className="min-w-0">
                <dt>
                  <Label>after labour</Label>
                </dt>
                <dd>
                  <Pct v={it.true_margin_pct} size="sm" tone={loses ? 'bad' : 'plain'} />
                </dd>
              </div>
              <div className="min-w-0">
                <dt>
                  <Label>prep</Label>
                </dt>
                <dd>
                  <Secs v={it.prep_seconds} estimate={it.prep_is_estimate} size="sm" />
                </dd>
              </div>
              <div className="min-w-0">
                <dt>
                  <Label>price</Label>
                </dt>
                <dd>
                  <Money v={it.price_pence} size="sm" />
                </dd>
              </div>
              <div className="min-w-0">
                <dt>
                  <Label>cost / unit</Label>
                </dt>
                <dd>
                  <CostFig cost={it.cost} size="sm" showSource={false} />
                </dd>
              </div>
              <div className="min-w-0">
                <dt>
                  <Label>sold, window</Label>
                </dt>
                <dd>
                  <N
                    v={it.units_sold}
                    size="sm"
                    tone={isZeroValue(it.units_sold) ? 'muted' : 'plain'}
                  />
                </dd>
              </div>
            </dl>
            <div className="mt-1.5 flex flex-wrap items-baseline gap-x-3">
              <Label>rank by margin % </Label>
              <Rank n={it.margin_rank} size="sm" />
              <Label>per minute </Label>
              <Rank n={it.mpm_rank} size="sm" active />
              <Move n={it.rank_delta} size="sm" />
            </div>
          </li>
        )
      })}
    </ul>
  )
}

export function ItemsTable({
  byMarginPct,
  byPerMinute,
  rankableCount,
  windowDays,
}: {
  byMarginPct: Item[]
  byPerMinute: Item[]
  rankableCount: number
  windowDays: number
}) {
  const [ordering, setOrdering] = useState<Ordering>('margin_per_minute')
  const [sort, setSort] = useState<{ key: SortKey | null; dir: Dir }>({ key: null, dir: 'desc' })
  const [query, setQuery] = useState('')
  const [shown, setShown] = useState(PAGE)
  const wide = useWide()

  const base = ordering === 'margin_pct' ? byMarginPct : byPerMinute

  const filtered = useMemo(() => {
    const q = query.trim().toLowerCase()
    if (q === '') return base
    return base.filter((it) => it.label.toLowerCase().includes(q))
  }, [base, query])

  const ordered = useMemo(
    () => (sort.key === null ? filtered : sortItems(filtered, sort.key, sort.dir)),
    [filtered, sort],
  )

  const visible = ordered.slice(0, shown)
  const hidden = ordered.length - visible.length

  function onSort(key: SortKey) {
    setSort((s) =>
      s.key === key
        ? { key, dir: s.dir === 'asc' ? 'desc' : 'asc' }
        : { key, dir: DEFAULT_DIR[key] },
    )
  }

  /** The caret and `aria-sort` this column should carry. */
  const dirOf = (key: SortKey): Dir | null => (sort.key === key ? sort.dir : null)

  const orderingLabel =
    sort.key !== null
      ? 'sorted by the column you chose'
      : ordering === 'margin_pct'
        ? 'ordered by margin %, highest first'
        : 'ordered by margin per minute, highest first'

  /** A column head with its hint kept as a title, which the kit's `<SortTh>`
   *  does not take a prop for. */
  const head = (key: SortKey, label: string, hint?: string, align: 'left' | 'right' = 'right') => (
    <SortTh align={align} dir={dirOf(key)} onSort={() => onSort(key)}>
      <span title={hint ?? `Sort by ${label.toLowerCase()}`}>{label}</span>
    </SortTh>
  )

  return (
    <Card
      title="Every ranked item"
      subtitle={
        <>
          Showing <span className="fig">{visible.length}</span> of{' '}
          <span className="fig">{ordered.length}</span>
          {ordered.length !== rankableCount && (
            <>
              {' '}
              matching {ordered.length === 1 ? 'item' : 'items'} out of{' '}
              <span className="fig">{rankableCount}</span> ranked
            </>
          )}{' '}
          — {orderingLabel}.
          {hidden > 0 && (
            <>
              {' '}
              <span className="fig">{hidden}</span> more not shown.
            </>
          )}{' '}
          Money and margins are <strong className="font-medium text-ink-2">per unit</strong>;
          units sold cover the {windowDays}-day window.
        </>
      }
    >
      {/* The ordering is what the section is about, so it is an underline tab
          set rather than a pill filter. */}
      <div className="-mx-5 mb-4 px-5">
        <TabsUnderline
          tabs={[
            { key: 'margin_pct', label: 'By margin %' },
            { key: 'margin_per_minute', label: 'By margin per minute' },
          ]}
          active={ordering}
          onChange={(k) => {
            setOrdering(k)
            setSort({ key: null, dir: 'desc' })
          }}
        />
      </div>

      <div className="mb-3 flex flex-wrap items-center gap-2">
        <input
          type="search"
          value={query}
          onChange={(e) => {
            setQuery(e.target.value)
            setShown(PAGE)
          }}
          placeholder="Filter by name"
          aria-label="Filter items by name"
          className="w-full rounded-control border border-line-2 bg-raised px-3 py-1.5 text-[0.875rem] outline-none focus:border-brand sm:w-56"
        />
        {sort.key !== null && (
          <Button variant="ghost" onClick={() => setSort({ key: null, dir: 'desc' })}>
            Back to the ranking
          </Button>
        )}
        {query.trim() !== '' && (
          <Button variant="ghost" onClick={() => setQuery('')}>
            Clear filter
          </Button>
        )}
        <span className="ml-auto">
          <Badge tone="muted">prep times are estimates</Badge>
        </span>
      </div>

      {ordered.length === 0 ? (
        <Note>
          No ranked item&rsquo;s name contains &ldquo;{query.trim()}&rdquo;. It may be one of the
          items listed as excluded or uncosted below — those are not in either ranking.
        </Note>
      ) : (
        <>
          {wide ? (
            <ScrollX>
              <div className="min-w-[72rem]">
                <Table>
                  <thead>
                    <tr>
                      {head('name', 'Item', 'Sort by item name', 'left')}
                      {head('sold', 'Sold', `Units sold in the ${windowDays}-day window`)}
                      {head('price', 'Price')}
                      {head('cost', 'Cost', 'Ingredient cost per unit')}
                      {head('prep', 'Prep')}
                      {head('labour', 'Labour', 'Prep time at the loaded hourly rate')}
                      {head('margin', 'Margin', 'Margin before labour')}
                      {head('after_labour', 'After labour')}
                      {head(
                        'contribution',
                        'Contribution',
                        'Price less ingredient cost, per unit',
                      )}
                      {head('mpm', 'Per staff min')}
                      {head('rank_pct', 'Rank by %', 'Rank by margin %')}
                      {head('rank_mpm', 'Rank by min', 'Rank by margin per minute')}
                      {head('delta', 'Move', 'Places gained per minute against margin %')}
                    </tr>
                  </thead>
                  <tbody>
                    <Rows items={visible} ordering={ordering} />
                  </tbody>
                </Table>
              </div>
            </ScrollX>
          ) : (
            <Cards items={visible} ordering={ordering} />
          )}

          <div className="mt-4 flex flex-wrap items-center gap-2">
            {hidden > 0 ? (
              <>
                <Button onClick={() => setShown((n) => n + PAGE)}>
                  Show {Math.min(PAGE, hidden)} more
                </Button>
                <Button variant="ghost" onClick={() => setShown(ordered.length)}>
                  Show all {ordered.length}
                </Button>
                <Note>
                  <span className="fig">{hidden}</span> rows are below this point and are not on
                  screen.
                </Note>
              </>
            ) : (
              <Note>
                All <span className="fig">{ordered.length}</span> rows are on screen.
              </Note>
            )}
            {shown > PAGE && (
              <Button variant="ghost" onClick={() => setShown(PAGE)}>
                Collapse
              </Button>
            )}
          </div>
        </>
      )}
    </Card>
  )
}
