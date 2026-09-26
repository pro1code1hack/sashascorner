/**
 * Items | Recipes: the two halves of the Menu area (owner, 2026-09-26: recipe
 * information belongs in the menu, not in its own nav entry). Plain links, so
 * the browser's back button and a reload both behave.
 */
import { cx } from '../../components/ui'
import { href } from '../../lib/router'

const TABS = [
  { id: 'items', label: 'Items', path: '/menu', hint: 'everything you sell' },
  { id: 'recipes', label: 'Recipes', path: '/menu/recipes', hint: 'set once, every item follows' },
] as const

export function MenuTabs({ current }: { current: 'items' | 'recipes' }) {
  return (
    <span className="inline-flex items-center gap-1" role="navigation" aria-label="Menu sections">
      {TABS.map((t) => (
        <a
          key={t.id}
          href={href(t.path)}
          title={t.hint}
          aria-current={t.id === current ? 'page' : undefined}
          className={cx(
            'inline-flex h-8 items-center rounded-full px-3.5 text-base font-bold no-underline transition-colors',
            t.id === current ? 'bg-brand-wash text-brand-ink' : 'text-ink-2 hover:bg-canvas hover:text-ink',
          )}
        >
          {t.label}
        </a>
      ))}
    </span>
  )
}
