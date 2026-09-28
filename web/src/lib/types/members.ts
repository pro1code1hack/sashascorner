/**
 * Members (Sasha's Corner Rewards): the admin API, docs/loyalty/CONTRACT.md §6.
 *
 * Served under `/api/members/*` on the ops domain behind the shared password.
 * Money is integer pence; timestamps are ISO-8601 UTC; a share is a FRACTION
 * 0–1 (not a percentage); a figure the server cannot work out is `null`, and
 * the screens say why rather than printing 0.
 */

export type MemberSegment = 'all' | 'lapsed_30' | 'reward_ready' | 'opted_in' | 'new_30'
export type MemberSort = 'recent' | 'stamps' | 'name'
export type MemberWallet = 'apple' | 'google' | 'web'

export interface MemberRow {
  member_id: number
  card_id: string
  first_name: string
  email: string | null
  phone: string | null
  /** The `?src=` the member joined from ("table", "till", "flyer-uni"…). */
  source: string | null
  created_at: string
  last_activity_at: string
  stamps_current: number
  stamps_required: number
  cycles_completed: number
  rewards_redeemed: number
  reward_available: boolean
  marketing_opt_in: boolean
  /** Where the card lives. `null`: no wallet pass or web card opened yet. */
  wallet: MemberWallet | null
}

export interface MembersResponse {
  total: number
  members: MemberRow[]
}

/**
 * Who referred this member. The contract names the field without a shape; the
 * screen accepts the member id alone or `{member_id, first_name}`.
 */
export type ReferredBy = number | { member_id: number; first_name: string | null } | null

export interface MemberDetailRow extends MemberRow {
  /** "DD-MM", never a year. */
  birthday: string | null
  opt_in_at: string | null
  /** Where consent was given ("join form", "web card"…). */
  opt_in_source: string | null
  referred_by: ReferredBy
}

export type StampReason = 'PURCHASE' | 'PAPER_MIGRATION' | 'MANUAL_FIX' | 'REDEEM' | 'REFERRAL' | 'UNDO'
export type RewardKind = 'STAMP_CARD' | 'BIRTHDAY' | 'REFERRAL'

export interface StampEvent {
  /** Phase 3: which card (programme) the event is on. */
  program_slug?: string
  id: number
  /** Non-zero; negative for an undo or a manual correction down. */
  delta: number
  reason: StampReason
  note: string | null
  staff_name: string | null
  device_name: string | null
  created_at: string
}

export interface MemberReward {
  id: number
  kind: RewardKind
  issued_at: string
  expires_at: string | null
  redeemed_at: string | null
  /** The drink it was spent on, when staff picked one. */
  redeemed_item: string | null
  staff_name: string | null
  voided_at: string | null
  program_slug?: string
  /** Phase 3: the catalogue entry it was taken as ("Slice of cake"). */
  redeemed_option?: string | null
}

/** Phase 3: one of the member's cards (one per programme). */
export interface MemberCard {
  card_id: string
  program_slug: string
  program_name: string
  program_kind: ProgramKind
  stamps_current: number
  stamps_required: number
  cycles_completed: number
  reward_available: boolean
  voided: boolean
  created_at: string
}

/** Phase 3: the Lightspeed till customer this member is. */
export interface LightspeedLink {
  customer_id: string
  linked_at: string | null
  source: string | null
  receipts: { receipt_id: string; closed_at: string; total_pence: number; awards: string[] }[]
}

export interface MemberDetail {
  member: MemberDetailRow
  events: StampEvent[]
  rewards: MemberReward[]
  cards?: MemberCard[]
  lightspeed?: LightspeedLink | null
  /** CAFEOPS_LOYALTY_AUTO_STAMP: whether receipts turn into stamps at all. */
  auto_stamp?: boolean
}

/** `POST /api/members/{id}/adjust` -> MemberDetail. */
export interface AdjustIn {
  delta: number
  /** At least 5 characters: it is the audit. */
  reason: string
  manager_pin: string
  /** Phase 3: which card; default the main one. */
  program?: string
}

/** Phase 3: `PUT /api/members/{id}/lightspeed`. One of the two. */
export interface LinkIn {
  customer_id?: string
  receipt_id?: string
}

export interface PosCustomer {
  customer_id: string
  label: string | null
  last_receipt_id: string
  last_at: string
  receipts: number
}

/* ---------------------------------------------------------------- stats --- */

export interface MembersStats {
  members_total: number
  members_new_in_window: number
  opted_in_share: number
  by_source: { source: string | null; members: number }[]
  daily: { date: string; new_members: number; stamps: number; redemptions: number }[]
  /** Stamps / EPOS receipts in the same window. `null` with no receipts synced. */
  stamp_share_of_transactions: number | null
  redemptions_per_week: number
  /** `null` until there is a member with a month of history. */
  visits_per_member_per_month: number | null
  members_with_redemption_share: number
  /** Share of campaign recipients stamped within 7 days. `null`: nothing sent. */
  campaign_return_rate: number | null
  // Phase 3.
  program_slug?: string
  /** Till customers seen in the window who came back on another day. */
  repeat_rate_members?: number | null
  repeat_rate_non_members?: number | null
  repeat_customers_members?: number
  repeat_customers_non_members?: number
  /** Members only, from scans: exists without the till. */
  repeat_rate_members_by_scans?: number | null
  /** Receipts naming a customer / all EPOS receipts. */
  receipts_with_customer_share?: number | null
  /** Why a repeat rate is null, in a sentence. */
  repeat_rate_reason?: string | null
}

