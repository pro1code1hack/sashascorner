/**
 * Supplier terms as a money table — every commercial number the ordering maths
 * is sized against, in one place, so they can be compared rather than read one
 * order at a time.
 *
 * The load-bearing fact is the split. Six of the eight suppliers carry
 * `terms_are_placeholders`: their minimum, delivery fee, free-delivery
 * threshold, lead time, delivery days and cutoff were INVENTED during seeding
 * and never confirmed with anybody (ARCHITECTURE.md §8F.4). Only Tesco and
 * Amazon have terms a human has checked.
 *
 * So the table is grouped, not badged. A badge on a row is something you can
 * skim past; a section headed "never confirmed with anyone" containing six rows
 * is not. Every figure under that heading is fiction until somebody rings the
 * supplier, and the minimums in particular are the numbers the whole per-supplier
 * split is built on.
 */
import { useState } from 'react'
import { Badge, Card, Cell, Chip, Panel, ScrollX, Table, Td, Th } from '../../components/ui'
import type { Supplier } from '../../lib/types'
import { ConfirmTerms } from './ConfirmTerms'
import { Days, P, channelLabel, shortTime, weekdayNames } from './figures'

const COLS = 8

function byName(a: Supplier, b: Supplier): number {
  return a.name.localeCompare(b.name, 'en-GB')
}

function GroupHead({
  title,
  detail,
  tone,
}: {
  title: string
  detail: string
  tone: 'warn' | 'plain'
}) {
  return (
    <tr>
      <td colSpan={COLS} className="border-b border-line pb-2 pt-5 px-3 first:pt-0">
        <div className="flex flex-wrap items-baseline gap-x-2 gap-y-1">
          <span
            className={`text-[0.875rem] font-semibold ${
              tone === 'warn' ? 'text-warn-ink' : 'text-ink'
            }`}
          >
            {title}
          </span>
          <span className="text-[0.75rem] text-ink-4">{detail}</span>
        </div>
      </td>
    </tr>
  )
}

/**
 * One supplier. The confirm control lives in the FIRST column, not in an action
 * column on the right: this table is 58rem wide inside a `<ScrollX>`, and a
 * button in the last column is unreachable at 375px without scrolling the table
 * sideways first. The leftmost column is the one that is always on screen.
 */
function Row({
  s,
  invented,
  open,
  onToggle,
}: {
  s: Supplier
  invented: boolean
  open: boolean
  onToggle: () => void
}) {
  return (
    <tr className={open ? 'bg-raised/40' : undefined}>
      <Td className={invented ? 'border-l-2 border-l-warn/60' : 'border-l-2 border-l-transparent'}>
        <div className="min-w-[8rem]">
          <Cell
            wrap
            top={s.name}
            sub={invented ? 'terms never confirmed' : 'terms checked'}
          />
          <button
            type="button"
            onClick={onToggle}
            aria-expanded={open}
            className={`mt-1 text-[0.6875rem] underline decoration-dotted underline-offset-2 transition-colors ${
              invented ? 'text-warn-ink hover:text-warn' : 'text-ink-4 hover:text-ink-2'
            }`}
          >
            {open ? 'close the form' : invented ? 'confirm these terms' : 'update these terms'}
          </button>
        </div>
      </Td>
      <Td align="right">
        {s.min_order_pence === 0 ? (
          <span className="text-[0.75rem] text-ink-4">no minimum</span>
        ) : (
          <P v={s.min_order_pence} tone={invented ? 'warn' : 'plain'} />
        )}
      </Td>
      <Td align="right">
        {s.delivery_fee_pence === 0 ? (
          <span className="text-[0.75rem] text-ink-4">none</span>
        ) : (
          <P v={s.delivery_fee_pence} />
        )}
      </Td>
      <Td align="right">
        {/* Null is not zero, and it is not "free from £0.00" either: this
            supplier has no free-delivery threshold at all. */}
        <P
          v={s.free_delivery_threshold_pence}
          missing="no threshold"
          tone={invented ? 'warn' : 'plain'}
        />
      </Td>
      <Td align="right">
        <Days n={s.lead_time_days} tone={invented ? 'warn' : 'plain'} />
      </Td>
      <Td>
        <span
          className={`fig text-[0.75rem] whitespace-nowrap ${
            invented ? 'text-warn-ink' : 'text-ink'
          }`}
        >
          {weekdayNames(s.delivery_weekdays)}
        </span>
      </Td>
      <Td>
        <span className="text-[0.75rem] text-ink-2 whitespace-nowrap">
          {channelLabel(s.order_channel)}
        </span>
      </Td>
      <Td align="right">
        {shortTime(s.cutoff_time) === null ? (
          <span className="text-[0.75rem] text-ink-4">no cutoff</span>
        ) : (
          <span className={`fig text-[0.875rem] ${invented ? 'text-warn-ink' : 'text-ink'}`}>
            {shortTime(s.cutoff_time)}
          </span>
        )}
      </Td>
    </tr>
  )
}

