/**
 * The populated path — which is now the live path.
 *
 * It was written against an empty `performance` array and drove entirely off it,
 * so the first real Deliveroo export filled the screen in rather than needing it
 * rewritten. Two channels now report.
 *
 * Three rules it keeps that the empty state cannot show off:
 *  - `net_pence` is contribution after commission AND ad spend over the days
 *    that reported all three. A partial window is not silently rolled into it,
 *    so the incomplete-day count travels with the figure — Just Eat's covers 7
 *    of its 9 days and the row says so.
 *  - `days` is days that reported, not the length of the window. Nine days of a
 *    28-day window is not a monthly figure and the table must not let it read as
 *    one.
 *  - Provenance is a column, not a footnote. A hand export and a scrape are not
 *    equally trustworthy and the row says which it was.
 */
import { Card, Cell, Chip, Note, Panel, ScrollX, Sparkline, Table, Td, Th } from '../../components/ui'
import { Label } from '../../components/prim'
import { M, N, Ratio } from './figures'
import { bpPct, bpTimes, channelName, prose } from './shape'
import type { ChannelFinding, ChannelPerformance } from './shape'

function sourceChips(sources: readonly string[]) {
  if (sources.length === 0) return <Label>no source recorded</Label>
  return (
    <span className="inline-flex flex-wrap gap-1">
      {sources.map((s) => (
        <Chip key={s} tone={s.toUpperCase().startsWith('CSV') ? 'plain' : 'info'}>
          {s.replace(/_/g, ' ').toLowerCase()}
        </Chip>
      ))}
    </span>
  )
}

/** Coverage is per field: how many days of the window actually reported it. An
 *  incomplete field is why a derived figure may be missing two rows up. */
function Coverage({ row, windowDays }: { row: ChannelPerformance; windowDays: number | null }) {
  const gaps = row.coverage.filter((c) => !c.complete)
  return (
    <Panel tone={gaps.length > 0 || row.caveats.length > 0 ? 'warn' : 'plain'} title={channelName(row.channel)}>
      <p>
        <span className="fig">{row.days}</span>{' '}
        {row.days === 1 ? 'day' : 'days'} reported
        {windowDays !== null && (
          <>
            {' '}
            of the <span className="fig">{windowDays}</span>-day window
          </>
        )}
        . Contribution covers <span className="fig">{row.net_complete_days}</span>{' '}
        {row.net_complete_days === 1 ? 'day' : 'days'}
        {row.net_incomplete_days > 0 && (
          <>
            ; <span className="fig">{row.net_incomplete_days}</span> dropped whole rather than
            part-subtracted
          </>
        )}
        .
      </p>
      {gaps.length > 0 && (
        <p className="mt-1.5">
          Fields that did not report on every day:{' '}
          {gaps
            .map((c) => `${c.field_name.replace(/_/g, ' ')} missing ${c.missing} of ${row.days}`)
            .join(', ')}
          .
        </p>
      )}
      {row.caveats.map((c) => (
        <p key={c} className="mt-1.5 text-warn-ink">
          {prose(c)}
        </p>
      ))}
    </Panel>
  )
}

export function PerformanceTable({
  rows,
  windowDays,
}: {
  rows: ChannelPerformance[]
  windowDays: number | null
}) {
  const reported = [...new Set(rows.map((r) => r.days))]
  const daysPhrase =
    reported.length === 1
      ? `${reported[0]} ${reported[0] === 1 ? 'day' : 'days'}`
      : `${Math.min(...reported)}–${Math.max(...reported)} days`

  return (
    <Card
      title="Contribution by channel"
      subtitle={`After commission and after ad spend. Each row covers the ${daysPhrase} that actually reported${
        windowDays === null ? '' : ` out of a ${windowDays}-day window`
      } — a day that reported only part of the three is dropped whole, never part-subtracted.`}
      pad={false}
    >
      <div className="px-5 py-4">
        <ScrollX>
          <div className="min-w-[64rem]">
            <Table>
              <thead>
                <tr>
                  <Th>Channel</Th>
                  <Th align="right">Orders</Th>
                  <Th align="right">Gross</Th>
                  <Th align="right">Commission</Th>
                  <Th align="right">Ad spend</Th>
                  <Th align="right">Net</Th>
                  <Th align="right">ROAS</Th>
                  <Th align="right">Impressions</Th>
                  <Th align="right">Menu views</Th>
                  <Th align="right">Converts</Th>
                  <Th>Source</Th>
                  <Th align="right">Gross, by day</Th>
                </tr>
              </thead>
              <tbody>
                {rows.map((r) => (
                  <tr key={r.channel}>
                    <Td>
                      <div className="min-w-[8rem]">
                        <Cell
                          wrap
                          top={channelName(r.channel)}
                          sub={
                            r.net_incomplete_days > 0
                              ? `${r.days} days reported, ${r.net_incomplete_days} incomplete`
                              : `${r.days} days reported`
                          }
                        />
                      </div>
                    </Td>
                    <Td align="right">
                      <N v={r.orders} />
                    </Td>
                    <Td align="right">
                      <M v={r.gross_pence} />
                    </Td>
                    <Td align="right">
                      <M v={r.commission_pence} />
                      <div className="mt-0.5">
                        <Label>{bpPct(r.commission_rate_bp) ?? 'rate not reported'}</Label>
                      </div>
                    </Td>
                    <Td align="right">
                      <M v={r.ad_spend_pence} />
                    </Td>
                    <Td align="right">
                      <M
                        v={r.net_pence}
                        missing="days incomplete"
                        tone={r.net_pence !== null && r.net_pence < 0 ? 'bad' : 'plain'}
                      />
                      <div className="mt-0.5">
                        <Label>
                          {bpPct(r.net_margin_bp) ?? 'margin not computed'} over{' '}
                          {r.net_complete_days} {r.net_complete_days === 1 ? 'day' : 'days'}
                        </Label>
                      </div>
                    </Td>
                    <Td align="right">
                      <Ratio text={bpTimes(r.roas_bp)} missing="no attribution" />
                      <div className="mt-0.5">
                        <Label>
                          on <M v={r.attributed_revenue_pence} size="sm" missing="nothing" />{' '}
                          attributed
                        </Label>
                      </div>
                    </Td>
                    <Td align="right">
                      <N v={r.impressions} />
                    </Td>
                    <Td align="right">
                      <N v={r.menu_views} />
                    </Td>
                    <Td align="right">
                      <Ratio text={bpPct(r.conversion_bp)} missing="no views" />
                      <div className="mt-0.5">
                        <Label>views to orders</Label>
                      </div>
                    </Td>
                    <Td>{sourceChips(r.sources)}</Td>
                    <Td align="right">
                      {/* The reference ends each row with a trend. This one is real:
                          the days this channel actually reported, in order. A day that
                          reported no gross is skipped rather than plotted at zero --
                          a line dipping to the floor on a missing export reads as
                          "trade stopped", which is a different and wrong statement. */}
                      {(() => {
                        const days = r.daily
                          .map((d) => d.gross_pence)
                          .filter((v): v is number => v !== null)
                        if (days.length < 2) {
                          return <Label>{days.length === 0 ? 'no days' : 'one day'}</Label>
                        }
                        return (
                          <div className="ml-auto w-[7rem]">
                            <Sparkline values={days} tone="plain" h={22} />
                            <div className="text-[0.6875rem] text-ink-5 mt-0.5">
                              {days.length} days
                            </div>
                          </div>
                        )
                      })()}
                    </Td>
                  </tr>
                ))}
              </tbody>
            </Table>
          </div>
        </ScrollX>
        <div className="mt-4 grid grid-cols-[minmax(0,1fr)] gap-3 border-t border-line pt-4 lg:grid-cols-2">
          {rows.map((r) => (
            <Coverage key={r.channel} row={r} windowDays={windowDays} />
          ))}
        </div>
      </div>
    </Card>
  )
}

