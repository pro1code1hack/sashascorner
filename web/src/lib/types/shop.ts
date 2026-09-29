/**
 * Online ordering (click & collect) admin shapes: docs/shop/CONTRACT.md §2 and
 * §5, mirrored literally. `/api/shop-admin/*` is authed like the rest of the
 * back office. Money is integer pence; a basket line's `qty` is a whole number
 * (contract §1), so it is the one quantity here that is an int, not a string.
 */

export type DiningOption = 'TAKEAWAY' | 'EAT_IN'
export type OrderStatus =
  | 'PENDING_PAYMENT'
  | 'NEW'
  | 'ACCEPTED'
  | 'PREPARING'
  | 'READY'
  | 'COLLECTED'
  | 'CANCELLED'
  | 'REJECTED'
export type PaymentMethod = 'COUNTER' | 'ONLINE'
export type PaymentStatus = 'UNPAID' | 'PAID' | 'REFUNDED' | 'FAILED'
export type OptionKind = 'SINGLE' | 'MULTI'
export type OptionLayout = 'TILES' | 'PHOTO_TILES' | 'CHECKLIST'
export type UpsellPlacement = 'ITEM_PAGE' | 'BASKET'
export type SizeCode = 'S' | 'M' | 'XL' | 'ONE'
export type SmsNotify = 'off' | 'ready' | 'all'
/** `domain/shop.allergens_state`: nobody said / confirmed none / a list. */
export type AllergensState = 'unknown' | 'none' | 'listed'
/** The one allergen token that is a claim rather than an allergen: "somebody checked, there are none". Exclusive. */
export const ALLERGENS_NONE = 'none'

/** Contract §2.4 vocabularies (`domain/shop.py: ALLERGENS`). */
export const ALLERGENS = ['milk', 'gluten', 'nuts', 'soya', 'egg', 'sesame', 'sulphites'] as const
export const DIETARY = ['vegan', 'vegetarian', 'gluten-free', 'decaf-available', 'contains-caffeine'] as const
export const NUTRITION_FIELDS = [
  ['energy_kj', 'Energy (kJ)'],
  ['fat_g', 'Fat (g)'],
  ['saturates_g', 'of which saturates (g)'],
  ['carbs_g', 'Carbohydrate (g)'],
  ['sugars_g', 'of which sugars (g)'],
  ['protein_g', 'Protein (g)'],
  ['salt_g', 'Salt (g)'],
] as const

/* ------------------------------------------------------------- summary --- */

/** A pluggable payment provider or till (POS) sink, as the server lists it. */
export interface ProviderRef {
  key: string
  display_name: string
  configured: boolean
}

export interface ShopSummary {
  enabled: boolean
  open_now: boolean
  counts: {
    new: number
    accepted: number
    preparing: number
    ready: number
    today_collected: number
    today_cancelled: number
  }
  today_revenue_pence: number
  next_due: { code: string; requested_local: string; customer_name: string; status: OrderStatus }[]
  stripe_configured: boolean
  telegram_configured: boolean
  /** Customer update channels the server can use (owner, 2026-09-29). */
  smtp_configured?: boolean
  sms_configured?: boolean
  push_configured?: boolean
  /** Payment providers the server knows (owner's extension, 2026-09-29). */
  payment_providers?: ProviderRef[]
  /** Tills an accepted order can be pushed to. */
  pos_sinks?: ProviderRef[]
}

/* -------------------------------------------------------------- orders --- */

export interface OrderLineOption {
  group: string
  name: string
  price_delta_pence: number
  modifier_id?: number | null
}

export interface OrderLine {
  id: number
  product_id: number | null
  menu_item_id: number
  name: string
  size_label: string
  qty: number
  unit_price_pence: number
  options: OrderLineOption[]
  line_total_pence: number
  sort_order: number
}

export interface OrderEvent {
  id?: number
  at: string
  kind: string
  detail: string | null
  actor: string
}