export function SupplierTerms({ suppliers }: { suppliers: Supplier[] }) {
  const invented = suppliers.filter((s) => s.terms_are_placeholders).sort(byName)
  const confirmed = suppliers.filter((s) => !s.terms_are_placeholders).sort(byName)
  const [editing, setEditing] = useState<number | null>(null)
  /* Read from the live list rather than held in state, so a confirmation that
     refetches the table cannot leave the form editing a stale supplier. */
  const selected = suppliers.find((s) => s.supplier_id === editing) ?? null
  const toggle = (id: number) => setEditing((cur) => (cur === id ? null : id))

  return (
    <Card
      title="Supplier terms"
      subtitle="The commercial numbers every order is sized against. Grouped by whether anybody has confirmed them."
      right={
        <Badge tone={invented.length > 0 ? 'warn' : 'ok'}>
          {invented.length} of {suppliers.length} invented
        </Badge>
      }
      pad={false}
    >
      <div className="px-5 py-4">
        <ScrollX>
          <div className="min-w-[58rem]">
            <Table>
              <thead>
                <tr>
                  <Th>Supplier</Th>
                  <Th align="right">Minimum order</Th>
                  <Th align="right">Delivery fee</Th>
                  <Th align="right">Free from</Th>
                  <Th align="right">Lead time</Th>
                  <Th>Delivers</Th>
                  <Th>Ordered via</Th>
                  <Th align="right">Cutoff</Th>
                </tr>
              </thead>
              <tbody>
                {confirmed.length > 0 && (
                  <>
                    <GroupHead
                      tone="plain"
                      title={`Confirmed — ${confirmed.length}`}
                      detail="Terms somebody has actually checked. Safe to size an order against."
                    />
                    {confirmed.map((s) => (
                      <Row
                        key={s.supplier_id}
                        s={s}
                        invented={false}
                        open={editing === s.supplier_id}
                        onToggle={() => toggle(s.supplier_id)}
                      />
                    ))}
                  </>
                )}
                {invented.length > 0 && (
                  <>
                    <GroupHead
                      tone="warn"
                      title={`Never confirmed with anyone — ${invented.length}`}
                      detail="Seeded placeholders. Treat every figure below as fiction."
                    />
                    {invented.map((s) => (
                      <Row
                        key={s.supplier_id}
                        s={s}
                        invented
                        open={editing === s.supplier_id}
                        onToggle={() => toggle(s.supplier_id)}
                      />
                    ))}
                  </>
                )}
              </tbody>
            </Table>
          </div>
        </ScrollX>

        {selected !== null && (
          <ConfirmTerms
            key={selected.supplier_id}
            supplier={selected}
            onClose={() => setEditing(null)}
          />
        )}

        <div className="mt-4 grid grid-cols-[minmax(0,1fr)] gap-3 border-t border-line pt-4">
          <Panel tone="warn" title="Whose figures these are">
            <div className="mb-2 flex flex-wrap gap-1">
              {invented.map((s) => (
                <Chip key={s.supplier_id} tone="warn">
                  {s.name}
                </Chip>
              ))}
            </div>
            {invented.length === 1 ? 'Its terms were' : 'Their terms were'} written by the seeder
            to make the ordering maths run, not taken from a price list or an account manager. The
            minimum decides whether an order is placed at all and the lead time decides how many
            days of cover it has to buy, so a wrong one either wastes stock or causes a stockout.
            Confirming these eight rows is the cheapest accuracy available to this system.
          </Panel>
          <Panel title="What the fees and thresholds are used for">
            Delivery fees and free-delivery thresholds are what the top-up logic optimises
            against: below a threshold it adds non-perishable tier B stock to save the fee, and it
            never tops up with perishables — that would be buying waste to save a delivery charge.
          </Panel>
        </div>
      </div>
    </Card>
  )
}
