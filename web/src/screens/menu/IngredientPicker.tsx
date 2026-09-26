/**
 * Ingredient picker for a recipe line (owner, 2026-09-26: the native select of
 * 113 names "is not usable at all"). Type to search name, category or supplier;
 * narrow by category; arrows + Enter to pick, Escape to close. Retired
 * ingredients are hidden unless the line already uses one.
 */
import { useEffect, useId, useMemo, useRef, useState } from 'react'
import { cx } from '../../components/ui'
import type { IngredientRow } from '../../lib/types/menu'
import { unitPrice, unitWord } from './common/figures'

const norm = (s: string) => s.toLowerCase().normalize('NFKD').replace(/[̀-ͯ]/g, '')

function haystack(r: IngredientRow): string {
  return norm([r.name, r.category ?? '', ...r.suppliers.map((s) => s.name)].join(' '))
}

export function IngredientPicker({
  value,
  options,
  inRecipe,
  onPick,
  autoOpen = false,
}: {
  value: number | null
  options: IngredientRow[]
  /** Ingredients already on other lines of this recipe, marked in the list. */
  inRecipe: Set<number>
  onPick: (id: number) => void
  autoOpen?: boolean
}) {
  const [open, setOpen] = useState(autoOpen)
  const [q, setQ] = useState('')
  const [cat, setCat] = useState<string | null>(null)
  const [active, setActive] = useState(0)
  const root = useRef<HTMLDivElement>(null)
  const trigger = useRef<HTMLButtonElement>(null)
  const search = useRef<HTMLInputElement>(null)
  const list = useRef<HTMLUListElement>(null)
  const listId = useId()
  const selected = options.find((o) => o.ingredient_id === value)

  const usable = useMemo(() => options.filter((o) => !o.retired || o.ingredient_id === value), [options, value])
  const cats = useMemo(() => {
    const m = new Map<string, number>()
    for (const o of usable) m.set(o.category ?? 'Other', (m.get(o.category ?? 'Other') ?? 0) + 1)
    return [...m.entries()].sort((a, b) => a[0].localeCompare(b[0]))
  }, [usable])

  const shown = useMemo(() => {
    const words = norm(q).split(/\s+/).filter(Boolean)
    const rows = usable.filter(
      (o) => (cat === null || (o.category ?? 'Other') === cat) && words.every((w) => haystack(o).includes(w)),
    )
    const first = words[0]
    return rows.sort((a, b) => {
      if (first) {
        // Names that start with what was typed come first.
        const sa = norm(a.name).startsWith(first) ? 0 : 1
        const sb = norm(b.name).startsWith(first) ? 0 : 1
        if (sa !== sb) return sa - sb
        return a.name.localeCompare(b.name)
      }
      return (a.category ?? 'Other').localeCompare(b.category ?? 'Other') || a.name.localeCompare(b.name)
    })
  }, [usable, q, cat])
  const grouped = q.trim() === '' && cat === null

  useEffect(() => setActive(0), [q, cat])
  useEffect(() => {
    if (!open) return
    search.current?.focus()
    const close = (e: MouseEvent) => {
      if (root.current && !root.current.contains(e.target as Node)) setOpen(false)
    }
    document.addEventListener('mousedown', close)
    return () => document.removeEventListener('mousedown', close)
  }, [open])
  useEffect(() => {
    list.current?.querySelector(`[data-i="${active}"]`)?.scrollIntoView({ block: 'nearest' })
  }, [active])

  const pick = (id: number) => {
    onPick(id)
    setOpen(false)
    setQ('')
    trigger.current?.focus()
  }

  return (
    <div ref={root} className="relative min-w-0">
      <button
        ref={trigger}
        type="button"
        aria-haspopup="listbox"
        aria-expanded={open}
        onClick={() => setOpen((v) => !v)}
        className={cx(
          'flex h-10 w-full min-w-0 items-center gap-2 rounded-button border bg-surface px-3 text-left text-md',
          open ? 'border-brand ring-3 ring-brand-wash' : 'border-line-control hover:border-line-strong',
        )}
      >
        <span className={cx('min-w-0 flex-1 truncate', !selected && 'text-ink-3')}>
          {selected ? selected.name : 'Pick an ingredient…'}
        </span>
        {selected?.category && <span className="flex-none text-sm text-ink-3 max-sm:hidden">{selected.category}</span>}
        <svg aria-hidden="true" width="12" height="12" viewBox="0 0 12 12" className="flex-none text-ink-2">
          <path d="M2.5 4.5 6 8l3.5-3.5" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" />
        </svg>
      </button>
      {open && (
        <div
          className="absolute left-0 top-[calc(100%+4px)] z-40 flex w-[min(560px,calc(100vw-32px))] flex-col rounded-card border border-line bg-surface shadow-login"
          onKeyDown={(e) => {
            if (e.key === 'Escape') {
              e.preventDefault()
              setOpen(false)
              trigger.current?.focus()
            } else if (e.key === 'ArrowDown') {
              e.preventDefault()
              setActive((a) => Math.min(a + 1, shown.length - 1))
            } else if (e.key === 'ArrowUp') {
              e.preventDefault()
              setActive((a) => Math.max(a - 1, 0))
            } else if (e.key === 'Enter') {
              e.preventDefault()
              const o = shown[active]
              if (o) pick(o.ingredient_id)
            }
          }}
        >
          <div className="flex flex-col gap-2 border-b border-line p-2.5">
            <div className="flex h-10 items-center gap-2 rounded-button border border-line-control px-3 focus-within:border-brand">
              <svg aria-hidden="true" width="14" height="14" viewBox="0 0 16 16" className="flex-none text-ink-3">
                <circle cx="7" cy="7" r="5" fill="none" stroke="currentColor" strokeWidth="1.8" />
                <path d="m11 11 3.5 3.5" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" />
              </svg>
              <input
                ref={search}
                value={q}
                onChange={(e) => setQ(e.target.value)}
                role="combobox"
                aria-expanded="true"
                aria-controls={listId}
                aria-activedescendant={shown[active] ? `${listId}-${shown[active].ingredient_id}` : undefined}
                aria-label="Search ingredients"
                placeholder="Search by name, category or supplier"
                className="h-full w-full min-w-0 bg-transparent text-md outline-none placeholder:text-ink-3"
              />
              {q && (
                <button type="button" onClick={() => setQ('')} className="text-sm text-ink-2 hover:text-ink">
                  Clear
                </button>
              )}
            </div>
            <div className="flex flex-wrap gap-1.5" role="group" aria-label="Category">
              <CatChip active={cat === null} onClick={() => setCat(null)} label="All" count={usable.length} />
              {cats.map(([c, n]) => (
                <CatChip key={c} active={cat === c} onClick={() => setCat(cat === c ? null : c)} label={c} count={n} />
              ))}
            </div>
          </div>
          <ul ref={list} id={listId} role="listbox" aria-label="Ingredients" className="max-h-[min(360px,50vh)] overflow-y-auto py-1">
            {shown.length === 0 && <li className="px-3 py-4 text-center text-sm text-ink-2">Nothing matches “{q}”.</li>}
            {shown.map((o, i) => {
              const head = grouped && (i === 0 || (shown[i - 1]?.category ?? 'Other') !== (o.category ?? 'Other'))
              const preferred = o.suppliers.find((s) => s.is_preferred) ?? o.suppliers[0]
              return (
                <li key={o.ingredient_id} role="presentation">
                  {head && (
                    <div className="px-3 pb-1 pt-2.5 text-label font-bold uppercase tracking-[.06em] text-ink-3">
                      {o.category ?? 'Other'}
                    </div>
                  )}
                  <div
                    id={`${listId}-${o.ingredient_id}`}
                    data-i={i}
                    role="option"
                    aria-selected={o.ingredient_id === value}
                    onMouseMove={() => setActive(i)}
                    onClick={() => pick(o.ingredient_id)}
                    className={cx(
                      'flex cursor-pointer items-center gap-3 px-3 py-2',
                      i === active && 'bg-brand-wash',
                      o.ingredient_id === value && 'font-bold',
                    )}
                  >
                    <span className="min-w-0 flex-1">
                      <span className="block truncate text-base">{o.name}</span>
                      <span className="block truncate text-xs text-ink-2">
                        {[!grouped ? o.category : null, preferred?.name, inRecipe.has(o.ingredient_id) ? 'already in this recipe' : null]
                          .filter(Boolean)
                          .join(' · ')}
                      </span>
                    </span>
                    <span className={cx('fig flex-none text-sm text-ink-2', o.unit_cost.is_estimate && 'italic')}>
                      {o.unit_cost.pence === null ? 'no price' : `${unitPrice(o.unit_cost.pence)} / ${unitWord(o.unit)}`}
                    </span>
                  </div>
                </li>
              )
            })}
          </ul>
          <div className="border-t border-line px-3 py-1.5 text-xs text-ink-3 max-sm:hidden">
            ↑ ↓ to move · Enter to pick · Esc to close · <em>italic</em> prices are estimates
          </div>
        </div>
      )}
    </div>
  )
}

function CatChip({ active, onClick, label, count }: { active: boolean; onClick: () => void; label: string; count: number }) {
  return (
    <button
      type="button"
      aria-pressed={active}
      onClick={onClick}
      className={cx(
        'flex h-8 flex-none items-center gap-1 whitespace-nowrap rounded-full border px-3 text-sm',
        active ? 'border-brand bg-brand-wash font-bold text-brand-ink' : 'border-line-control text-ink hover:bg-canvas',
      )}
    >
      {label}
      <span className="text-xs opacity-60">{count}</span>
    </button>
  )
}
