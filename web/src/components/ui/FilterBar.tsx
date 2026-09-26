/**
 * A top filter bar: compact labelled selects on one wrapping row, and a row of
 * removable chips for whatever is active with "Clear all". Generic; the screen
 * owns the state and the filtering.
 *
 *   <FilterBar label="Filter ingredients" search={<SearchInput … />}>
 *     <FilterSelect label="Category" value={cat} onChange={setCat} options={…} />
 *   </FilterBar>
 *   <ActiveFilters chips={[{ key: 'cat', label: 'Category: Syrup', onRemove }]} onClearAll={…} />
 */
import type { ReactNode } from 'react'
import { cx } from './cx'

export function FilterBar({
  label,
  search,
  children,
  trailing,
  className,
}: {
  label: string
  search?: ReactNode
  children?: ReactNode
  /** Right-aligned (sort, counts). Wraps under at narrow widths. */
  trailing?: ReactNode
  className?: string
}) {
  return (
    <div role="group" aria-label={label} className={cx('flex flex-wrap items-center gap-2', className)}>
      {search && <div className="w-full min-w-0 sm:w-[240px] sm:flex-none">{search}</div>}
      {children}
      {trailing && <div className="ml-auto flex flex-wrap items-center gap-2">{trailing}</div>}
    </div>
  )
}

export interface FilterOption {
  value: string
  label: string
}

/**
 * A native select styled as a pill. `allValue` is the "no filter" option; any
 * other value marks the pill active. The visible text is the chosen option, so
 * the first option should read as its own label ("All categories").
 */
export function FilterSelect({
  label,
  value,
  onChange,
  options,
  allValue = 'all',
  className,
}: {
  label: string
  value: string
  onChange: (v: string) => void
  options: readonly FilterOption[]
  allValue?: string
  className?: string
}) {
  const active = value !== allValue
  return (
    <select
      aria-label={label}
      value={value}
      onChange={(e) => onChange(e.target.value)}
      className={cx(
        'h-[34px] max-w-full min-w-0 cursor-pointer rounded-full border px-3 text-base outline-none',
        'focus-visible:border-brand focus-visible:ring-3 focus-visible:ring-brand-wash',
        active ? 'border-brand-line bg-brand-wash font-bold text-brand-ink' : 'border-line-control bg-surface text-ink',
        className,
      )}
    >
      {options.map((o) => (
        <option key={o.value} value={o.value}>
          {o.label}
        </option>
      ))}
    </select>
  )
}

/** A two-state pill ("Estimated prices only"). */
export function FilterToggle({
  active,
  onToggle,
  count,
  children,
}: {
  active: boolean
  onToggle: () => void
  count?: ReactNode
  children: ReactNode
}) {
  return (
    <button
      type="button"
      aria-pressed={active}
      onClick={onToggle}
      className={cx(
        'flex h-[34px] flex-none items-center gap-1.5 whitespace-nowrap rounded-full border px-3 text-base',
        active ? 'border-brand-line bg-brand-wash font-bold text-brand-ink' : 'border-line-control bg-surface text-ink hover:bg-canvas',
      )}
    >
      {children}
      {count !== undefined && <span className="fig text-xs opacity-60">{count}</span>}
    </button>
  )
}

export interface ActiveFilterChip {
  key: string
  label: ReactNode
  onRemove: () => void
}

/** Removable chips for the active filters, plus "Clear all". Renders nothing when none. */
export function ActiveFilters({
  chips,
  onClearAll,
  summary,
  className,
}: {
  chips: readonly ActiveFilterChip[]
  onClearAll: () => void
  /** Leading text, e.g. "24 of 113". Shown even with no chips. */
  summary?: ReactNode
  className?: string
}) {
  if (chips.length === 0 && !summary) return null
  return (
    <div className={cx('flex flex-wrap items-center gap-1.5 text-sm', className)}>
      {summary && <span className="mr-1 text-ink-2">{summary}</span>}
      {chips.map((c) => (
        <button
          key={c.key}
          type="button"
          onClick={c.onRemove}
          aria-label={`Remove filter: ${typeof c.label === 'string' ? c.label : c.key}`}
          className="inline-flex h-7 items-center gap-1 rounded-full border border-line-strong bg-surface pl-2.5 pr-2 hover:bg-canvas"
        >
          {c.label}
          <span aria-hidden="true" className="text-ink-2">
            ×
          </span>
        </button>
      ))}
      {chips.length > 0 && (
        <button type="button" onClick={onClearAll} className="ml-1 text-brand-ink underline">
          Clear all
        </button>
      )}
    </div>
  )
}