/** Ranks well, converts badly. The API writes the sentence; we lay out the
 *  figures beside it and change not a word of it. */
export function Findings({ findings }: { findings: ChannelFinding[] }) {
  return (
    <div className="grid grid-cols-[minmax(0,1fr)] gap-6">
      {findings.map((f) => (
        <Card
          key={f.channel}
          title={`Ranks well, converts badly — ${channelName(f.channel)}`}
          subtitle={`${f.considered} items considered: rank ${f.rank_threshold} or better, at least ${f.min_views} views.`}
          right={<Label>benchmark {bpPct(f.benchmark_bp) ?? 'not computed'}</Label>}
          pad={false}
        >
          <div className="px-5 py-4">
            {f.gaps.length === 0 ? (
              <Note>
                Nothing on this channel ranks well and converts badly. Every well-ranked item is at
                or above the channel benchmark.
              </Note>
            ) : (
              <ScrollX>
                <div className="min-w-[48rem]">
                  <Table>
                    <thead>
                      <tr>
                        <Th>Item</Th>
                        <Th align="right">Best rank</Th>
                        <Th align="right">Views</Th>
                        <Th align="right">Orders</Th>
                        <Th align="right">Converts</Th>
                        <Th align="right">Short by</Th>
                        <Th align="right">Lost orders</Th>
                        <Th align="right">Lost revenue</Th>
                      </tr>
                    </thead>
                    <tbody>
                      {f.gaps.map((g) => (
                        <tr key={g.menu_item_id}>
                          <Td>
                            <div className="min-w-[11rem] max-w-[22rem]">
                              <Cell
                                wrap
                                top={g.item_name}
                                sub={`#${g.best_rank} on the channel, over ${g.days} ${
                                  g.days === 1 ? 'day' : 'days'
                                }`}
                              />
                            </div>
                          </Td>
                          <Td align="right">
                            <N v={g.best_rank} />
                          </Td>
                          <Td align="right">
                            <N v={g.views} />
                          </Td>
                          <Td align="right">
                            <N v={g.orders} />
                          </Td>
                          <Td align="right">
                            <Ratio text={bpPct(g.conversion_bp)} />
                            <div className="mt-0.5">
                              <Label>vs {bpPct(g.benchmark_bp)}</Label>
                            </div>
                          </Td>
                          <Td align="right">
                            <Ratio text={bpPct(g.shortfall_bp)} tone="warn" />
                          </Td>
                          <Td align="right">
                            <N v={g.lost_orders} tone="warn" />
                          </Td>
                          <Td align="right">
                            <M v={g.lost_revenue_pence} missing="no price" />
                          </Td>
                        </tr>
                      ))}
                    </tbody>
                  </Table>
                </div>
              </ScrollX>
            )}
            {(f.gaps.some((g) => g.sentence !== '') || f.caveats.length > 0) && (
              <div className="mt-4 grid grid-cols-[minmax(0,1fr)] gap-3 border-t border-line pt-4">
                {/* The API's own sentence per item, verbatim. It says the thing
                    the columns only imply: good placement, poor conversion. */}
                {f.gaps
                  .filter((g) => g.sentence !== '')
                  .map((g) => (
                    <Panel key={g.menu_item_id} title={g.item_name}>
                      {prose(g.sentence)}
                    </Panel>
                  ))}
                {f.caveats.map((c) => (
                  <Panel key={c} tone="warn">
                    {prose(c)}
                  </Panel>
                ))}
              </div>
            )}
          </div>
        </Card>
      ))}
    </div>
  )
}
