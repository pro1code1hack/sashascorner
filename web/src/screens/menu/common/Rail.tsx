/**
 * The left rail shared by Recipes, Menu items and Ingredients
 * (recipes-menu-ingredients.md §V0.6).
 *
 * ≥ 1280px: a 210px column of uppercase heads and rows, with an optional footer
 * (the Menu tab's "New category"). Below: the rows as a chip strip under the
 * header, heads and footer dropped, scrolling inside itself.
 */
import type { ReactNode } from 'react'
import { FilterChip, cx } from '../../../components/ui'

export type RailItem =
  | { kind: 'head'; label: string }
  | { kind: 'row'; key: string; label: string; count?: number | null; active: boolean; onSelect: () => void }

export function RailColumn({ items, label, footer }: { items: readonly RailItem[]; label: string; footer?: ReactNode }) {
  return (
    <nav
      aria-label={label}
      className="hidden w-[210px] flex-none flex-col gap-0.5 overflow-y-auto border-r border-line-soft px-2.5 pb-4 pt-2.5 wide:flex"
    >
      {items.map((item, i) =>
        item.kind === 'head' ? (
          <div key={`h-${i}`} className="px-2.5 pb-1.5 pt-3.5 text-label font-bold uppercase tracking-[.08em] text-ink-3">
            {item.label}
          </div>
        ) : (
          <button
            key={item.key}
            type="button"
            aria-current={item.active ? 'true' : undefined}
            onClick={item.onSelect}
            title={item.label}
            className={cx(
              'flex min-h-9 w-full flex-none items-center justify-between gap-2 rounded-control px-2.5 text-left text-base transition-[background-color]',
              item.active ? 'bg-brand-wash font-bold text-brand-ink' : 'font-medium text-ink hover:bg-canvas',
            )}
          >
            <span className="min-w-0 truncate">{item.label}</span>
            {item.count !== undefined && item.count !== null && (
              <span className={cx('fig flex-none text-xs', item.active ? 'text-brand-ink' : 'text-ink-2')}>{item.count}</span>
            )}
          </button>
        ),
      )}
      {footer}
    </nav>
  )
}

export function RailChips({ items, label }: { items: readonly RailItem[]; label: string }) {
  const rows = items.filter((i): i is Extract<RailItem, { kind: 'row' }> => i.kind === 'row')
  return (
    <div
      role="group"
      aria-label={label}
      className="no-scrollbar flex flex-none items-center gap-2 overflow-x-auto border-b border-line-soft px-4 py-3 sm:px-5 wide:hidden"
    >
      {rows.map((r) => (
        <FilterChip key={r.key} active={r.active} onClick={r.onSelect} count={r.count ?? undefined} size={36}>
          {r.label}
        </FilterChip>
      ))}
    </div>
  )
}
