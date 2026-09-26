/**
 * The route table: one entry per nav item, plus the routes reached from
 * inside the app (Setup checklist). The sidebar is generated from NAV_GROUPS;
 * App renders `resolveRoute(location).Screen`.
 *
 * A route matches its own path and anything below it, so a screen may use
 * sub-paths (`#/recipes/proposal/abc123`) and read them with useLocation().
 */
import { lazy } from 'react'
import type { ComponentType, LazyExoticComponent } from 'react'
import type { Location } from './lib/router'
import { ScreenLoadFailed } from './components/shell/ScreenFallback'

/**
 * Each screen is its own chunk, fetched the first time its route renders
 * (App wraps it in Suspense). A chunk that fails to load -- typically a tab
 * left open across a deploy, whose old hashed files are gone -- renders a
 * sentence and a reload button instead of a blank page.
 */
function screen<K extends string>(
  load: () => Promise<Record<K, ComponentType>>,
  name: K,
): LazyExoticComponent<ComponentType> {
  return lazy(() =>
    load().then(
      (m): { default: ComponentType } => ({ default: m[name] }),
      (): { default: ComponentType } => ({ default: ScreenLoadFailed }),
    ),
  )
}

const StockScreen = screen(() => import('./screens/stock/StockScreen'), 'StockScreen')
const OrdersScreen = screen(() => import('./screens/orders/OrdersScreen'), 'OrdersScreen')
const AgentsScreen = screen(() => import('./screens/agents/AgentsScreen'), 'AgentsScreen')
const RecipesScreen = screen(() => import('./screens/recipes/RecipesScreen'), 'RecipesScreen')
const MenuItemsScreen = screen(() => import('./screens/menu/MenuItemsScreen'), 'MenuItemsScreen')
const IngredientsScreen = screen(() => import('./screens/ingredients/IngredientsScreen'), 'IngredientsScreen')
const SuppliersScreen = screen(() => import('./screens/suppliers/SuppliersScreen'), 'SuppliersScreen')
const OverviewScreen = screen(() => import('./screens/money/OverviewScreen'), 'OverviewScreen')
const SalesScreen = screen(() => import('./screens/money/SalesScreen'), 'SalesScreen')
const ExpensesScreen = screen(() => import('./screens/money/ExpensesScreen'), 'ExpensesScreen')
const ReconcileScreen = screen(() => import('./screens/money/ReconcileScreen'), 'ReconcileScreen')
const ProfitLossScreen = screen(() => import('./screens/money/ProfitLossScreen'), 'ProfitLossScreen')
const DirectorsAccountScreen = screen(() => import('./screens/money/DirectorsAccountScreen'), 'DirectorsAccountScreen')
const SettingsScreen = screen(() => import('./screens/settings/SettingsScreen'), 'SettingsScreen')
const SetupScreen = screen(() => import('./screens/setup/SetupScreen'), 'SetupScreen')

export type RouteId =
  | 'stock'
  | 'orders'
  | 'agents'
  | 'recipes'
  | 'menu'
  | 'ingredients'
  | 'suppliers'
  | 'money.overview'
  | 'money.sales'
  | 'money.expenses'
  | 'money.reconcile'
  | 'money.pnl'
  | 'money.director'
  | 'settings'
  | 'setup'

export interface RouteDef {
  id: RouteId
  /** Hash path, "/money/reconcile". */
  path: string
  /** Nav label and the page title on the phone top bar. */
  label: string
  Screen: ComponentType
  /** Which shell badge counts against this item. */
  badge?: 'orders_waiting' | 'proposals_waiting'
}

export const ROUTES: Record<RouteId, RouteDef> = {
  stock: { id: 'stock', path: '/stock', label: 'Stock', Screen: StockScreen },
  orders: { id: 'orders', path: '/orders', label: 'Orders', Screen: OrdersScreen, badge: 'orders_waiting' },
  agents: { id: 'agents', path: '/agents', label: 'Agents', Screen: AgentsScreen, badge: 'proposals_waiting' },
  recipes: { id: 'recipes', path: '/recipes', label: 'Recipes', Screen: RecipesScreen },
  menu: { id: 'menu', path: '/menu', label: 'Menu items', Screen: MenuItemsScreen },
  ingredients: { id: 'ingredients', path: '/ingredients', label: 'Ingredients', Screen: IngredientsScreen },
  suppliers: { id: 'suppliers', path: '/suppliers', label: 'Suppliers', Screen: SuppliersScreen },
  'money.overview': { id: 'money.overview', path: '/money/overview', label: 'Overview', Screen: OverviewScreen },
  'money.sales': { id: 'money.sales', path: '/money/sales', label: 'Sales', Screen: SalesScreen },
  'money.expenses': { id: 'money.expenses', path: '/money/expenses', label: 'Expenses', Screen: ExpensesScreen },
  'money.reconcile': { id: 'money.reconcile', path: '/money/reconcile', label: 'Reconcile', Screen: ReconcileScreen },
  'money.pnl': { id: 'money.pnl', path: '/money/pnl', label: 'Profit & loss', Screen: ProfitLossScreen },
  'money.director': {
    id: 'money.director',
    path: '/money/director',
    label: "Director's account",
    Screen: DirectorsAccountScreen,
  },
  settings: { id: 'settings', path: '/settings', label: 'Settings', Screen: SettingsScreen },
  setup: { id: 'setup', path: '/setup', label: 'Setup checklist', Screen: SetupScreen },
}

/** Sidebar groups, in the design's order. Settings sits in the footer. */
export const NAV_GROUPS: ReadonlyArray<{ head: string; items: readonly RouteId[] }> = [
  { head: 'Every day', items: ['stock', 'orders', 'agents'] },
  { head: 'Menu', items: ['recipes', 'menu', 'ingredients', 'suppliers'] },
  {
    head: 'Money',
    items: ['money.overview', 'money.sales', 'money.expenses', 'money.reconcile', 'money.pnl', 'money.director'],
  },
]

export const DEFAULT_ROUTE: RouteId = 'stock'

/** Longest-prefix match; `null` when nothing matches (the caller redirects). */
export function resolveRoute(loc: Location): RouteDef | null {
  let best: RouteDef | null = null
  for (const r of Object.values(ROUTES)) {
    if (loc.path === r.path || loc.path.startsWith(r.path + '/')) {
      if (best === null || r.path.length > best.path.length) best = r
    }
  }
  // "#/money" alone lands on Overview.
  if (best === null && loc.path === '/money') return ROUTES['money.overview']
  return best
}
