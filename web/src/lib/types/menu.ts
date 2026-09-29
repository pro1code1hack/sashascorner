/**
 * Types for the Recipes, Menu items and Ingredients screens, transcribed from
 * `cafeops/api/areas/menu_schemas.py` (the contract).
 *
 * Money: integer pence where the database stores an integer (`price_pence`),
 * an exact decimal STRING of pence where it is derived (`Cost.pence`,
 * `*_pence: string`). Quantities are always strings. A missing cost is
 * `pence: null`, never 0 (invariant 8).
 */
import type { ComponentRole, Cost, PriceSource, SizeCode, Unit } from '../types'

export type { ComponentRole, Cost, PriceSource, SizeCode, Unit }

export type ChangeStatus = 'new' | 'off' | 'on' | 'changed' | 'unchanged'
export type MenuKind = 'DRINKS' | 'FOOD' | 'OTHER'

export interface ChangeItem {
  key: string
  menu_item_id: number | null
  name: string
  size_code: SizeCode | null
  status: ChangeStatus
  on_till: boolean
  price_before: number | null
  price_after: number | null
  cost_before: Cost
  cost_after: Cost
  cost_delta_pence: string | null
  margin_pct_before: number | null
  margin_pct_after: number | null
  labour_cost_pence: string | null
  true_margin_after_pence: string | null
  margin_per_minute_before_pence: string | null
  margin_per_minute_after_pence: string | null
  prep_seconds: number | null
  prep_is_estimate: boolean
  units_sold: string
}

export interface ChangeImpact {
  affected_item_count: number
  items: ChangeItem[]
  cost_delta_pence_per_item: string | null
  cost_delta_pence_range: [string, string] | null
  monthly_cogs_delta_pence: string | null
  revenue_delta_pence: string | null
  worst_margin_after: ChangeItem | null
  untimed_count: number
  estimated_count: number
  window_days: number
  warnings: string[]
}

/* ------------------------------------------------------------ recipes --- */

export interface RecipeRailTemplate {
  template_id: number
  name: string
  category: string | null
  sizes: SizeCode[]
  item_count: number
}
export interface RecipeRailProposal {
  proposal_id: string
  name: string
  category: string | null
  menu_item_count: number
  is_hollow: boolean
  name_is_ambiguous: boolean
  blocked_reason: string | null
}
export interface RecipesRail {
  templates: RecipeRailTemplate[]
  proposals: RecipeRailProposal[]
  one_offs: { menu_item_id: number; name: string }[]
}

export interface EditorComponent {
  component_id: number
  role: ComponentRole
  ingredient_id: number | null
  ingredient_name: string | null
  unit: Unit | null
  qty_by_size: Partial<Record<SizeCode, string>>
  is_substitutable: boolean
  is_required: boolean
  unit_cost: Cost | null
}
export interface EditorOption {
  option_id: number
  axis_id: number
  name: string
  ingredient_id: number | null
  ingredient_name: string | null
  unit: Unit | null
  qty_by_size: Partial<Record<SizeCode, string>> | null
  price_delta_pence: number
  season_id: number | null
  season_name: string | null
  active: boolean
  missing_ingredient: boolean
  menu_item_ids: Partial<Record<SizeCode, number>>
}
export interface EditorAxis {
  axis_id: number
  name: string
  role: ComponentRole
  options: EditorOption[]
}
export interface EditorItem {
  menu_item_id: number
  name: string
  size_code: SizeCode | null
  option_id: number | null
  price_pence: number
  active: boolean
  on_till: boolean
  cost: Cost
  prep_seconds: number | null
  prep_is_estimate: boolean
  availability: string
}
export interface Swap {
  modifier_id: number
  name: string
  action: 'SUBSTITUTE' | 'ADD' | 'SCALE'
  target_role: ComponentRole
  ingredient_id: number | null
  ingredient_name: string | null
  qty_delta: string | null
  qty_multiplier: string | null
  price_pence: number
  price_is_estimate: boolean | null
  is_active: boolean
  applies_here: boolean
  version_from: string | null
}
export interface HistoryEntry {
  effective_from: string
  actor: string
  kind: string
  summary: string
  lines: string[]
}
export interface RecipeHistory {
  template_id: number
  baseline_from: string | null
  entries: HistoryEntry[]
}
export interface RecipeEditor {
  template_id: number
  name: string
  category: string | null
  version: string
  as_of: string
  sizes: SizeCode[]
  prep_seconds_by_size: Partial<Record<SizeCode, number>>
  prep_is_estimate: boolean | null
  base_price_by_size: Partial<Record<SizeCode, number | null>>
  base_price_disagrees: SizeCode[]
  loaded_hourly_rate_pence: number | null
  components: EditorComponent[]
  axes: EditorAxis[]
  items: EditorItem[]
  swaps: Swap[]
  history: RecipeHistory
  item_name_pattern: string
  warnings: string[]
}

