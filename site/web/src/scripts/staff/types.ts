// Shapes of the staff API, docs/loyalty/CONTRACT.md §5. Kept in step with it by hand.

export type Role = 'staff' | 'manager' | 'owner' | 'STAFF' | 'MANAGER' | 'OWNER';

export interface StaffUser {
  id: number;
  name: string;
  role: Role;
}

export interface PairResult {
  device_token: string;
}

export interface LoginResult {
  session_token: string;
  expires_at: string;
  user: StaffUser;
}

export interface MeResult {
  user: StaffUser;
  device: { id: number; name: string };
  expires_at: string;
}

export interface Reward {
  id: number;
  kind: 'STAMP_CARD' | 'BIRTHDAY' | 'REFERRAL' | string;
  label: string;
  expires_at: string | null;
}

export interface LastEvent {
  id: number;
  delta: number;
  reason: string;
  at: string;
  staff_name: string | null;
}

/** A reward-catalogue entry (phase 3): what a ready reward can be taken as. */
export interface RewardOption {
  id: number;
  name: string;
  description: string | null;
  max_price_pence: number | null;
  covers: string;
}

export interface ProgramRef {
  slug: string;
  name: string;
  kind: 'STAMPS' | 'POINTS' | string;
}

export interface ScanResult {
  card_id: string;
  first_name: string;
  member_since: string;
  stamps_current: number;
  stamps_required: number;
  rewards: Reward[];
  stamps_last_10_min: number;
  last_event: LastEvent | null;
  voided: boolean;
  // Phase 3 (optional so an older server still type-checks at runtime).
  program_slug?: string;
  program_name?: string;
  program_kind?: 'STAMPS' | 'POINTS' | string;
  points_per_pound?: number | null;
  /** "Free matcha ready": also names the reward before it is ready ("free matcha"). */
  reward_ready_label?: string;
  max_stamps_per_scan?: number;
  reward_options?: RewardOption[];
  /** The member's other live cards: any one scan shows all of them. */
  other_cards?: ScanResult[];
  joinable?: ProgramRef[];
  lightspeed_linked?: boolean;
}

export interface DrinksResult {
  items: Drink[];
  max_price_pence?: number | null;
  covers?: string | null;
}

export interface PosCustomer {
  customer_id: string;
  label: string | null;
  last_receipt_id: string;
  last_at: string;
  receipts: number;
}

export interface StampResult {
  card: ScanResult;
  event_id: number;
  undo_until: string;
  reward_issued: boolean;
}

export interface RedeemResult {
  card: ScanResult;
  reward_id: number;
  undo_until: string;
}

export interface UndoResult {
  card: ScanResult;
}

export interface Drink {
  menu_item_id: number;
  name: string;
  category: string | null;
  price_pence: number | null;
}

export interface LookupMember {
  card_id: string;
  first_name: string;
  contact_masked: string;
}
