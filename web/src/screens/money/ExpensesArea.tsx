/**
 * `#/money/expenses`: the expenses log. Orders were briefly tabs here and are
 * their own page again (owner, 2026-09-26); their old links redirect.
 */
import { useLocation } from '../../lib/router'
import { ExpensesOrdersRedirect } from '../orders/OrdersScreen'
import { ExpensesScreen } from './ExpensesScreen'

export function ExpensesArea() {
  const loc = useLocation()
  const rest = loc.segments.slice(2)
  if (rest[0] === 'orders' || rest[0] === 'drafts' || rest[0] === 'shop-runs') return <ExpensesOrdersRedirect rest={rest} />
  return <ExpensesScreen />
}