export type TemplateOp =
  | { op: 'component.qty'; component_id: number; qty_by_size: Partial<Record<SizeCode, string>> }
  | {
      op: 'component.set'
      component_id: number
      role?: ComponentRole
      ingredient_id?: number | null
      is_substitutable?: boolean
      is_required?: boolean
    }
  | {
      op: 'component.add'
      role: ComponentRole
      ingredient_id: number | null
      qty_by_size: Partial<Record<SizeCode, string>>
      is_substitutable: boolean
      is_required: boolean
    }
  | { op: 'component.remove'; component_id: number }
  | { op: 'prep.set'; prep_seconds_by_size: Partial<Record<SizeCode, number>>; is_estimate: boolean }
  | { op: 'price.base'; base_price_pence_by_size: Partial<Record<SizeCode, number>> }
  | {
      op: 'option.set'
      option_id: number
      name?: string
      ingredient_id?: number | null
      qty_by_size?: Partial<Record<SizeCode, string>> | null
      price_delta_pence?: number
      season_id?: number | null
    }
  | {
      op: 'option.add'
      axis_id: number
      name: string
      ingredient_id: number | null
      qty_by_size: Partial<Record<SizeCode, string>> | null
      price_delta_pence: number
      season_id: number | null
    }
  | { op: 'option.active'; option_id: number; active: boolean }
  | { op: 'option.remove'; option_id: number }
  | { op: 'template.rename'; name: string }

export interface ChangesetPreview {
  template_id: number
  base_version: string
  at: string
  diff: string[]
  refusals: { op_index: number; message: string }[]
  warnings: string[]
  pos_actions: string[]
  impact: ChangeImpact
  items_created: ChangeItem[]
  items_deactivated: ChangeItem[]
  loaded_hourly_rate_pence: number | null
  writes_nothing: boolean
}
export interface ChangesetApplied {
  template_id: number
  effective_from: string
  new_version: string
  summary: string
  diff: string[]
  menu_items_created: number[]
  menu_items_repriced: number[]
  rollup_items_recosted: number
  pos_actions: string[]
}
export interface Season {
  season_id: number
  name: string
  starts_on: string
  ends_on: string
  is_recurring_annually: boolean
  is_open_today: boolean
}
export interface SwapPreview {
  modifier_id: number
  name: string
  diff: string[]
  refusals: string[]
  warnings: string[]
  sales_in_window: number
  revenue_delta_pence: number | null
  window_days: number
  recipes_affected: string[]
}
export interface ProposalCost {
  menu_item_id: number
  name: string
  size_code: SizeCode | null
  price_pence: number
  cost_before: Cost
  cost_after: Cost
  margin_pct_before: number | null
  margin_pct_after: number | null
}
export interface ProposalPreview {
  proposal_id: string
  name: string
  blocked_reason: string | null
  would_repoint: number
  would_close_manual_lines: number
  components: number
  options: number
  sizes: SizeCode[]
  items_skipped_other_template: string[]
  items_unresolved_option: string[]
  cost_changes: ProposalCost[]
  warnings: string[]
  summary: string | null
}

/* ---------------------------------------------------------- menu items --- */

export interface MenuSize {
  menu_item_id: number
  size_code: SizeCode | null
  price_pence: number
  active: boolean
  on_till: boolean
  cost: Cost
  margin_pct: number | null
  labour_cost_pence: string | null
  manual_recipe: boolean
  data_quality_flag: string | null
  /** Optional: absent in fixtures recorded before these fields existed. */
  has_recipe?: boolean
  prep_seconds?: number | null
  prep_is_estimate?: boolean | null
  prep_is_override?: boolean
  margin_per_minute_pence?: string | null
}
export interface MenuGroup {
  key: string
  anchor_id: number
  name: string
  category: string | null
  kind: MenuKind
  note: string | null
  template_id: number | null
  template_name: string | null
  photo_url: string | null
  is_active: boolean
  on_till: boolean
  sizes: MenuSize[]
  lowest_margin: { pct: number | null; is_estimate: boolean; is_missing: boolean; no_price: boolean }
  season_id?: number | null
  season_name?: string | null
  /** Net units, every size, last 30 days (decimal string). */
  sold_30d?: string
}
export interface MenuCategory {
  name: string
  kind: MenuKind
  count: number
  registered: boolean
}
export interface MenuItemsResponse {
  as_of: string
  groups: MenuGroup[]
  categories: MenuCategory[]
}
export interface MenuLine {
  ingredient_id: number
  ingredient_name: string
  qty: string
  unit: Unit
  unit_cost: Cost
  line_cost: Cost
  category: string | null
}
export interface MenuItemDetail {
  size: MenuSize
  group: MenuGroup
  lines: MenuLine[]
  editable_lines: boolean
  estimate_names: string[]
  prices: { effective_from: string; effective_to: string | null; price_pence: number; source: string; set_by: string | null }[]
  history: HistoryEntry[]
}
export interface LineIn {
  ingredient_id: number
  qty: string
  unit: Unit | null
}
export interface LinesPreview {
  menu_item_ids: number[]
  diff: string[]
  impact: ChangeImpact
  at: string
}
export interface PricesPreview {
  diff: string[]
  pos_actions: string[]
  impact: ChangeImpact
  at: string
}
export interface MenuWrite {
  menu_item_ids: number[]
  summary: string
}
export interface PhotoResult {
  asset_id: number | null
  photo_url: string | null
  width: number | null
  height: number | null
  bytes: number | null
  content_type: string | null
  menu_item_ids: number[]
}

