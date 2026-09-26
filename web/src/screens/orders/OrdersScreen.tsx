/**
 * Orders: its own page with three sub-pages (owner, 2026-09-26), each in the
 * hash so a reload keeps it:
 *
 *   #/orders               every stored order, filterable, one row each
 *   #/orders/<id>          one order's page: set packs and confirm a draft,
 *                          mark sent, receive into stock, receipt photo
 *   #/orders/drafts        what needs ordering next, per supplier; "Create
 *                          order" writes a draft to confirm
 *   #/orders/shop-runs     bought at a shop because it could not wait
 *
 * The Telegram bot is not in use for now (DECISIONS 18): orders are created and
 * confirmed here, always by a named person (invariant 1). Nothing is ever sent
 * to a supplier by the app. Links from before (`#/money/expenses/orders/…`,
 * `#/orders/history`) redirect.
 */
import { useEffect } from 'react'
import { useQuery } from '@tanstack/react-query'
import { Loading, PageBody, PageHeader } from '../../components/ui'
import { navigate, useLocation } from '../../lib/router'
import { KEYS, stockApi } from '../../lib/stock-api'
import { Drafts } from './Drafts'
import { History } from './History'
import { OrderPage } from './OrderPage'
import { OrdersTabs } from './OrdersTabs'
import type { OrdersTab } from './OrdersTabs'
import { ShopRuns } from './ShopRuns'

export function OrdersArea() {
  const loc = useLocation()
  const sub = loc.segments[1]
  const legacy = sub === 'history' ? '/orders' : null
  useEffect(() => {
    if (legacy) navigate(legacy, { replace: true })
  }, [legacy])
  if (legacy) return <Loading what="Opening orders" />
  if (sub !== undefined && /^\d+$/.test(sub)) return <OrderPage poId={Number(sub)} />
  if (sub === 'drafts') return <OrdersScreen view="drafts" />
  if (sub === 'shop-runs') return <OrdersScreen view="runs" />
  return <OrdersScreen view="orders" />
}

function OrdersScreen({ view }: { view: OrdersTab }) {
  const orders = useQuery({ queryKey: KEYS.orders, queryFn: () => stockApi.orders(), staleTime: 30_000 })
  const counts = orders.data?.counts
  return (
    <>
      <PageHeader
        title="Orders"
        subtitle={<OrdersTabs current={view} toConfirm={counts?.waiting} />}
        saved={counts ? `${counts.open} open · ${counts.waiting} to confirm` : undefined}
      />
      <PageBody className="compact:px-5">
        {view === 'drafts' && <Drafts />}
        {view === 'orders' && <History />}
        {view === 'runs' && <ShopRuns />}
      </PageBody>
    </>
  )
}

/** `#/money/expenses/orders[/<id>]`, `…/drafts`, `…/shop-runs` from the short-lived Expenses tabs. */
export function ExpensesOrdersRedirect({ rest }: { rest: string[] }) {
  const [sub, id] = rest
  const to = sub === 'drafts' ? '/orders/drafts' : sub === 'shop-runs' ? '/orders/shop-runs' : id ? `/orders/${id}` : '/orders'
  useEffect(() => navigate(to, { replace: true }), [to])
  return <Loading what="Opening orders" />
}
