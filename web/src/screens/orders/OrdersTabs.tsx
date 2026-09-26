/**
 * Orders | Draft orders | Shop runs: the three sub-pages of Orders (owner,
 * 2026-09-26: "orders should be a separate page … there 3 sub pages"). Plain
 * links styled like MenuTabs, so back and reload behave.
 */
import { cx } from '../../components/ui'
import { href } from '../../lib/router'

export type OrdersTab = 'orders' | 'drafts' | 'runs'

const TABS: ReadonlyArray<{ id: OrdersTab; label: string; path: string; hint: string }> = [
  { id: 'orders', label: 'Orders', path: '/orders', hint: 'every supplier order, received into stock' },
  { id: 'drafts', label: 'Draft orders', path: '/orders/drafts', hint: 'what needs ordering next, per supplier' },
  { id: 'runs', label: 'Shop runs', path: '/orders/shop-runs', hint: 'bought at a shop because it could not wait' },
]

export function OrdersTabs({ current, toConfirm }: { current: OrdersTab; toConfirm?: number }) {
  return (
    <span className="inline-flex flex-wrap items-center gap-1" role="navigation" aria-label="Orders sections">
      {TABS.map((t) => (
        <a
          key={t.id}
          href={href(t.path)}
          title={t.hint}
          aria-current={t.id === current ? 'page' : undefined}
          className={cx(
            'inline-flex h-8 items-center gap-1.5 rounded-full px-3.5 text-base font-bold no-underline transition-colors',
            t.id === current ? 'bg-brand-wash text-brand-ink' : 'text-ink-2 hover:bg-canvas hover:text-ink',
          )}
        >
          {t.label}
          {t.id === 'orders' && toConfirm ? <span className="rounded-full bg-alert px-1.5 text-xs text-white">{toConfirm}</span> : null}
        </a>
      ))}
    </span>
  )
}
