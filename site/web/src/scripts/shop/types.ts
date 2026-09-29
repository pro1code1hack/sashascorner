// Shapes of the public ordering API, docs/shop/CONTRACT.md §4. Money is integer pence;
// times are ISO-8601 with offset, `*_local` fields are display strings (Europe/London).

export type Dining = 'takeaway' | 'eat_in';
export type AllergensState = 'unknown' | 'none' | 'listed';
export type SizeCode = 'S' | 'M' | 'XL' | 'ONE';

export interface ShopConfig {
  enabled: boolean;
  closed_message: string;
  hero_title: string;
  hero_subtitle: string;
  dining: { takeaway: boolean; eat_in: boolean; default: Dining };
  pay: { counter: boolean; online: boolean };
  kcal_notice: string;
  allergen_notice: string;
  collection_note: string;
  terms_url: string;
  open_now: boolean;
  next_open_local: string | null;
  cafe: { name: string; address_line: string; postcode: string; phone: string };
  loyalty: { program_name: string; stamps_required: number; reward_text: string };
}

export interface Banner {
  id: number;
  title: string;
  subtitle: string | null;
  photo_url: string | null;
  link_href: string | null;
}

export interface Category {
  id: number;
  slug: string;
  name: string;
  blurb: string | null;
  photo_url: string | null;
  /** True when `photo_url` is borrowed from one of the category's products (Agent F). */
  photo_is_fallback?: boolean;
  product_count: number;
}

export interface Size {
  menu_item_id: number;
  code: SizeCode;
  label: string;
  price_pence: number;
  kcal: number | null;
}

export interface Option {
  id: number;
  name: string;
  description: string | null;
  price_delta_pence: number;
  kcal: number | null;
  is_default: boolean;
  available: boolean;
  photo_url: string | null;
}

export interface OptionGroup {
  id: number;
  name: string;
  prompt: string | null;
  kind: 'single' | 'multi';
  layout: 'tiles' | 'photo_tiles' | 'checklist';
  required: boolean;
  min_select: number;
  max_select: number | null;
  collapsed: boolean;
  options: Option[];
}

export interface Upsell {
  heading: string;
  product_ids: number[];
}

export interface Product {
  id: number;
  slug: string;
  name: string;
  category_slug: string | null;
  description: string | null;
  note: string | null;
  kcal: number | null;
  kcal_by_size: Partial<Record<SizeCode, number>> | null;
  nutrition: Record<string, string | null> | null;
  allergens: string[];
  /** Whether the allergen list has been filled in (Agent F): `unknown` until someone checks. */
  allergens_state?: AllergensState;
  dietary: string[];
  ingredients_text: string | null;
  photo_url: string | null;
  badge: string | null;
  available: boolean;
  featured: boolean;
  from_price_pence: number;
  default_size: SizeCode;
  sizes: Size[];
  option_groups: OptionGroup[];
  upsells: Upsell[];
}

export interface Catalogue {
  generated_at: string;
  version: string;
  banners: Banner[];
  categories: Category[];
  products: Product[];
  basket_upsells: Upsell[];
}

export interface SlotsResponse {
  date: string;
  open: boolean;
  reason: string | null;
  asap: { available: boolean; at: string; local: string };
  slots: { at: string; local: string; available: boolean }[];
  days: string[];
}

export interface QuoteLineIn {
  product_id: number;
  menu_item_id: number;
  qty: number;
  option_ids: number[];
}
export interface QuoteBody {
  dining: Dining;
  lines: QuoteLineIn[];
  reward: boolean;
}
export interface LineOptionOut {
  group: string;
  name: string;
  price_delta_pence: number;
}
export interface QuoteLineOut extends QuoteLineIn {
  name: string;
  size_label: string;
  unit_price_pence: number;
  line_total_pence: number;
  options: LineOptionOut[];
  problems: string[];
}
export interface Quote {
  lines: QuoteLineOut[];
  subtotal_pence: number;
  discount_pence: number;
  total_pence: number;
  reward: { applied: boolean; line_index: number | null; text: string | null };
  problems: string[];
}

export interface PlaceOrderBody extends QuoteBody {
  asap: boolean;
  requested_at: string | null;
  customer: { name: string; phone?: string; email?: string };
  note?: string;
  /** Eat in: the table the customer is sitting at (optional, Agent F). */
  table?: string;
  allergy_ack: true;
  payment: 'counter' | 'online';
  expected_total_pence: number;
  website: string;
}
export interface PlacedOrder {
  code: string;
  status: OrderStatus;
  access_token: string;
  total_pence: number;
  requested_at: string;
  requested_local: string;
  payment: { method: 'counter' | 'online'; status: PaymentStatus; checkout_url: string | null };
}

export type OrderStatus =
  | 'PENDING_PAYMENT'
  | 'NEW'
  | 'ACCEPTED'
  | 'PREPARING'
  | 'READY'
  | 'COLLECTED'
  | 'CANCELLED'
  | 'REJECTED';
export type PaymentStatus = 'UNPAID' | 'PAID' | 'REFUNDED' | 'FAILED';

export interface OrderView {
  code: string;
  status: OrderStatus;
  status_label: string;
  status_step: number;
  dining: Dining;
  /** Eat in: the table number, when given (Agent F). */
  table?: string | null;
  asap: boolean;
  requested_at: string;
  requested_local: string;
  placed_at: string;
  ready_at: string | null;
  collected_at: string | null;
  customer: { name: string };
  lines: {
    name: string;
    size_label: string;
    qty: number;
    unit_price_pence: number;
    line_total_pence: number;
    options: LineOptionOut[];
  }[];
  subtotal_pence: number;
  discount_pence: number;
  total_pence: number;
  payment: { method: 'counter' | 'online'; status: PaymentStatus };
  collection_note: string;
  cancel_allowed: boolean;
  events: { at: string; kind: string; detail: string | null }[];
}

export interface Me {
  first_name: string;
  email: string | null;
  phone: string | null;
  card_id: string;
  stamps_current: number;
  stamps_required: number;
  reward: { id: number; text: string } | null;
  recent_orders: RecentOrder[];
}

export interface RecentOrder {
  code: string;
  status: OrderStatus;
  placed_at: string;
  total_pence: number;
  /** The lines as basket input, for "Order again" (Agent F). */
  lines?: QuoteLineIn[];
  /** False when a line of this order can no longer be ordered as it was (Agent F). */
  reorder_complete?: boolean;
}
