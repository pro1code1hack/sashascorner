/**
 * Orders (stock-orders-suppliers.md §2): one basket per supplier, confirmed in
 * Telegram.
 *
 * DECISIONS §1: the web never creates or confirms a purchase order. Drafts are
 * shown as the bot will send them, pending ones read "Waiting in Telegram",
 * and the only writes here follow a human's confirmation or sit beside it:
 * cancel, mark sent, receive, and logging a shop run.
 *
 * The view is in the hash (`#/orders/history`), so a reload keeps it.
 */
import { useQuery } from '@tanstack/react-query'
import { FilterChip, PageBody, PageHeader } from '../../components/ui'
import { navigate, useLocation } from '../../lib/router'
import { KEYS, stockApi } from '../../lib/stock-api'
import { Drafts } from './Drafts'
import { History } from './History'
import { ShopRuns } from './ShopRuns'

type View = 'draft' | 'history' | 'runs'

const VIEWS: ReadonlyArray<{ id: View; label: string; path: string }> = [
  { id: 'draft', label: 'Draft orders', path: '/orders' },
  { id: 'history', label: 'History', path: '/orders/history' },
  { id: 'runs', label: 'Shop runs', path: '/orders/shop-runs' },
]

export function OrdersScreen() {
  const loc = useLocation()
  const sub = loc.segments[1]
  const view: View = sub === 'history' ? 'history' : sub === 'shop-runs' ? 'runs' : 'draft'
  const orders = useQuery({ queryKey: KEYS.orders, queryFn: () => stockApi.orders(), staleTime: 30_000 })
  const counts = orders.data?.counts

  return (
    <>
      <PageHeader title="Orders" subtitle="one basket per supplier, confirmed in Telegram" />
      <div className="flex flex-none flex-wrap items-center gap-2 border-b border-line px-4 py-2.5 sm:px-5">
        <nav aria-label="Orders views" className="flex flex-wrap gap-2">
          {VIEWS.map((v) => (
            <FilterChip key={v.id} active={view === v.id} onClick={() => navigate(v.path)}>
              {v.label}
            </FilterChip>
          ))}
        </nav>
        {counts && (
          <span className="ml-auto text-base text-ink-2">
            {counts.open} open · {counts.waiting} waiting in Telegram
          </span>
        )}
      </div>
      <PageBody className="compact:px-5">
        {view === 'draft' && <Drafts />}
        {view === 'history' && <History />}
        {view === 'runs' && <ShopRuns />}
      </PageBody>
    </>
  )
}
