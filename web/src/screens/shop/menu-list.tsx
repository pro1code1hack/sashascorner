/**
 * The online-ordering bits of the Menu items list (owner, 2026-09-29: the shop
 * menu and the menu are one list): one batched read of the shop catalogue keyed
 * by item name, the "Online" pills for a row, the online filter, and the bulk
 * "Sold out today" / "Back on sale" strip for selected rows
 * (`POST /api/shop-admin/products/bulk`).
 */
import { useMemo } from 'react'
import { Button, Pill } from '../../components/ui'
import { LIVE } from '../../lib/api'
import { SHOP_KEY, catalogueWrites, useShopCatalogue } from '../../lib/shop-api'
import type { ProductAdmin } from '../../lib/types/shop'
import { OutcomeLine, useWrite } from '../stock/writes'
import { plural } from './shared'

export type OnlineFilter = 'all' | 'online' | 'notonline' | 'soldout'

export const ONLINE_OPTIONS: ReadonlyArray<{ value: OnlineFilter; label: string }> = [
  { value: 'all', label: 'Online or not' },
  { value: 'online', label: 'Shown online' },
  { value: 'notonline', label: 'Not online' },
  { value: 'soldout', label: 'Sold out online' },
]

/** Shop products by menu item name; `undefined` until read (or in fixture mode). */
export function useShopProductsByName(): Map<string, ProductAdmin> | undefined {
  const cat = useShopCatalogue()
  return useMemo(() => (cat.data ? new Map(cat.data.products.map((p) => [p.item_name, p])) : undefined), [cat.data])
}

/** Is a product listed in the shop right now? */
export function isOnline(p: ProductAdmin | undefined): boolean {
  return Boolean(p && p.visible && p.ops_active && p.category_ops_name !== null)
}

export function matchesOnline(filter: OnlineFilter, p: ProductAdmin | undefined): boolean {
  switch (filter) {
    case 'online':
      return isOnline(p)
    case 'notonline':
      return !isOnline(p)
    case 'soldout':
      return Boolean(p && !p.available)
    default:
      return true
  }
}

/** "Shown" / "Hidden" plus "Sold out" for a list row or a card. */
export function OnlinePills({ p, compact = false }: { p: ProductAdmin | undefined; compact?: boolean }) {
  if (!LIVE) return null
  if (!p) return <Pill tone="muted">{compact ? 'Not synced' : 'Not in the shop yet'}</Pill>
  return (
    <span className="inline-flex flex-wrap gap-1">
      {isOnline(p) ? <Pill tone="brand">Shown</Pill> : <Pill tone="muted">Hidden</Pill>}
      {!p.available && <Pill tone="warn">Sold out</Pill>}
    </span>
  )
}

/** The strip above the list while rows are selected. */
export function BulkOnline({ selected, onClear }: { selected: number[]; onClear: () => void }) {
  const w = useWrite()
  if (selected.length === 0) return null
  const run = (patch: { available?: boolean; visible?: boolean }, what: string) =>
    void w.run(() => catalogueWrites.productsBulk(selected, patch), { invalidate: [SHOP_KEY], ok: () => `${plural(selected.length, 'item')} ${what}.`, after: onClear })
  return (
    <div className="flex flex-wrap items-center gap-2 text-sm">
      <span className="font-bold text-ink">{selected.length} selected</span>
      <Button size="sm" pending={w.pending} pendingLabel="Applying…" onClick={() => run({ available: false }, 'marked sold out today')}>
        Sold out today
      </Button>
      <Button size="sm" disabled={w.pending} onClick={() => run({ available: true }, 'back on sale')}>
        Back on sale
      </Button>
      <Button size="sm" disabled={w.pending} onClick={() => run({ visible: false }, 'hidden online')}>
        Hide online
      </Button>
      <Button size="sm" disabled={w.pending} onClick={() => run({ visible: true }, 'shown online')}>
        Show online
      </Button>
      <Button variant="ghost" size="sm" onClick={onClear}>
        Clear
      </Button>
      <OutcomeLine outcome={w.outcome} />
    </div>
  )
}
