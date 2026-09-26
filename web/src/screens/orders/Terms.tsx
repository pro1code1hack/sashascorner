/**
 * A supplier's terms, and how much of them is a guess.
 *
 * Six of eight suppliers carry `terms_are_placeholders` (ARCHITECTURE.md §8F.4):
 * lead time, delivery days, cutoff, minimum and the free-delivery threshold were
 * never confirmed with any of them. The cover window is built from exactly those
 * fields, so every quantity on such an order rests on the guess — and the screen
 * must not print them in the same ink as Tesco's and Amazon's real ones. Each
 * invented field says so on its own label, because a single badge on the card
 * header does not tell you WHICH number is invented.
 */
import { Badge, Cell, Fig, Note, Panel, ScrollX, Table, Td, Th } from '../../components/ui'
import { DayName, Days, Field, M } from './figures'
import {
  channelLabel,
  shortTime,
  weekdayNames,
  type Supplier,
  type SupplierOrder,
} from './data'

function Invented() {
  return <span className="text-warn font-semibold"> · invented</span>
}

export function TermsPanel({ o }: { o: SupplierOrder }) {
  const s = o.supplier
  const ph = s.terms_are_placeholders
  const mark = ph ? <Invented /> : null
  /* The panel itself stays quiet. Six of eight suppliers carry invented terms,
     so it is the ambient condition rather than an alarm, and a wash of amber on
     six of eight cards would say only "this is a dashboard". What is marked in
     amber is the individual field that was invented — which is what a reader
     actually needs, since a badge on the header does not say WHICH number is a
     guess. The two fields derived from the guesses, the cover window and the
     target delivery date, carry the mark too. */
  return (
    <Panel
      tone="muted"
      title="Terms, and the window they build"
      right={channelLabel(s.order_channel)}
    >
      <div className="grid grid-cols-2 gap-x-4 gap-y-3 sm:grid-cols-3 lg:grid-cols-6">
        <Field label={<>Lead time{mark}</>} tone={ph ? 'warn' : undefined}>
          <Days n={s.lead_time_days} />
        </Field>
        <Field label={<>Delivers{mark}</>} tone={ph ? 'warn' : undefined}>
          <Fig size="sm">{weekdayNames(s.delivery_weekdays)}</Fig>
        </Field>
        <Field label={<>Cutoff{mark}</>} tone={ph ? 'warn' : undefined}>
          {shortTime(s.cutoff_time) ? (
            <Fig>{shortTime(s.cutoff_time)}</Fig>
          ) : (
            <Fig missing="no cutoff" />
          )}
        </Field>
        <Field label={<>Minimum{mark}</>} tone={ph ? 'warn' : undefined}>
          {s.min_order_pence === 0 ? (
            <Fig size="sm" tone="muted">
              none
            </Fig>
          ) : (
            <M v={s.min_order_pence} />
          )}
        </Field>
        <Field label="Delivery fee">
          {s.delivery_fee_pence === 0 ? (
            <Fig size="sm" tone="muted">
              none
            </Fig>
          ) : (
            <M v={s.delivery_fee_pence} />
          )}
        </Field>
        <Field label={<>Free from{mark}</>} tone={ph ? 'warn' : undefined}>
          {s.free_delivery_threshold_pence === null ? (
            <Fig size="sm" tone="muted">
              no threshold
            </Fig>
          ) : (
            <M v={s.free_delivery_threshold_pence} />
          )}
        </Field>
      </div>

      <div className="mt-3 grid grid-cols-2 gap-x-4 gap-y-3 border-t border-line-2/60 pt-3 sm:grid-cols-4">
        <Field label={<>Cover window{mark}</>} tone={ph ? 'warn' : undefined}>
          <Days n={o.cover_window_days} tone={ph ? 'warn' : 'plain'} />
        </Field>
        <Field label="Covering">
          <Fig size="sm">
            {o.cover_window_from ?? '—'} → {o.cover_window_to ?? '—'}
          </Fig>
        </Field>
        <Field label="Next delivery in">
          <Days n={o.days_until_next_delivery} />
        </Field>
        <Field label={<>Target delivery{mark}</>} tone={ph ? 'warn' : undefined}>
          <DayName iso={o.target_delivery_date} />
        </Field>
      </div>

      <div className="mt-3">
        {ph ? (
          <Note tone="warn">
            Five of these terms are invented placeholders, and the cover window is built from
            them — so is every quantity on this order.
          </Note>
        ) : (
          <Note tone="muted">Terms confirmed. Only Tesco and Amazon are.</Note>
        )}
      </div>
    </Panel>
  )
}

/** All eight suppliers side by side: the reference view of §10.3, and the only
 *  place the two real sets of terms can be compared with the six invented ones. */
export function TermsTable({ suppliers }: { suppliers: Supplier[] }) {
  const rows = [...suppliers].sort((a, b) =>
    a.terms_are_placeholders === b.terms_are_placeholders
      ? a.name.localeCompare(b.name)
      : a.terms_are_placeholders
        ? 1
        : -1,
  )
  return (
    <ScrollX>
      <div className="min-w-[42rem]">
        <Table>
          <thead>
            <tr>
              <Th>Supplier</Th>
              <Th>Terms</Th>
              <Th align="right">Lead</Th>
              <Th>Delivers</Th>
              <Th align="right">Cutoff</Th>
              <Th align="right">Minimum</Th>
              <Th align="right">Fee</Th>
              <Th align="right">Free from</Th>
            </tr>
          </thead>
          <tbody>
            {rows.map((s) => (
              <tr key={s.supplier_id}>
                <Td className="min-w-[11rem]">
                  <Cell top={s.name} sub={channelLabel(s.order_channel)} />
                </Td>
                <Td>
                  {s.terms_are_placeholders ? (
                    <Badge tone="warn">invented</Badge>
                  ) : (
                    <Badge tone="ok">confirmed</Badge>
                  )}
                </Td>
                <Td align="right">
                  <Days n={s.lead_time_days} />
                </Td>
                {/* A delivery-day list and a "no cutoff" are read as one thing;
                    wrapped over three lines they read as three. The table scrolls
                    inside itself instead. */}
                <Td className="whitespace-nowrap">
                  <Fig size="sm">{weekdayNames(s.delivery_weekdays)}</Fig>
                </Td>
                <Td align="right" className="whitespace-nowrap">
                  {shortTime(s.cutoff_time) ? (
                    <Fig size="sm">{shortTime(s.cutoff_time)}</Fig>
                  ) : (
                    <Fig missing="none" />
                  )}
                </Td>
                <Td align="right">
                  {s.min_order_pence === 0 ? (
                    <Fig size="sm" tone="muted">
                      none
                    </Fig>
                  ) : (
                    <M v={s.min_order_pence} size="sm" />
                  )}
                </Td>
                <Td align="right">
                  {s.delivery_fee_pence === 0 ? (
                    <Fig size="sm" tone="muted">
                      none
                    </Fig>
                  ) : (
                    <M v={s.delivery_fee_pence} size="sm" />
                  )}
                </Td>
                <Td align="right" className="whitespace-nowrap">
                  {s.free_delivery_threshold_pence === null ? (
                    <Fig missing="none" />
                  ) : (
                    <M v={s.free_delivery_threshold_pence} size="sm" />
                  )}
                </Td>
              </tr>
            ))}
          </tbody>
        </Table>
      </div>
    </ScrollX>
  )
}
