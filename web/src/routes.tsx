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
const OrdersArea = screen(() => import('./screens/orders/OrdersScreen'), 'OrdersArea')
const AgentsScreen = screen(() => import('./screens/agents/AgentsScreen'), 'AgentsScreen')
/** Recipes live inside Menu now (#/menu/recipes); #/recipes redirects there. */
const RecipesRedirect = screen(() => import('./screens/menu/MenuArea'), 'RecipesRedirect')
const MenuArea = screen(() => import('./screens/menu/MenuArea'), 'MenuArea')
const IngredientsScreen = screen(() => import('./screens/ingredients/IngredientsScreen'), 'IngredientsScreen')
const SuppliersScreen = screen(() => import('./screens/suppliers/SuppliersScreen'), 'SuppliersScreen')
const OverviewScreen = screen(() => import('./screens/money/OverviewScreen'), 'OverviewScreen')
const SalesScreen = screen(() => import('./screens/money/SalesScreen'), 'SalesScreen')
const ExpensesArea = screen(() => import('./screens/money/ExpensesArea'), 'ExpensesArea')
const TransactionsScreen = screen(() => import('./screens/money/TransactionsScreen'), 'TransactionsScreen')
const MembersArea = screen(() => import('./screens/members/MembersArea'), 'MembersArea')
const SettingsScreen = screen(() => import('./screens/settings/SettingsScreen'), 'SettingsScreen')
const SetupScreen = screen(() => import('./screens/setup/SetupScreen'), 'SetupScreen')
/** The public website's admin, moved in from the site's /admin (owner, 2026-09-28). */
const WebsiteToday = screen(() => import('./screens/website/TodayScreen'), 'TodayScreen')
const WebsiteBookings = screen(() => import('./screens/website/BookingsScreen'), 'BookingsScreen')
const WebsiteMessages = screen(() => import('./screens/website/MessagesScreen'), 'MessagesScreen')
const WebsiteEvents = screen(() => import('./screens/website/EventsScreen'), 'EventsScreen')
const WebsiteMenu = screen(() => import('./screens/website/SiteMenuScreen'), 'SiteMenuScreen')
const WebsitePhotos = screen(() => import('./screens/website/PhotosScreen'), 'PhotosScreen')
const WebsiteDetails = screen(() => import('./screens/website/CafeDetailsScreen'), 'CafeDetailsScreen')

export type RouteId =
  | 'stock'
  | 'members'
  | 'orders'
  | 'agents'
  | 'recipes'
  | 'menu'
  | 'ingredients'
  | 'suppliers'
  | 'money.overview'
  | 'money.sales'
  | 'money.expenses'
  | 'money.transactions'
  | 'settings'
  | 'setup'
  | 'website.today'
  | 'website.bookings'
  | 'website.messages'
  | 'website.events'
  | 'website.menu'
  | 'website.photos'
  | 'website.details'

export interface RouteDef {
  id: RouteId
  /** Hash path, "/money/sales". */
  path: string
  /** Nav label and the page title on the phone top bar. */
  label: string
  Screen: ComponentType
  /** Which shell badge counts against this item. */
  badge?: 'orders_waiting' | 'proposals_waiting' | 'messages_new' | 'photos_missing'
}

export const ROUTES: Record<RouteId, RouteDef> = {
  stock: { id: 'stock', path: '/stock', label: 'Stock', Screen: StockScreen },
  /** Sasha's Corner Rewards: list, one page per member, insights, campaigns, staff, programme. */
  members: { id: 'members', path: '/members', label: 'Members', Screen: MembersArea },
  /** Orders, Draft orders, Shop runs; one page per order (owner, 2026-09-26). */
  orders: { id: 'orders', path: '/orders', label: 'Orders', Screen: OrdersArea, badge: 'orders_waiting' },
  agents: { id: 'agents', path: '/agents', label: 'Agents', Screen: AgentsScreen, badge: 'proposals_waiting' },
  recipes: { id: 'recipes', path: '/recipes', label: 'Recipes', Screen: RecipesRedirect },
  menu: { id: 'menu', path: '/menu', label: 'Menu items', Screen: MenuArea },
  ingredients: { id: 'ingredients', path: '/ingredients', label: 'Ingredients', Screen: IngredientsScreen },
  suppliers: { id: 'suppliers', path: '/suppliers', label: 'Suppliers', Screen: SuppliersScreen },
  'money.overview': { id: 'money.overview', path: '/money/overview', label: 'Overview', Screen: OverviewScreen },
  'money.sales': { id: 'money.sales', path: '/money/sales', label: 'Sales', Screen: SalesScreen },
  'money.expenses': {
    id: 'money.expenses',
    path: '/money/expenses',
    label: 'Expenses',
    Screen: ExpensesArea,
  },
  'money.transactions': {
    id: 'money.transactions',
    path: '/money/transactions',
    label: 'Transactions',
    Screen: TransactionsScreen,
  },
  settings: { id: 'settings', path: '/settings', label: 'Settings', Screen: SettingsScreen },
  setup: { id: 'setup', path: '/setup', label: 'Setup checklist', Screen: SetupScreen },
  'website.today': { id: 'website.today', path: '/website', label: 'Today', Screen: WebsiteToday },
  'website.bookings': { id: 'website.bookings', path: '/website/bookings', label: 'Bookings', Screen: WebsiteBookings },
  'website.messages': {
    id: 'website.messages',
    path: '/website/messages',
    label: 'Messages',
    Screen: WebsiteMessages,
    badge: 'messages_new',
  },
  'website.events': { id: 'website.events', path: '/website/events', label: 'Events', Screen: WebsiteEvents },
  'website.menu': { id: 'website.menu', path: '/website/menu', label: 'Website menu', Screen: WebsiteMenu },
  'website.photos': {
    id: 'website.photos',
    path: '/website/photos',
    label: 'Photos',
    Screen: WebsitePhotos,
    badge: 'photos_missing',
  },
  'website.details': { id: 'website.details', path: '/website/details', label: 'Café details', Screen: WebsiteDetails },
}

/** Sidebar groups, in the design's order. Settings sits in the footer. */
export const NAV_GROUPS: ReadonlyArray<{ head: string; items: readonly RouteId[] }> = [
  { head: 'Every day', items: ['stock', 'members', 'agents'] },
  { head: 'Menu', items: ['menu', 'ingredients', 'suppliers'] },
  {
    head: 'Money',
    items: ['money.overview', 'money.sales', 'money.transactions', 'money.expenses', 'orders'],
  },
  {
    head: 'Website',
    items: [
      'website.today',
      'website.bookings',
      'website.messages',
      'website.events',
      'website.menu',
      'website.photos',
      'website.details',
    ],
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
