/**
 * Loyalty card back office v2: docs/loyalty/BACKOFFICE-V2.md.
 *
 * The source of truth for field names between `cafeops/api/areas/members*.py` and the
 * four Loyalty tabs. Money is integer pence, timestamps ISO-8601 UTC, shares are
 * FRACTIONS 0..1, and a figure the server cannot work out is `null` (never 0).
 */
export type { StaffUser, StaffRole, StaffResponse, StaffCreateIn, StaffPatchIn, StaffDevice, DevicesResponse, DevicePairing } from './members'

/* -------------------------------------------------------------- stickers --- */

export type StickerKey = 'cat' | 'seal' | 'matcha' | 'boba' | 'cake' | 'knight' | 'latte' | 'star'

export interface Sticker {
  key: StickerKey
  name: string
  /** Path under the web app's public dir, e.g. "/stickers/slot-1.svg". */
  url: string
}

/* ------------------------------------------------------------ members list --- */

export type LoyaltySegment = 'all' | 'reward_ready' | 'lapsed_30' | 'new_30' | 'opted_in' | 'no_wallet'
export type LoyaltySort = 'recent' | 'joined' | 'stamps' | 'name'
export type WalletKind = 'apple' | 'google' | 'web'

export interface LoyaltyMemberRow {
  member_id: number
  card_id: string
  first_name: string
  email: string | null
  phone: string | null
  /** The `?src=` tag they joined from ("window-sticker", "counter-qr", "back-office"…); null = website, untagged. */
  source: string | null
  created_at: string
  last_activity_at: string
  /** Last purchase stamp or free drink; null = never came in since joining. */
  last_visit_at: string | null
  stamps_current: number
  stamps_required: number
  /** Cards filled. */
  cycles_completed: number
  /** Free drinks given. */
  rewards_redeemed: number
  reward_available: boolean
  marketing_opt_in: boolean
  /** null = no wallet yet (never opened the card). */
  wallet: WalletKind | null
}

export type SegmentCounts = Record<LoyaltySegment, number>

export interface LoyaltyMembersResponse {
  total: number
  members: LoyaltyMemberRow[]
  /** Size of every segment, ignoring the search text. */
  counts: SegmentCounts
}

/* ------------------------------------------------------------- one member --- */

export interface LoyaltyMember extends LoyaltyMemberRow {
  /** "DD-MM", never a year. */
  birthday: string | null
  opt_in_at: string | null
  opt_in_source: string | null
  terms_accepted_at: string | null
  notes: string | null
  /** Visits a month over the last 90 days, one decimal; null before any visit. */
  visits_per_month: number | null
}

export interface CardFace {
  card_id: string
  /** Short form shown as "card 6acde3" (first 6 hex of the id). */
  short_id: string
  stamps_current: number
  stamps_required: number
  /** One key per filled slot, length === stamps_current. */
  stickers: StickerKey[]
  reward_available: boolean
  /** The reward "Give the free drink" will redeem; null when none is ready. */
  reward_id: number | null
  reward_text: string
  voided: boolean
  /** Whether "Undo last" has anything to undo, and what ("the stamp from 2 min ago"). */
  can_undo: boolean
  undo_label: string | null
}

export type HistoryKind =
  | 'stamp'
  | 'welcome'
  | 'paper'
  | 'correction'
  | 'undo'
  | 'referral'
  | 'expired'
  | 'free_drink'
  | 'birthday'
  | 'joined'

export interface HistoryEntry {
  kind: HistoryKind
  /** "Stamp given", "Free drink given", "Stamp undone", "Joined"… */
  title: string
  /** "Counter iPad · Cat", "Card filled and reset · Counter iPad", "Back office · Cake". */
  detail: string | null
  /** Sticker to draw as the row's icon, when there is one. */
  sticker: StickerKey | null
  at: string
}

export interface LoyaltyMemberDetail {
  member: LoyaltyMember
  card: CardFace
  history: HistoryEntry[]
}

export interface MemberCreateIn {
  first_name: string
  email?: string | null
  phone?: string | null
  /** "DD-MM" */
  birthday?: string | null
  marketing_opt_in: boolean
  /** Defaults to "back-office". */
  source?: string | null
}

export interface MemberPatchIn {
  first_name?: string
  email?: string | null
  phone?: string | null
  birthday?: string | null
  marketing_opt_in?: boolean
  notes?: string | null
}

