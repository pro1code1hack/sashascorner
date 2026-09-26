/**
 * Why that many packs.
 *
 * The line in the table says `6 packs`. This panel is the whole derivation
 * behind it, in the order the domain computes it: a forecast over the effective
 * cover window, less what is on hand, less what is already on an open PO, up to
 * the next whole pack.
 *
 * Two rules do the work here:
 *
 *  - Nothing is re-derived from prose. Every figure is a field. The only
 *    arithmetic the panel does at all is `packs × pack_size`, and that is exact
 *    integer work on BigInt via `lib/dec` — no float touches it (invariant 11).
 *  - A low-confidence forecast has no number, so the reason is rendered IN the
 *    figure's place rather than next to it (invariant 9). The row keeps its
 *    label; what it loses is the figure, which is the honest shape.
 */
import type { ReactNode } from 'react'
import { Badge, Fig, Note, Panel } from '../../components/ui'
import { fromInt, isZero, mul, mustDec, toFixed } from '../../lib/dec'
import { plural, trimQty } from '../../lib/format'
import { M, Q } from './figures'
import { prose, type OrderLine, type SupplierOrder } from './data'

/** An exact Dec as text: the product is integer work, then trailing zeros go. */
function decText(u: bigint, s: number): string {
  return trimQty(toFixed({ u, s }, Math.max(s, 6)))
}

function Step({
  op,
  label,
  sub,
  children,
  strong = false,
}: {
  op?: string
  label: ReactNode
  sub?: ReactNode
  children: ReactNode
  strong?: boolean
}) {
  return (
    <div className="grid grid-cols-[1.25rem_minmax(0,1fr)_minmax(0,auto)] items-baseline gap-x-3 border-b border-line py-1.5 last:border-b-0">
      <span aria-hidden className="fig text-center text-[0.75rem] text-ink-4">
        {op ?? ''}
      </span>
      <span className="min-w-0">
        <span className={`text-[0.75rem] ${strong ? 'text-ink' : 'text-ink-2'}`}>{label}</span>
        {sub && <span className="block text-[0.6875rem] leading-[16px] text-ink-4">{sub}</span>}
      </span>
      <span className="min-w-0 text-right">{children}</span>
    </div>
  )
}

/** Invariant 9 in one place: the sentence goes where the number would have been. */
function Withheld({ reasons }: { reasons: readonly string[] }) {
  return (
    <span className="inline-block max-w-[30ch] text-left text-[0.75rem] italic leading-[16px] text-ink-3">
      {reasons.length > 0
        ? prose(reasons.join(' '))
        : 'the forecast is low-confidence, so no quantity is given'}
    </span>
  )
}

