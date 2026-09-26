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
import { Empty, ErrorBox, Loading, Segmented, cx } from '../../components/ui'
import { Pagination } from '../../components/ui/Pagination'
import { dayShort, stamp } from '../../lib/format'
import { useItemSales } from '../../lib/menu-api'
import type { SaleChannel } from '../../lib/types/menu'
import { gbp, qtyText, sizeLabel } from './common/figures'

const CHANNEL: Record<SaleChannel, string> = {
  EPOS: 'Till',
  DELIVEROO: 'Deliveroo',
  JUST_EAT: 'Just Eat',
  OTHER: 'Other',
}

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
          <dl className="grid grid-cols-2 gap-2 sm:grid-cols-4">
            <Fig label="Sold" value={qtyText(d.units)} />
            <Fig label="Taken" value={gbp(d.gross_pence)} />
            <Fig label="First sale" value={dayShort(d.first_sold_at)} />
            <Fig label="Last sale" value={dayShort(d.last_sold_at)} />
          </dl>
          <p className="text-sm text-ink-2">
            {Object.entries(d.by_channel)
              .map(([c, n]) => `${n} via ${CHANNEL[c as SaleChannel] ?? c}`)
              .join(' · ')}
            . {d.payment_note}
          </p>
          <div className={cx('overflow-hidden rounded-card border border-line', q.isFetching && 'opacity-70')}>
            <div className="hidden grid-cols-[170px_56px_56px_90px_100px_minmax(0,1fr)] gap-3 border-b border-line bg-canvas-2 px-3 py-2 text-label font-bold uppercase tracking-[.06em] text-ink-3 sm:grid">
              <span>When</span>
              <span>Size</span>
              <span className="text-right">Qty</span>
              <span className="text-right">Takings</span>
              <span>Channel</span>
              <span>Receipt</span>
            </div>
            <ul>
              {d.rows.map((r) => (
                <li
                  key={r.sale_id}
                  className={cx(
                    'grid grid-cols-[minmax(0,1fr)_auto] gap-x-3 gap-y-0.5 border-b border-line-row px-3 py-2 text-base last:border-b-0 sm:grid-cols-[170px_56px_56px_90px_100px_minmax(0,1fr)] sm:items-center',
                    r.voided && 'text-ink-2 line-through',
                  )}
                >
                  <span className="fig">{stamp(r.sold_at)}</span>
                  <span className="text-ink-2 sm:text-ink max-sm:hidden">{sizeLabel(r.size_code)}</span>
                  <span className="fig text-right max-sm:hidden">{qtyText(r.qty)}</span>
                  <span className={cx('fig text-right font-bold', r.is_refund && 'text-alert')}>{gbp(r.gross_pence)}</span>
                  <span className="text-sm text-ink-2 sm:text-base sm:text-ink">
                    <span className="sm:hidden">
                      {sizeLabel(r.size_code)} · ×{qtyText(r.qty)} ·{' '}
                    </span>
                    {CHANNEL[r.channel] ?? r.channel}
                  </span>
                  <span className="min-w-0 truncate text-right text-sm text-ink-2 sm:text-left">
                    {r.receipt_id}
                    {r.voided && ' · voided'}
                    {r.is_refund && ' · refund'}
                    {r.modifier_names.length > 0 && ` · ${r.modifier_names.join(', ')}`}
                  </span>
                </li>
              ))}
            </ul>
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

function Fig({ label, value }: { label: string; value: string }) {
  return (
    <div className="rounded-button bg-canvas px-3 py-2">
      <dt className="text-xs font-bold text-ink-2">{label}</dt>
      <dd className="fig text-lg font-extrabold">{value}</dd>
    </div>
  )
}
