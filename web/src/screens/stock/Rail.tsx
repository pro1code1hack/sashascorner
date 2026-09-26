/**
 * The left rail of Stock and Suppliers (stock-orders-suppliers.md §0.1).
 *
 * ≥ 1280px: a 210px column with uppercase group heads and rows.
 * Below: a horizontal chip strip under the header, heads dropped
 * (`railChips = rail.filter(isRow)`), scrolling inside itself.
 *
 * Built locally because the kit has no rail primitive; FilterChip is reused for
 * the strip so selection colours match everywhere (`on()` in the design).
 */
import type { ReactNode } from 'react'
import { FilterChip, cx } from '../../components/ui'

export type RailItem =
  | { kind: 'head'; label: string }
  | { kind: 'row'; key: string; label: ReactNode; count?: number | null; active: boolean; onSelect: () => void }

export function RailColumn({ items, label }: { items: readonly RailItem[]; label: string }) {
  return (
    <nav
      aria-label={label}
      className="hidden w-[210px] flex-none flex-col gap-0.5 overflow-y-auto border-r border-line-soft px-2.5 pb-4 pt-2.5 wide:flex"
    >
      {items.map((item, i) =>
        item.kind === 'head' ? (
          <div
            key={`h-${i}`}
            className="px-2.5 pb-1.5 pt-3.5 text-label font-bold uppercase tracking-[.08em] text-ink-3"
          >
            {item.label}
          </div>
        ) : (
          <button
            key={item.key}
            type="button"
            aria-current={item.active ? 'true' : undefined}
            onClick={item.onSelect}
            className={cx(
              'flex min-h-9 w-full items-center gap-2 rounded-control px-2.5 text-left text-base transition-[background-color]',
              item.active ? 'bg-brand-wash font-bold text-brand-ink' : 'font-medium text-ink hover:bg-canvas',
            )}
          >
            <span className="min-w-0 flex-1 truncate">{item.label}</span>
            {item.count !== undefined && item.count !== null && (
              <span className="fig text-xs opacity-60">{item.count}</span>
            )}
          </button>
        ),
      )}
    </nav>
  )
}

export function RailChips({ items, label }: { items: readonly RailItem[]; label: string }) {
  const rows = items.filter((i): i is Extract<RailItem, { kind: 'row' }> => i.kind === 'row')
  return (
    <div
      role="group"
      aria-label={label}
      className="scroll-x flex flex-none items-center gap-2 border-b border-line-soft px-4 py-3 sm:px-5 wide:hidden"
    >
      {rows.map((r) => (
        <FilterChip key={r.key} active={r.active} onClick={r.onSelect} count={r.count ?? undefined} size={36}>
          {r.label}
        </FilterChip>
      ))}
    </div>
  )
}