/* --------------------------------------------------------- ingredients --- */

export interface Pack {
  pack_size: string
  pack_unit: Unit
  pack_cost_pence: number
  source: PriceSource
  supplier_id: number | null
  supplier_name: string | null
  effective_from: string
  recorded_by: string | null
  note: string | null
}
export interface IngredientRow {
  ingredient_id: number
  name: string
  category: string | null
  unit: Unit
  unit_cost: Cost
  pack: Pack | null
  note: string | null
  suppliers: { supplier_id: number; name: string; is_preferred: boolean }[]
  used_in_count: number
  retired: boolean
  storage: 'AMBIENT' | 'CHILLED' | 'FROZEN'
  shelf_life_days: number | null
  shelf_life_source: string | null
  open_life_days?: number | null
  transit_buffer_days?: number
  tier?: string
  /** Decimal string, fraction lost in use (stock depletion only, never cost). */
  waste_factor?: string
  /** Reference photo from the media library, or null. */
  photo_url?: string | null
  /** UK 14 allergens: null = unknown, [] = checked and none. */
  allergens?: string[] | null
  /** A research URL, or "checked by <name> on <date>". */
  allergens_source?: string | null
  /** True only when a person recorded the list from the pack. */
  allergens_confirmed?: boolean
  /** Provenance of a photo the café did not take; null for own uploads. */
  photo_licence?: string | null
  photo_author?: string | null
  photo_source_url?: string | null
}
export interface IngredientPhotoResult {
  ingredient_id: number
  asset_id: number | null
  photo_url: string | null
}
export interface IngredientAllergensResult {
  ingredient_id: number
  allergens: string[] | null
  allergens_source: string | null
}
export interface IngredientsResponse {
  rows: IngredientRow[]
  categories: [string, number][]
  estimated_count: number
  total: number
  suppliers: { supplier_id: number; name: string }[]
}
export interface Offer {
  supplier_product_id: number
  supplier_id: number
  supplier_name: string
  sku: string | null
  pack_size: string
  pack_unit: Unit
  price_pence: number
  unit_cost_pence: string | null
  vs_cheapest_pct: number | null
  is_cheapest: boolean
  is_preferred: boolean
  last_seen_price_at: string | null
  terms_are_placeholders: boolean
}
export interface IngredientDetail {
  row: IngredientRow
  offers: Offer[]
  preferred_supplier: string | null
  used_in: { menu_item_id: number; name: string; via: 'one-off' | 'recipe'; template_name: string | null }[]
  price_history: {
    effective_from: string
    effective_to: string | null
    pack_size: string
    pack_unit: Unit
    pack_cost_pence: number
    cost_per_unit_pence: string
    source: PriceSource
    supplier_name: string | null
    recorded_by: string | null
  }[]
  unit_locked_by: Record<string, number>
  retire_blocked_by: Record<string, number>
}
export interface IngredientPriceBody {
  pack_size: string
  pack_unit: Unit
  pack_cost_pence: number
  source: PriceSource
  supplier_id: number | null
  note?: string | null
}
export interface IngredientPricePreview {
  ingredient_id: number
  unit: Unit
  unit_cost_before: Cost
  unit_cost_after: Cost
  diff: string[]
  impact: ChangeImpact
  at: string
}
export interface IngredientWrite {
  ingredient_id: number
  summary: string
}

/* ------------------------------------------------- menu item sales --- */

export type SaleChannel = 'EPOS' | 'DELIVEROO' | 'JUST_EAT' | 'OTHER'
export interface ItemSale {
  sale_id: number
  sold_at: string
  receipt_id: string
  menu_item_id: number
  size_code: SizeCode | null
  qty: string
  gross_pence: number
  channel: SaleChannel
  voided: boolean
  is_refund: boolean
  modifier_names: string[]
}
export interface ItemSales {
  menu_item_ids: number[]
  total_rows: number
  page: number
  page_size: number
  units: string
  gross_pence: number
  first_sold_at: string | null
  last_sold_at: string | null
  by_channel: Partial<Record<SaleChannel, number>>
  payment_note: string
  rows: ItemSale[]
}
export interface PrepWrite {
  menu_item_ids: number[]
  summary: string
  rollup_items_recosted: number
}
