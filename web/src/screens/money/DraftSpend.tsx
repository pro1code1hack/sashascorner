/**
 * What the next round of ordering would cost, per supplier — and the insistence
 * that it is a draft.
 *
 * `writes_nothing` is true on this response. No endpoint in the API creates,
 * confirms or sends a purchase order (invariant 1): the draft is computed, read,
 * and then confirmed by a human in Telegram. So this is not spend. It is the
 * shape of spend that is about to be proposed, and the difference matters if
 * anybody ever reconciles this screen against a bank statement.
 *
 * The one derived figure here — the column sums — is added as exact integers by
 * `lib/dec.ts` and then checked against the API's own `total_pence`. If the two
 * ever disagree the screen says so rather than quietly showing its own.
 */
import { cmp, fromInt, sub, sum } from '../../lib/dec'
import type { Dec } from '../../lib/dec'
import { Badge, Card, Cell, Chip, Note, Panel, ScrollX, SectionLabel, Table, Td, Th } from '../../components/ui'
import { Label } from '../../components/prim'
import type { OrdersDraftResponse, SupplierOrder } from '../../lib/types'
import { N, P, PD, prose } from './figures'

function shortfall(o: SupplierOrder): Dec {
  return sub(fromInt(o.supplier.min_order_pence), fromInt(o.total_pence))
}

/** What the system decided about this supplier's order. Colour only where it
 *  classified something: a supplier with lines that cannot be placed is a warn,
 *  a supplier with nothing needed is not a problem at all. */
function Status({ o }: { o: SupplierOrder }) {
  if (o.lines.length === 0) return <Badge tone="muted">nothing needed</Badge>
  if (!o.meets_minimum) return <Badge tone="warn">under minimum</Badge>
  if (o.min_order_topped_up) return <Badge tone="info">topped up to minimum</Badge>
  return <Badge tone="ok">meets minimum</Badge>
}

export function DraftSpend({ draft }: { draft: OrdersDraftResponse }) {
  const orders = [...draft.suppliers].sort((a, b) => b.total_pence - a.total_pence)
  const withLines = orders.filter((o) => o.lines.length > 0)

  const subtotals = sum(orders.map((o) => fromInt(o.subtotal_pence)))
  const fees = sum(orders.map((o) => fromInt(o.delivery_fee_pence)))
  const totals = sum(orders.map((o) => fromInt(o.total_pence)))
  const reconciles = cmp(totals, fromInt(draft.total_pence)) === 0

  return (
    <Card
      title="Draft spend by supplier"
      subtitle={`Computed for ${draft.order_date}. One order per supplier, each with its own terms.`}
      right={<Badge tone="info">{draft.writes_nothing ? 'writes nothing' : 'draft'}</Badge>}
      pad={false}
    >
      <div className="px-5 py-4">
        <ScrollX>
          <div className="min-w-[46rem]">
            <Table>
              <thead>
                <tr>
                  <Th>Supplier</Th>
                  <Th align="right">Lines</Th>
                  <Th align="right">Subtotal</Th>
                  <Th align="right">Delivery</Th>
                  <Th align="right">Total</Th>
                  <Th align="right">Minimum</Th>
                  <Th>State</Th>
                </tr>
              </thead>
              <tbody>
                {orders.map((o) => {
                  const quiet = o.lines.length === 0
                  return (
                    <tr key={o.supplier.supplier_id}>
                      <Td>
                        <div className="min-w-[9rem]">
                          <Cell
                            wrap
                            top={o.supplier.name}
                            sub={
                              <>
                                {o.supplier.terms_are_placeholders && (
                                  <span className="text-warn-ink">invented terms</span>
                                )}
                                {o.supplier.terms_are_placeholders && o.skipped.length > 0 && ' · '}
                                {o.skipped.length > 0 && (
                                  <>
                                    {o.skipped.length} candidate
                                    {o.skipped.length === 1 ? '' : 's'} deliberately not ordered
                                  </>
                                )}
                              </>
                            }
                          />
                        </div>
                      </Td>
                      <Td align="right">
                        <N v={o.lines.length} tone={quiet ? 'muted' : 'plain'} />
                      </Td>
                      <Td align="right">
                        <P v={o.subtotal_pence} tone={quiet ? 'muted' : 'plain'} />
                      </Td>
                      <Td align="right">
                        {o.delivery_fee_pence === 0 ? (
                          <Label>{quiet ? '—' : 'waived'}</Label>
                        ) : (
                          <P v={o.delivery_fee_pence} />
                        )}
                      </Td>
                      <Td align="right">
                        <P
                          v={o.total_pence}
                          size={quiet ? 'base' : 'lg'}
                          tone={quiet ? 'muted' : 'plain'}
                        />
                      </Td>
                      <Td align="right">
                        {o.supplier.min_order_pence === 0 ? (
                          <Label>none</Label>
                        ) : (
                          <P
                            v={o.supplier.min_order_pence}
                            tone={o.supplier.terms_are_placeholders ? 'warn' : 'plain'}
                          />
                        )}
                      </Td>
                      <Td>
                        <Status o={o} />
                      </Td>
                    </tr>
                  )
                })}
              </tbody>
              <tfoot>
                <tr>
                  <Td>
                    <span className="font-medium text-ink">
                      {withLines.length} of {orders.length} suppliers
                    </span>
                  </Td>
                  <Td align="right">
                    <N v={withLines.reduce((n, o) => n + o.lines.length, 0)} />
                  </Td>
                  <Td align="right">
                    <PD v={subtotals} />
                  </Td>
                  <Td align="right">
                    <PD v={fees} />
                  </Td>
                  <Td align="right">
                    <PD v={totals} size="lg" />
                  </Td>
                  <Td align="right" />
                  <Td>
                    <Label>draft, not spend</Label>
                  </Td>
                </tr>
              </tfoot>
            </Table>
          </div>
        </ScrollX>

        <div className="mt-4 grid grid-cols-[minmax(0,1fr)] gap-3 border-t border-line pt-4">
          {!reconciles && (
            <Panel tone="bad" title="The column does not reconcile">
              The per-supplier totals above do not add up to the API&rsquo;s own{' '}
              <span className="fig">total_pence</span>. One of the two is wrong and this screen is
              not going to guess which.
            </Panel>
          )}
          <Panel title="Nothing here has been spent">
            The draft is a read — <span className="fig">writes_nothing</span> is{' '}
            {draft.writes_nothing ? 'true' : 'false'} — and an order exists only once a human
            confirms it in Telegram (invariant 1). The footer above is this screen&rsquo;s own
            exact integer sum, checked against the API&rsquo;s{' '}
            <span className="fig">total_pence</span>: it{' '}
            {reconciles ? 'reconciles' : 'does not reconcile'}.
          </Panel>
          {draft.reorder_cadence_days === null && (
            <Panel>
              No reorder cadence is set, so these windows are sized from each supplier&rsquo;s own
              delivery days rather than a fixed ordering rhythm.
            </Panel>
          )}
        </div>
      </div>
    </Card>
  )
}