/** `OrderAdmin`: every order column plus lines, events, member and the local strings. */
export interface OrderAdmin {
  id: number
  code: string
  /** "SC-XXXXXX", as the customer sees it. */
  code_display: string
  status: OrderStatus
  dining: DiningOption
  asap: boolean
  requested_at: string
  placed_at: string
  accepted_at: string | null
  ready_at: string | null
  collected_at: string | null
  cancelled_at: string | null
  cancel_reason: string | null
  cancelled_by: string | null
  customer_name: string
  customer_phone: string | null
  customer_email: string | null
  member_id: number | null
  card_id: string | null
  note: string | null
  /** Eat in: the table the customer is sitting at ("4", "window"); null for takeaway. */
  table?: string | null
  allergy_ack: boolean
  subtotal_pence: number
  discount_pence: number
  total_pence: number
  reward_id: number | null
  payment_method: PaymentMethod
  payment_status: PaymentStatus
  payment_ref: string | null
  /** The provider's payment id (Stripe payment_intent), kept for refunds. */
  payment_intent?: string | null
  /** PAID online: `POST …/refund` would try the provider. */
  refundable?: boolean
  paid_at: string | null
  sale_receipt_id: string | null
  stamp_event_id: number | null
  staff_note: string | null
  /** The till's reference once the order was pushed to a POS sink. */
  pos_ref?: string | null
  /** Customer updates (owner, 2026-09-29): texts opted in per order; a push subscription. */
  sms_opt_in?: boolean
  push_subscribed?: boolean
  push_subscription_id?: number | null
  created_at?: string
  updated_at: string
  lines: OrderLine[]
  events: OrderEvent[]
  member: { id: number; first_name: string; stamps_current: number } | null
  requested_local: string
  placed_local: string
  /** Negative when late. */
  minutes_until_due: number
  /** 0 for today, 1 for tomorrow…; computed on the client when the server leaves it out. */
  days_ahead?: number
  /** What `POST …/status` would accept from here (contract §3.8). */
  allowed_transitions: OrderStatus[]
  client_ip_hash?: string | null
  user_agent?: string | null
}

export type OrdersScope = 'live' | 'today' | 'all'

export interface OrdersPage {
  items: OrderAdmin[]
  total: number
  page: number
  page_size: number
}

export interface OrderStatusIn {
  status: OrderStatus
  reason?: string
  by: string
}
export interface OrderNoteIn {
  staff_note: string
  by: string
}
export interface OrderPaidIn {
  by: string
}
export interface OrderRefundIn {
  by: string
  reason?: string
}

/* ------------------------------------------------------------ settings --- */

export interface HoursRow {
  /** Mon=0 … Sun=6. */
  weekday: number
  open: string
  close: string
}

export interface Closure {
  date: string
  note?: string | null
}

/** Every `shop_settings` column (contract §2.1). */
export interface ShopSettings {
  enabled: boolean
  closed_message: string
  hero_title: string
  hero_subtitle: string
  takeaway_enabled: boolean
  eat_in_enabled: boolean
  default_dining: DiningOption
  lead_minutes: number
  slot_minutes: number
  max_orders_per_slot: number
  days_ahead: number
  hours: HoursRow[]
  last_order_minutes_before_close: number
  closures: Closure[]
  pay_at_counter: boolean
  pay_online: boolean
  kcal_notice: string
  allergen_notice: string
  collection_note: string
  terms_url: string
  loyalty_stamps_online: boolean
  notify_telegram: boolean
  /** Off (default): customers sign in with their Rewards card to place an order (§10.J). */
  guest_orders: boolean
  stripe_configured?: boolean
  telegram_configured?: boolean
  /** Customer updates (owner, 2026-09-29). */
  email_notify?: boolean
  push_notify?: boolean
  sms_notify?: SmsNotify
  /** Which payment provider takes online payments (`payment_providers[].key`). */
  payment_provider: string
  /** Which till an order is pushed to (`pos_sinks[].key`; "" or "none" = no push). */
  pos_sink: string
  updated_at: string | null
}

export type ShopSettingsPatch = Partial<Omit<ShopSettings, 'updated_at'>>

/* ----------------------------------------------------------- catalogue --- */

export interface CategoryAdmin {
  id: number
  ops_name: string
  name: string
  slug: string
  blurb: string | null
  photo_url: string | null
  sort_order: number
  visible: boolean
  updated_at?: string | null
  product_count: number
}

export interface ProductSize {
  menu_item_id: number
  code: SizeCode
  label: string
  price_pence: number
  active: boolean
  kcal: number | null
}

export interface ProductAdmin {
  id: number
  item_name: string
  display_name: string | null
  /** `display_name` or `item_name`: what the shop shows. */
  name: string
  slug: string
  category_ops_name: string | null
  category_slug: string | null
  description: string | null
  kcal: number | null
  allergens: string[]
  /** 'unknown' = empty list, nobody said; 'none' = confirmed none; 'listed'. */
  allergens_state?: AllergensState
  dietary: string[]
  default_size: SizeCode | null
  ingredients_text: string | null
  note: string | null
  kcal_by_size: Record<string, number> | null
  nutrition: Record<string, string> | null
  photo_url: string | null
  /** False when the photo shown is the ops menu item's, not the shop's own. */
  photo_is_own: boolean
  /** True when no photo exists and the shop shows a placeholder. */
  photo_is_fallback?: boolean
  badge: string | null
  sort_order: number
  visible: boolean
  available: boolean
  featured: boolean
  /** False when no active `menu_item` row carries this name any more. */
  ops_active: boolean
  from_price_pence: number | null
  sizes: ProductSize[]
  /** Explicit attachments only. */
  option_group_ids: number[]
  /** Explicit attachments plus groups applying to the category. */
  effective_option_group_ids: number[]
  updated_at?: string | null
}

