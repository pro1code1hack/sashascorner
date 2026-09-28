/**
 * Shapes of the site API's `GET /api/admin/menu` and its writes, as
 * site/backend/sashasite/menu_admin.py defines them. Read through the back
 * office's forward (`siteGet('/menu')`).
 *
 * Names, sizes and prices are read-only here: they belong to the Café Ops menu
 * (or, until that has categories, the TV boards file). Only the website's own
 * presentation overlay is written.
 */
import { gbp } from '../../lib/format'

export type MenuSource = 'ops' | 'board'

export interface SiteSize {
  code: string
  label: string
  price_pence: number | null
}

export interface SiteSeason {
  name: string
  starts_on: string | null
  ends_on: string | null
  recurring_annually: boolean
  /** False: hidden from the public menu until its season opens. */
  in_season: boolean
}

export interface SiteItemWeb {
  description: string | null
  signature: boolean
  hidden: boolean
  position: number | null
  use_ops_note: boolean
}

export interface SiteMenuItem {
  /** The item name; what writes are keyed by. */
  key: string
  id: string
  name: string
  sizes: SiteSize[]
  seasonal: SiteSeason | null
  /** Café Ops `menu_item.note`; public only with `web.use_ops_note`. */
  ops_note: string | null
  has_photo: boolean
  /** Would the public menu show it right now (not hidden, in season, category shown)? */
  public: boolean
  web: SiteItemWeb
}

export interface SiteMenuCategory {
  name: string
  slug: string
  /** DRINKS | FOOD | OTHER from Café Ops; null for the board. */
  kind: string | null
  blurb: string | null
  hidden: boolean
  position: number | null
  items: SiteMenuItem[]
}

export interface DriftMismatch {
  board_name: string
  ops_name: string
  size: string
  board_pence: number | null
  ops_pence: number | null
}

export interface SiteMenuDrift {
  board_items: number
  ops_items: number
  matched: number
  price_mismatches: DriftMismatch[]
  board_only: string[]
  ops_only: string[]
  error?: string | null
}

export interface SiteMenuAdmin {
  source: MenuSource
  warnings: string[]
  categories: SiteMenuCategory[]
  unassigned: SiteMenuItem[]
  drift: SiteMenuDrift
}

/** `PUT /menu/categories/{slug}`. A null blurb clears the website's override. */
export interface CategoryPut {
  blurb?: string | null
  hidden?: boolean
}

/** `PUT /menu/items/{key}`. A null description means none on the website. */
export interface ItemPut {
  description?: string | null
  signature?: boolean
  hidden?: boolean
  use_ops_note?: boolean
}

/** `POST /menu/order`: the whole order, categories and every category's items. */
export interface OrderBody {
  categories: string[]
  items: Record<string, string[]>
}

/* ------------------------------------------------------------- helpers --- */

export function plural(n: number, one: string, many = `${one}s`): string {
  return `${n} ${n === 1 ? one : many}`
}

export function sizeText(z: SiteSize): string {
  const price = z.price_pence === null ? 'no price' : gbp(z.price_pence)
  return z.code === 'One' ? price : `${z.code} ${price}`
}

export function kindLabel(kind: string | null): string | null {
  if (kind === null) return null
  const k = kind.toUpperCase()
  return k === 'FOOD' ? 'Food' : k === 'DRINKS' ? 'Drinks' : 'Other'
}

/** The Café Ops menu list, searched for this name (items here have no Café Ops id). */
export function opsMenuHref(name: string): string {
  return `#/menu?q=${encodeURIComponent(name)}`
}

/** No description would show: neither the website's own, nor the note in its place. */
export function lacksDescription(it: SiteMenuItem): boolean {
  if (it.web.use_ops_note && it.ops_note) return false
  return !it.web.description
}

/** Drift names arrive as "Name  [category slug]" from the boards; show the name. */
export function driftName(raw: string): string {
  return raw.replace(/\s+\[[^\]]*\]$/, '').trim()
}