/**
 * Where minimums bite — money that cannot be spent efficiently yet.
 *
 * With one nuance the first draft of this card got wrong: `meets_minimum` is
 * false for a supplier with NOTHING needed, because zero is below any minimum.
 * That is not a blocked order, it is an absent one, and the API says so in its
 * own words: a minimum is a condition on placing an order, not a reason to place
 * one. Only a supplier with lines that fall short is an operational problem.
 */
export function Minimums({ draft }: { draft: OrdersDraftResponse }) {
  const short = draft.suppliers.filter((o) => !o.meets_minimum)
  const blocking = short.filter((o) => o.lines.length > 0)
  const idle = short.filter((o) => o.lines.length === 0)

  return (
    <Card
      title="Where minimums bite"
      subtitle="A minimum order is money that has to be spent in one go or not at all."
      right={
        <Badge tone={blocking.length > 0 ? 'warn' : 'ok'}>
          {blocking.length} blocked
        </Badge>
      }
    >
      {blocking.length === 0 ? (
        <Note>
          Nothing is blocked by a minimum today. Every supplier with lines on its draft clears
          its own minimum, so no order is being held back or padded to reach one.
        </Note>
      ) : (
        <div className="space-y-4">
          {blocking.map((o) => (
            <Panel
              key={o.supplier.supplier_id}
              tone="warn"
              title={o.supplier.name}
              right={
                <>
                  <PD v={shortfall(o)} tone="warn" size="lg" /> short
                </>
              }
            >
              <div className="flex flex-wrap gap-x-6 gap-y-1">
                <span>
                  <Label>on the draft </Label>
                  <P v={o.total_pence} size="sm" />
                </span>
                <span>
                  <Label>minimum </Label>
                  <P
                    v={o.supplier.min_order_pence}
                    size="sm"
                    tone={o.supplier.terms_are_placeholders ? 'warn' : 'plain'}
                  />
                </span>
                <span>
                  <Label>lines </Label>
                  <N v={o.lines.length} size="sm" />
                </span>
              </div>
              {o.supplier.terms_are_placeholders && (
                <p className="mt-2 text-warn-ink">
                  That minimum was never confirmed with {o.supplier.name}, so the shortfall may
                  not exist at all.
                </p>
              )}
            </Panel>
          ))}
        </div>
      )}

      {idle.length > 0 && (
        <div className="mt-4 border-t border-line pt-4">
          <SectionLabel>Below minimum, but not a problem</SectionLabel>
          <div className="mb-2 flex flex-wrap gap-1">
            {idle.map((o) => (
              <Chip key={o.supplier.supplier_id} tone="muted">
                {o.supplier.name}
              </Chip>
            ))}
          </div>
          <p className="text-[0.875rem] leading-[20px] text-ink-3">
            {idle.length === 1 ? 'Reports' : 'Report'} under-minimum only because nothing is
            needed from {idle.length === 1 ? 'it' : 'them'} at all. A minimum is a condition on
            placing an order, not a reason to place one.
          </p>
        </div>
      )}

      {/* The ordering run's own notes about minimums and top-ups, verbatim. */}
      {(() => {
        const notes = draft.suppliers
          .flatMap((o) => o.notes)
          .filter(
            (n) =>
              /minimum|free-delivery|delivery fee/i.test(n) &&
              // The placeholder-terms essay belongs to the terms card, and a
              // sourcing note mentions the minimum only in passing.
              !/INVENTED PLACEHOLDERS/.test(n) &&
              !/SOURCING MOVED|SWITCHED to|KEPT /.test(n),
          )
        if (notes.length === 0) return null
        return (
          <div className="mt-4 border-t border-line pt-4">
            <SectionLabel>Reported by the ordering run</SectionLabel>
            <div className="grid grid-cols-[minmax(0,1fr)] gap-2">
              {notes.map((n) => (
                <Panel key={n}>{prose(n)}</Panel>
              ))}
            </div>
          </div>
        )
      })()}
    </Card>
  )
}
