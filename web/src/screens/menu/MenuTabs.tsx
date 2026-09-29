/**
 * Items | Recipes: the two halves of the Menu area (owner, 2026-09-26: recipe
 * information belongs in the menu, not in its own nav entry). Plain links, so
 * the browser's back button and a reload both behave.
 *
 * A real <nav>, rendered in the header's `actions` slot: the subtitle is a <p>,
 * and a navigation landmark inside a paragraph is invalid HTML.
 */
import { cx } from '../../components/ui'
import { href } from '../../lib/router'

const TABS = [
  { id: 'items', label: 'Items', path: '/menu', hint: 'everything you sell' },
  { id: 'recipes', label: 'Recipes', path: '/menu/recipes', hint: 'set once, every item follows' },
] as const

/** The one-line description of a tab, for the page subtitle. */
export const MENU_TAB_HINT: Record<'items' | 'recipes', string> = {
  items: 'Everything you sell.',
  recipes: 'Set a recipe once; every item made from it follows.',
}

export function MenuTabs({ current }: { current: 'items' | 'recipes' }) {
  return (
    <nav className="inline-flex items-center gap-1" aria-label="Menu sections">
      {TABS.map((t) => (
        <a
          key={t.id}
          href={href(t.path)}
          aria-current={t.id === current ? 'page' : undefined}
          className={cx(
            'inline-flex h-9 items-center rounded-full px-3.5 text-base font-bold no-underline transition-colors',
            t.id === current ? 'bg-brand-wash text-brand-ink' : 'text-ink-2 hover:bg-canvas hover:text-ink',
          )}
        >
          {t.label}
        </a>
      ))}
    </nav>
  )
}
