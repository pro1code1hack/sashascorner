/**
 * Order history for one menu item: every till line matched to it, newest
 * first, from `GET /api/menu-items/:id/sales` (read-only, paginated).
 *
 * Source is the `sale` table: Lightspeed receipt lines, one per item per
 * receipt, with the channel it came through. How each line was PAID is not
 * known per item (the payment reports are daily totals per method), and the
 * server says so in `payment_note`; this section shows that sentence rather
 * than inventing a column.
 */
import { useState } from 'react'
import { Empty, ErrorBox, Loading, Segmented, TBody, Td, Th, THead, Table, Tr } from '../../components/ui'
import { Pagination } from '../../components/ui/Pagination'
import { dayShort, stamp } from '../../lib/format'
import { useItemSales } from '../../lib/menu-api'
import type { SaleChannel } from '../../lib/types/menu'
import { Figures } from '../money/filters'
import { gbp, qtyText, sizeLabel } from './common/figures'

const CHANNEL: Record<SaleChannel, string> = {
  EPOS: 'Till',
  CASH: 'Cash (off till)',
  DELIVEROO: 'Deliveroo',
  JUST_EAT: 'Just Eat',
  WEB: 'Online order',
  OTHER: 'Other',
}
const channelWord = (c: string) => CHANNEL[c as SaleChannel] ?? c

export function ItemSales({
  menuItemId,
  sizeCount,
  sizeName,
  onTill,
}: {
  menuItemId: number
  sizeCount: number
  sizeName: string
  onTill: boolean
}) {
  const [page, setPage] = useState(1)
  const [per, setPer] = useState(25)
  const [scope, setScope] = useState<'all' | 'size'>('all')
  const all = scope === 'all' || sizeCount < 2
  const q = useItemSales(menuItemId, page, per, all)
  const d = q.data

  return (
    <section aria-labelledby="mi-sales" className="flex flex-col gap-3">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <h2 id="mi-sales" className="text-xl font-extrabold tracking-[-.01em]">
          Order history
        </h2>
        {sizeCount > 1 && (
          <Segmented
            label="Which sizes"
            value={scope}
            onChange={(v) => {
              setScope(v)
              setPage(1)
            }}
            options={[
              { value: 'all', label: 'All sizes' },
              { value: 'size', label: `Size ${sizeName} only` },
            ]}
          />
        )}
      </div>
      {q.isLoading && <Loading what="Loading sales" />}
      {q.error && <ErrorBox error={q.error} what="the sales for this item" />}
      {d && d.total_rows === 0 && (
        <Empty>
          No till sales are matched to this item{all ? '' : ' at this size'}.
          {!onTill && ' It is not matched to a Lightspeed product yet, so its sales cannot be counted here.'}
        </Empty>
      )}
      {d && d.total_rows > 0 && (
        <>
          <Figures
            className="border-t"
            items={[
              { label: 'Sold', value: qtyText(d.units), strong: true },
              { label: 'Taken', value: gbp(d.gross_pence) },
              { label: 'First sale', value: dayShort(d.first_sold_at) },
              { label: 'Last sale', value: dayShort(d.last_sold_at) },
            ]}
          />
          <p className="text-sm text-ink-2">
            {Object.entries(d.by_channel)
              .map(([c, n]) => `${n} via ${channelWord(c)}`)
              .join(' · ')}
            . {d.payment_note}
          </p>
          <div className="rounded-card border border-line px-3" aria-busy={q.isFetching || undefined}>
            <Table label="Till lines matched to this item" minWidth={620}>
              <THead>
                <tr>
                  <Th>When</Th>
                  <Th>Size</Th>
                  <Th numeric>Qty</Th>
                  <Th numeric>Takings</Th>
                  <Th>Channel</Th>
                  <Th>Receipt</Th>
                </tr>
              </THead>
              <TBody>
                {d.rows.map((r) => (
                  <Tr key={r.sale_id} className={r.voided ? 'text-ink-2 line-through' : undefined}>
                    <Td className="fig whitespace-nowrap">{stamp(r.sold_at)}</Td>
                    <Td>{sizeLabel(r.size_code)}</Td>
                    <Td numeric>{qtyText(r.qty)}</Td>
                    <Td numeric strong>
                      {gbp(r.gross_pence)}
                    </Td>
                    <Td>{channelWord(r.channel)}</Td>
                    <Td secondary>
                      {r.receipt_id}
                      {r.voided && ' · voided'}
                      {r.is_refund && ' · refund'}
                      {r.modifier_names.length > 0 && ` · ${r.modifier_names.join(', ')}`}
                    </Td>
                  </Tr>
                ))}
              </TBody>
            </Table>
          </div>
          <Pagination
            page={page}
            pageSize={per}
            total={d.total_rows}
            noun="sales"
            sizes={[25, 50, 100]}
            onPage={setPage}
            onPageSize={(n) => {
              setPer(n)
              setPage(1)
            }}
          />
          <p className="text-xs text-ink-2">Voided lines are struck through and left out of the totals above.</p>
        </>
      )}
    </section>
  )
}