/* ------------------------------------------------------------- programme --- */

export interface LoyaltyProgram {
  id: number
  slug: string
  name: string
  stamps_required: number
  max_stamps_per_scan: number
  reward_text: string
  /** `null` = any drink qualifies, however dear. */
  reward_max_price_pence: number | null
  birthday_reward: boolean
  /** 0 turns referrals off. */
  referral_stamps: number
  active: boolean
}

/* ---------------------------------------------- phase 3: programmes --- */

export type ProgramKind = 'STAMPS' | 'POINTS'

export interface Eligibility {
  scope: 'drinks' | 'all'
  categories: string[]
  keywords: string[]
  template_ids: number[]
}

export interface RewardOption {
  id: number
  name: string
  description: string | null
  eligibility: Eligibility
  covers: string
  max_price_pence: number | null
  active: boolean
}

export interface ProgramFull extends LoyaltyProgram {
  kind: ProgramKind
  points_per_pound: number | null
  eligibility: Eligibility
  earns: string
  description: string | null
  reward_ready_label: string
  sort_order: number
  is_default: boolean
  reward_options: RewardOption[]
  cards: number
  kind_editable: boolean
}

export interface ProgramsResponse {
  programs: ProgramFull[]
  auto_stamp: boolean
}

export interface RewardOptionIn {
  id?: number | null
  name: string
  description?: string | null
  eligibility?: Eligibility | null
  max_price_pence?: number | null
  active?: boolean
}

export interface ProgramEditIn {
  name?: string
  kind?: ProgramKind
  stamps_required?: number
  points_per_pound?: number
  max_stamps_per_scan?: number
  reward_text?: string
  reward_max_price_pence?: number | null
  reward_ready_label?: string
  description?: string | null
  birthday_reward?: boolean
  referral_stamps?: number
  active?: boolean
  sort_order?: number
  eligibility?: Eligibility | null
  reward_options?: RewardOptionIn[]
  manager_pin: string
}

export interface ProgramCreateIn extends ProgramEditIn {
  slug: string
}

export interface MenuFacets {
  categories: string[]
  templates: { id: number; name: string }[]
}

export interface EligibilityPreview {
  count: number
  sample: string[]
  covers: string
}

export interface ProgramIn {
  name?: string
  stamps_required?: number
  max_stamps_per_scan?: number
  reward_text?: string
  reward_max_price_pence?: number | null
  birthday_reward?: boolean
  referral_stamps?: number
  manager_pin: string
}

/* ----------------------------------------------------------------- staff --- */

export type StaffRole = 'STAFF' | 'MANAGER' | 'OWNER'

export interface StaffUser {
  id: number
  name: string
  role: StaffRole
  active: boolean
  telegram_id: number | null
}

export interface StaffResponse {
  users: StaffUser[]
}

export interface StaffCreateIn {
  name: string
  role: StaffRole
  pin: string
  telegram_id?: number | null
}

export interface StaffPatchIn {
  name?: string
  role?: StaffRole
  pin?: string
  active?: boolean
  telegram_id?: number | null
}

export interface StaffDevice {
  id: number
  name: string
  registered_at: string | null
  last_seen_at: string | null
  revoked_at: string | null
}

export interface DevicesResponse {
  devices: StaffDevice[]
}

export interface DevicePairing {
  device_id: number
  /** Six digits, typed into the scanner once. */
  pairing_code: string
  /** 15 minutes after it was issued. */
  expires_at: string
}

/* ------------------------------------------------------------- campaigns --- */

export type CampaignSegment = 'ALL_OPTED_IN' | 'LAPSED_30' | 'REWARD_READY' | 'NEW_30'

export interface Campaign {
  id: number
  title: string
  /** 200 characters at most: it is a lock-screen line. */
  message: string
  segment: CampaignSegment
  is_promo: boolean
  scheduled_at: string | null
  sent_at: string | null
  recipients: number | null
  created_by: string
  created_at: string
  /** Optional (CONTRACT addition): recipients stamped within 7 days. */
  returned?: number | null
  /** Optional (CONTRACT addition): opted-in members the segment reaches now, unsent campaigns. */
  audience?: number | null
}

export interface CampaignsResponse {
  campaigns: Campaign[]
  promo_limit_per_month: number
}

export interface CampaignIn {
  title: string
  message: string
  segment: CampaignSegment
  scheduled_at?: string | null
  is_promo: boolean
}

export interface CampaignSendResult {
  recipients: number
  /** Members left out because they already had this month's promos. */
  skipped_over_limit: number
}

/* ---------------------------------------------------------------- alerts --- */

export interface MemberAlert {
  at: string
  kind: string
  detail: string
}

export interface AlertsResponse {
  alerts: MemberAlert[]
}