export function Derivation({ o, l }: { o: SupplierOrder; l: OrderLine }) {
  const f = l.forecast
  const withheld = f.qty == null || f.is_low_confidence
  const packSize = mustDec(l.pack_size)
  const orderedQty = mul(fromInt(l.packs), packSize)
  const hasOpenPos = !isZero(mustDec(l.on_open_pos_qty))
  const ph = o.supplier.terms_are_placeholders
  const shortened = l.cover_days !== null && l.full_cover_days !== null && l.cover_days < l.full_cover_days

  return (
    <div className="mt-4 rounded-card-lg border border-line bg-sunk">
      <header className="flex flex-wrap items-baseline justify-between gap-x-4 gap-y-1 border-b border-line px-4 py-2.5">
        <h4 className="font-medium text-ink">
          Why <Fig>{l.packs}</Fig> {plural(l.packs, 'pack')} of {l.ingredient_name}
        </h4>
        <span className="text-[0.6875rem] text-ink-4">
          {l.sku ? `sku ${l.sku} · ` : ''}
          {o.supplier.name}
        </span>
      </header>

      <div className="grid gap-x-8 gap-y-4 px-4 py-3 lg:grid-cols-[minmax(0,1fr)_minmax(0,1fr)]">
        {/* the chain, in the order the domain computes it */}
        <div className="min-w-0">
          <Step
            label={
              <>
                forecast over{' '}
                <Fig size="sm">{f.window_days}</Fig> {plural(f.window_days, 'day')}
                {shortened ? ' — the window a cap shortened' : ''}
              </>
            }
            sub={
              <>
                {f.history_days} {plural(f.history_days, 'day')} of history
                {f.used_flat_average ? ' · flat average, not EWMA' : ''}
              </>
            }
          >
            {withheld ? <Withheld reasons={f.reasons} /> : <Q qty={f.qty ?? null} unit={f.unit} />}
          </Step>

          <Step op="−" label="on hand now">
            <Q qty={l.on_hand_qty} unit={l.unit} />
          </Step>

          <Step
            op="−"
            label="already on an open purchase order"
            sub={hasOpenPos ? undefined : 'nothing outstanding'}
          >
            <Q qty={l.on_open_pos_qty} unit={l.unit} tone={hasOpenPos ? 'plain' : 'muted'} />
          </Step>

          <Step op="=" label="need" strong>
            <Q qty={l.need_qty} unit={l.unit} />
          </Step>

          <Step op="÷" label="pack size">
            <Fig>
              {trimQty(l.pack_size)}
              <span className="font-normal text-ink-3"> {l.pack_unit}</span>
            </Fig>
          </Step>

          <Step op="=" label="packs, rounded up to a whole pack" strong>
            <Fig size="lg">{l.packs}</Fig>
          </Step>

          <Step op="×" label="pack price">
            <M v={l.pack_price_pence} />
          </Step>

          <Step op="=" label="line total" strong>
            <M v={l.line_total_pence} size="lg" />
          </Step>
        </div>

        {/* what the packs do to the shelf, and every caveat on the figure */}
        <div className="min-w-0">
          <div className="rounded-card-lg border border-line bg-surface px-3 py-2">
            <Step label="quantity ordered">
              <Q qty={decText(orderedQty.u, orderedQty.s)} unit={l.pack_unit} />
            </Step>
            <Step label="on hand after delivery">
              <Q qty={l.resulting_on_hand_qty} unit={l.unit} />
            </Step>
            <Step
              label="cover"
              sub={ph ? "from this supplier's invented lead time" : undefined}
            >
              {l.cover_days === null ? (
                <Fig missing="not computed" />
              ) : (
                <Fig tone={shortened ? 'warn' : 'plain'}>
                  {l.cover_days}
                  {shortened && l.full_cover_days !== null ? (
                    <span className="font-normal text-ink-3"> of {l.full_cover_days}</span>
                  ) : null}
                  <span className="font-normal text-ink-3"> d</span>
                </Fig>
              )}
            </Step>
          </div>

          {/* The cap is the single most important thing this panel can say: the
              quantity deliberately NOT ordered is the system working, not a
              shortfall. A Panel is what stops it being skimmed past. */}
          {l.is_capped && (
            <div className="mt-3">
              <Panel
                tone="warn"
                title={
                  <span className="flex flex-wrap items-center gap-2">
                    <Badge tone="warn">invariant 4</Badge>
                    {l.cap_reason && <span>{prose(l.cap_reason)}</span>}
                  </span>
                }
              >
                {l.capped_out_qty !== null && (
                  <p>
                    <Q qty={l.capped_out_qty} unit={l.unit} tone="warn" /> deliberately not
                    ordered
                    {l.forecast_full_window_qty !== null && l.full_cover_days !== null ? (
                      <>
                        {' '}
                        — the full {l.full_cover_days}-day window wanted{' '}
                        <Q qty={l.forecast_full_window_qty} unit={l.unit} size="sm" />.
                      </>
                    ) : (
                      '.'
                    )}
                  </p>
                )}
                {l.cap_detail && (
                  <Note tone="muted">
                    <span className="mt-1.5 block">{prose(l.cap_detail)}</span>
                  </Note>
                )}
              </Panel>
            </div>
          )}

          {l.is_top_up && (
            <div className="mt-3">
              <Panel tone="info" title={<Badge tone="info">top-up</Badge>}>
                Added to clear this supplier&rsquo;s minimum, not because stock demanded it.
                Invariant 5 means it is never a perishable: buying something that spoils to save
                a delivery fee is buying waste.
              </Panel>
            </div>
          )}

          {l.clamped && (
            <div className="mt-3">
              <Panel tone="muted">{prose(l.clamped)}</Panel>
            </div>
          )}

          {!withheld && f.reasons.length > 0 && (
            <div className="mt-3">
              {f.reasons.map((r) => (
                <Note key={r} tone="muted">
                  {prose(r)}
                </Note>
              ))}
            </div>
          )}

          {ph && (
            <Note tone="warn">
              <span className="mt-3 block">
                The window above comes from {o.supplier.name}&rsquo;s lead time and delivery
                days, which are invented placeholders — so this quantity is only as good as
                they are.
              </span>
            </Note>
          )}
        </div>
      </div>
    </div>
  )
}
