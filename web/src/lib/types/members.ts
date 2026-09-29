/**
 * Members (Sasha's Corner Rewards): the admin API, docs/loyalty/CONTRACT.md §6.
 *
 * Served under `/api/members/*` on the ops domain behind the shared password.
 * Money is integer pence; timestamps are ISO-8601 UTC; a share is a FRACTION
 * 0–1 (not a percentage); a figure the server cannot work out is `null`, and
 * the screens say why rather than printing 0.
 *
 * What is left here is what Loyalty › Programme still reads through
 * lib/members-api.ts: the reward catalogue (programmes) and staff & devices.
 * Members, insights and messages are typed in types/loyalty.ts.
 */

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
  /** More than this many purchase stamps on one card within `cooldown_minutes` needs a manager's PIN. */
  cooldown_max_stamps: number
  cooldown_minutes: number
}

export interface ProgramsResponse {
  programs: ProgramFull[]
  auto_stamp: boolean
  /** False while no active manager or owner exists: rules then save without a PIN. */
  pin_required: boolean
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
  cooldown_max_stamps?: number
  cooldown_minutes?: number
  /** Required once a manager or owner exists (`ProgramsResponse.pin_required`). */
  manager_pin?: string
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