export interface ProductPatch {
  display_name?: string | null
  description?: string | null
  note?: string | null
  kcal?: number | null
  kcal_by_size?: Record<string, number> | null
  nutrition?: Record<string, string> | null
  allergens?: string[]
  dietary?: string[]
  ingredients_text?: string | null
  badge?: string | null
  visible?: boolean
  available?: boolean
  featured?: boolean
  category_ops_name?: string | null
  sort_order?: number
  default_size?: string | null
  option_group_ids?: number[]
}

export interface CategoryPatch {
  name?: string
  blurb?: string | null
  visible?: boolean
  sort_order?: number
}

export interface OptionAdmin {
  id: number
  group_id?: number
  name: string
  description: string | null
  price_delta_pence: number
  kcal: number | null
  is_default: boolean
  available: boolean
  sort_order: number
  modifier_id: number | null
  modifier_name: string | null
  photo_url: string | null
}

export interface GroupAdmin {
  id: number
  name: string
  prompt: string | null
  kind: OptionKind
  layout: OptionLayout
  required: boolean
  min_select: number
  max_select: number | null
  collapsed: boolean
  applies_to_categories: string[]
  sort_order: number
  active: boolean
  options: OptionAdmin[]
  /** Products explicitly attached. */
  product_ids: number[]
  updated_at?: string | null
}

export interface OptionIn {
  id?: number
  name: string
  description?: string | null
  price_delta_pence: number
  kcal?: number | null
  is_default: boolean
  available: boolean
  modifier_id?: number | null
  sort_order: number
}

export interface GroupIn {
  name: string
  prompt?: string | null
  kind: OptionKind
  layout: OptionLayout
  required: boolean
  min_select: number
  max_select?: number | null
  collapsed: boolean
  applies_to_categories: string[]
  active?: boolean
  options: OptionIn[]
}

export interface UpsellAdmin {
  id: number
  placement: UpsellPlacement
  heading: string
  product_ids: number[]
  sort_order: number
  active: boolean
}

export interface UpsellIn {
  placement: UpsellPlacement
  heading: string
  product_ids: number[]
  sort_order?: number
  active: boolean
}

export interface BannerAdmin {
  id: number
  title: string
  subtitle: string | null
  photo_url: string | null
  link_href: string | null
  active: boolean
  sort_order: number
  starts_on: string | null
  ends_on: string | null
}

export interface BannerIn {
  title: string
  subtitle?: string | null
  link_href?: string | null
  active: boolean
  sort_order?: number
  starts_on?: string | null
  ends_on?: string | null
}

export interface CatalogueAdmin {
  categories: CategoryAdmin[]
  products: ProductAdmin[]
  option_groups: GroupAdmin[]
  upsells: UpsellAdmin[]
  banners: BannerAdmin[]
  ops: { items_without_category: number; menu_items_active: number }
  /** The vocabularies (`domain/shop.py`). */
  allergens: string[]
  dietary: string[]
}

/** `GET /api/shop-admin/insights?days=` (contract §10.B.1). Money counts COLLECTED orders only. */
export interface ShopInsights {
  period: { from: string; to: string; days: number }
  previous: { from: string; to: string }
  figures: {
    orders: number
    revenue_pence: number
    avg_basket_pence: number | null
    items: number
    members_share: number | null
    online_paid_share: number | null
    cancelled: number
    rejected: number
    avg_minutes_to_ready: number | null
    vs_previous: { orders_pct: number | null; revenue_pct: number | null; avg_basket_pct: number | null }
  }
  per_day: { date: string; orders: number; revenue_pence: number; cancelled: number }[]
  by_hour: { hour: number; orders: number }[]
  by_weekday: { weekday: number; orders: number; revenue_pence: number }[]
  top_products: { product_id: number | null; name: string; qty: number; revenue_pence: number }[]
  top_options: { group: string; name: string; qty: number }[]
  dining: { takeaway: number; eat_in: number }
  payment: { counter: number; online: number }
  status_now: { new: number; accepted: number; preparing: number; ready: number }
  caveats: string[]
}

/** `GET/PUT /api/shop-admin/products/by-menu-item/{id}` (contract §10.B.2). */
export interface ProductByMenuItem extends ProductAdmin {
  menu_item_id: number
  effective_option_groups: { id: number; name: string; kind: OptionKind }[]
  shop_url_path: string
}

/** `GET /api/shop-admin/modifiers`, for linking an option to stock depletion. */
export interface ModifierRef {
  id: number
  name: string
  price_pence: number
}
