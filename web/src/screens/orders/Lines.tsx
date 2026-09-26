/**
 * What is about to be bought from one supplier.
 *
 * The table is nine columns of figures and it is WIDER than a phone, so it
 * scrolls inside itself: `<ScrollX>` plus a `min-w` block. The previous build
 * let a wide panel run off the viewport and truncate a figure mid-digit
 * (`67.94670` with the rest gone) and a cut-off number is worse than an absent
 * one, because it still reads as a number.
 *
 * The derivation of a line does NOT live in the table. It renders underneath,
 * outside the scroller, so the prose wraps at the reader's width instead of at
 * the table's.
 */
import { Badge, Cell, Fig, ScrollX, Table, Td, Th } from '../../components/ui'
import { isZero, mustDec } from '../../lib/dec'
import { plural, trimQty } from '../../lib/format'
import { M, Q } from './figures'
import { Derivation } from './Derivation'
import type { OrderLine, SupplierOrder } from './data'

function lineKey(o: SupplierOrder, l: OrderLine): string {
  return `${o.supplier.supplier_id}:${l.ingredient_id}`
}

function Cover({ l }: { l: OrderLine }) {
  if (l.cover_days === null) return <Fig missing="not computed" />
  const shortened = l.full_cover_days !== null && l.cover_days < l.full_cover_days
  return (
    <Fig tone={shortened ? 'warn' : 'plain'}>
      {l.cover_days}
      {shortened && l.full_cover_days !== null ? (
        <span className="font-normal text-ink-3"> of {l.full_cover_days}</span>
      ) : null}
      <span className="font-normal text-ink-3"> d</span>
    </Fig>
  )
}

export function Lines({
  o,
  open,
  onToggle,
}: {
  o: SupplierOrder
  open: ReadonlySet<string>
  onToggle: (key: string) => void
}) {
  const feeWaived = o.supplier.delivery_fee_pence > 0 && o.delivery_fee_pence === 0
  return (
    <div>
      <ScrollX>
        <div className="min-w-[50rem]">
          <Table>
            <thead>
              <tr>
                <Th>Ingredient</Th>
                <Th align="right">Packs</Th>
                <Th align="right">Pack price</Th>
                <Th align="right">Need</Th>
                <Th align="right">On hand</Th>
                <Th align="right">After delivery</Th>
                <Th align="right">Cover</Th>
                <Th align="right">Line total</Th>
                <Th align="right"> </Th>
              </tr>
            </thead>
            <tbody>
              {o.lines.map((l) => {
                const k = lineKey(o, l)
                const isOpen = open.has(k)
                const openPos = !isZero(mustDec(l.on_open_pos_qty))
                return (
                  <tr key={k} className={isOpen ? 'bg-raised' : undefined}>
                    {/* Name over the quieter identifying line: the SKU where the
                        supplier gave one, the pack it comes in where it did not. */}
                    <Td className="min-w-[11rem]">
                      <Cell
                        top={l.ingredient_name}
                        sub={l.sku ? `sku ${l.sku}` : `${trimQty(l.pack_size)} ${l.pack_unit} pack`}
                      />
                      {(l.is_capped || l.is_top_up || l.forecast.is_low_confidence) && (
                        <span className="mt-1.5 flex flex-wrap items-center gap-1">
                          {l.is_capped && <Badge tone="warn">capped</Badge>}
                          {l.is_top_up && <Badge tone="info">top-up</Badge>}
                          {l.forecast.is_low_confidence && (
                            <Badge tone="muted">forecast withheld</Badge>
                          )}
                        </span>
                      )}
                    </Td>
                    <Td align="right">
                      <Fig className="whitespace-nowrap">{l.packs}</Fig>
                    </Td>
                    <Td align="right">
                      <M v={l.pack_price_pence} size="sm" />
                    </Td>
                    <Td align="right">
                      <Q qty={l.need_qty} unit={l.unit} size="sm" />
                    </Td>
                    <Td align="right">
                      <Q qty={l.on_hand_qty} unit={l.unit} size="sm" />
                      {openPos && (
                        <span className="block text-[0.6875rem] text-ink-4">
                          + {trimQty(l.on_open_pos_qty)} on open POs
                        </span>
                      )}
                    </Td>
                    <Td align="right">
                      <Q qty={l.resulting_on_hand_qty} unit={l.unit} size="sm" tone="muted" />
                    </Td>
                    <Td align="right">
                      <Cover l={l} />
                    </Td>
                    <Td align="right">
                      <M v={l.line_total_pence} />
                    </Td>
                    <Td align="right">
                      <button
                        type="button"
                        aria-expanded={isOpen}
                        onClick={() => onToggle(k)}
                        className="whitespace-nowrap rounded-control px-1.5 py-0.5 text-[0.6875rem] text-ink-3 underline decoration-dotted underline-offset-2 hover:text-ink"
                      >
                        {isOpen ? 'hide why' : 'why?'}
                      </button>
                    </Td>
                  </tr>
                )
              })}
            </tbody>
          </Table>
        </div>
      </ScrollX>

      {/* The totals sit OUTSIDE the scroller. They are the figures most likely
          to be read, and inside a horizontal scroll they are the figures most
          likely to be half-read. */}
      <div className="mt-3 flex flex-wrap items-baseline justify-end gap-x-7 gap-y-2 border-t border-line pt-3">
        <span className="mr-auto text-[0.75rem] text-ink-4">
          {o.lines.length} {plural(o.lines.length, 'line')}
        </span>
        <span className="whitespace-nowrap">
          <span className="mr-2 text-[0.6875rem] text-ink-4">subtotal</span>
          <M v={o.subtotal_pence} />
        </span>
        <span className="whitespace-nowrap">
          <span className="mr-2 text-[0.6875rem] text-ink-4">delivery</span>
          {feeWaived ? (
            <Fig tone="muted" size="sm">
              waived
            </Fig>
          ) : o.delivery_fee_pence === 0 ? (
            <Fig tone="muted" size="sm">
              none
            </Fig>
          ) : (
            <M v={o.delivery_fee_pence} />
          )}
        </span>
        <span className="whitespace-nowrap">
          <span className="mr-2 text-[0.6875rem] font-medium text-ink-3">total</span>
          <M v={o.total_pence} size="lg" />
        </span>
      </div>

      {feeWaived && (
        <p className="mt-2 max-w-[86ch] text-[0.75rem] text-ink-4">
          The <M v={o.supplier.delivery_fee_pence} size="sm" /> delivery fee is waived: the
          order clears this supplier&rsquo;s free-delivery threshold on its own, and nothing
          was added to get it there.
        </p>
      )}

      {o.lines
        .filter((l) => open.has(lineKey(o, l)))
        .map((l) => (
          <Derivation key={lineKey(o, l)} o={o} l={l} />
        ))}
    </div>
  )
}