export interface SendLinkOut {
  delivery: 'email' | 'sms' | 'none'
  /** Masked destination ("r•••@gmail.com"), null when delivery is none. */
  to: string | null
  /** The web-card link itself, so staff can copy it when nothing could be sent. */
  url: string
  message: string
}

/* ---------------------------------------------------------------- insights --- */

export type InsightsDays = 30 | 90 | 180

export interface Kpi {
  value: number | null
  /** Same figure over the previous window; null when not comparable. */
  previous: number | null
}

export interface WeekBucket {
  /** Monday of the week, local date "YYYY-MM-DD". */
  week_start: string
  new_members: number
  stamps: number
  free_drinks: number
}

export interface Insights {
  days: InsightsDays
  /** Members with a live card now vs at the start of the window. */
  members: Kpi
  joined_in_window: number
  /** Distinct members with a purchase stamp in the window. */
  active_members: Kpi
  visits_per_active_member_per_month: number | null
  stamps: Kpi
  stamps_per_week: number
  free_drinks: Kpi
  /** Share of members who joined before the window and stamped in it. 0..1. */
  came_back: Kpi
  opted_in_share: number | null
  opted_in_count: number
  weekly: WeekBucket[]
  by_source: { source: string | null; label: string; members: number; share: number }[]
  /** Local hour (7..20) and purchase stamps in the window. */
  hours: { hour: number; stamps: number }[]
  regulars: { member_id: number; first_name: string; stamps: number }[]
  alerts: { at: string; kind: string; detail: string }[]
}

/* ---------------------------------------------------------------- messages --- */

export type LoyaltyCampaignSegment = 'ALL_OPTED_IN' | 'LAPSED_30' | 'REWARD_READY' | 'NEW_30' | 'BIRTHDAY'
export type CampaignStatus = 'draft' | 'scheduled' | 'sent' | 'cancelled'

export interface LoyaltyCampaign {
  id: number
  title: string
  message: string
  segment: LoyaltyCampaignSegment
  is_promo: boolean
  status: CampaignStatus
  scheduled_at: string | null
  sent_at: string | null
  cancelled_at: string | null
  /** Sent: how many got it. */
  recipients: number | null
  /** Unsent: how many it would reach now. */
  audience: number | null
  returned: number | null
  /** returned / recipients once 7 days have passed or anyone returned; null otherwise. */
  return_rate: number | null
  created_by: string
  created_at: string
}

export interface LoyaltyCampaignsResponse {
  campaigns: LoyaltyCampaign[]
  promo_limit_per_month: number
  /** Promotional campaigns sent or scheduled this calendar month. */
  promos_this_month: number
  /** Who each segment reaches now (promos: opted-in only). */
  audiences: Record<LoyaltyCampaignSegment, number>
  /** Everyone with a live card: who a notice (is_promo=false) to ALL_OPTED_IN reaches. */
  everyone_with_card: number
}

export interface LoyaltyCampaignIn {
  title?: string | null
  message: string
  segment: LoyaltyCampaignSegment
  is_promo: boolean
  /** ISO with offset; null/absent = draft unless send_now. */
  scheduled_at?: string | null
  send_now?: boolean
}

export interface LoyaltyCampaignSendResult {
  recipients: number
  skipped_over_limit: number
}

/* --------------------------------------------------------------- programme --- */

export interface ProgramSettings {
  id: number
  name: string
  stamps_required: number
  reward_text: string
  birthday_reward: boolean
  welcome_stamp: boolean
  stamps_expire: boolean
  /** Enabled stickers, in scanner order. */
  stickers: StickerKey[]
  sticker_catalogue: Sticker[]
  /** Public join page, e.g. "https://sashascorner.co.uk/rewards". Tag with ?src=. */
  join_url: string
  /** Known tags and how many members joined from each. */
  join_sources: { source: string; members: number }[]
  /** Members who hold a card on this programme. */
  cards: number
  /** A manager PIN is required to save while a manager/owner staff user exists. */
  pin_required: boolean
}

export interface ProgramSettingsIn {
  stamps_required?: number
  reward_text?: string
  birthday_reward?: boolean
  welcome_stamp?: boolean
  stamps_expire?: boolean
  stickers?: StickerKey[]
  manager_pin?: string
}
