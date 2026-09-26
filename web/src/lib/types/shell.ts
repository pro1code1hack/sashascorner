/**
 * Types for the shell area's endpoints: settings, setup, sync, password change,
 * agent proposals and the run log. Mirrors `cafeops/api/areas/shell_schemas.py`.
 * The frame's own `GET /api/shell` types live in `lib/shell-api.ts`.
 *
 * Timestamps are tz-aware UTC ISO strings. Money is integer pence.
 */
import type { SyncAttemptStatus } from '../shell-api'

export interface SyncAttempt {
  status: SyncAttemptStatus
  finished_at: string | null
  detail: string | null
}

/* ------------------------------------------------------------ settings --- */

export interface SettingsResponse {
  auth: {
    /** Which password is in force: the one set here, or the server's env bootstrap. */
    source: 'database' | 'environment' | null
    set_at: string | null
    set_by: string | null
    active_sessions: number
    min_length: number
  }
  lightspeed: {
    configured: boolean
    env_vars: string[]
    last_ok_finished_at: string | null
    last_sale_at: string | null
    last_attempt: SyncAttempt | null
  }
  telegram: { bot_configured: boolean; owner_chat_configured: boolean; language: 'ru' }
  agent: { narration: 'model' | 'template'; model: string | null }
  labour: { loaded_hourly_rate_pence: number | null }
}

export interface PasswordChangeIn {
  current_password: string
  new_password: string
  actor?: string | null
}

export interface PasswordChangeResponse {
  token: string
  expires_at: string
  revoked_sessions: number
  message: string
  telegram: { sent: boolean; detail: string }
}

/* ---------------------------------------------------------------- sync --- */

export interface SyncStarted {
  run_id: number
  message: string
}

export interface SyncRun {
  id: number
  trigger: 'SCHEDULED' | 'MANUAL_WEB' | 'CLI'
  source: 'LIVE' | 'FIXTURES'
  status: SyncAttemptStatus
  started_at: string
  finished_at: string | null
  window_since: string
  window_until: string
  receipts_seen: number | null
  lines_ingested: number | null
  unresolved_count: number | null
  detail: string | null
  requested_by: string | null
}

/* --------------------------------------------------------------- setup --- */

export type SetupStepKey = 'import' | 'shelf_life' | 'supplier_terms' | 'lightspeed' | 'first_count'

export interface SetupStep {
  n: number
  key: SetupStepKey
  done: boolean
  /** null = not countable, which is not the same as 0. */
  remaining: number | null
  title: string
  body: string
  cta: { label: string; route: string } | null
  cli_fix: string | null
}

export interface SetupWarning {
  severity: 'FAIL' | 'WARN' | 'INFO'
  name: string
  detail: string
  fix: string
}

export interface SetupResponse {
  empty_install: boolean
  open_steps: number
  steps: SetupStep[]
  warnings: SetupWarning[]
}

/* -------------------------------------------------------------- agents --- */

export type ProposalKind = 'waste_factor' | 'template_grouping' | 'channel_import' | 'data_fix' | 'supplier_basket'
export type ProposalStatus = 'WAITING' | 'ACCEPTED' | 'DECLINED' | 'SUPERSEDED' | 'APPLY_FAILED'

export interface AgentProposal {
  id: number
  created_at: string
  agent: string
  agent_label: string
  kind: ProposalKind
  subject_ref: string
  title: string
  body: string
  confidence: 'high' | 'medium' | 'low' | 'none' | null
  accept_label: string
  decline_label: string
  /** apply = a service runs; navigate = accept opens an editor; unavailable = no service. */
  accept_mode: 'apply' | 'navigate' | 'unavailable'
  navigate_to: string | null
  unavailable_reason: string | null
  note: string | null
  status: ProposalStatus
  decided_at: string | null
  decided_by: string | null
  decision_note: string | null
  applied_result: Record<string, unknown> | null
  run_id: string
}

export interface WaitingOrder {
  po_id: number
  supplier: string
  total_pence: number
  target_delivery_date: string
  created_at: string
  note: string
}

export interface AgentProposalsResponse {
  waiting: AgentProposal[]
  decided: AgentProposal[]
  waiting_count: number
  orders_waiting: WaitingOrder[]
}

export interface DecisionIn {
  decided_by: string
  note?: string | null
}

export interface DecisionResponse {
  proposal: AgentProposal
  outcome: 'applied' | 'recorded' | 'superseded' | 'failed' | 'declined'
  applied: Record<string, string> | null
  message: string
}

export interface AgentRunTool {
  tool_name: string
  label: string
  kind: string
  outcome: 'OK' | 'REFUSED' | 'FAILED' | 'AWAITING_HUMAN'
  inputs: Record<string, unknown>
  output: string | null
  refusal_reason: string | null
}

export interface AgentRunRow {
  key: string
  run_id: string | null
  agent: string
  agent_label: string
  tool_label: string
  started_at: string
  finished_at: string
  produced: string
  result: string
  result_tone: 'plain' | 'alert'
  read_summary: string
  wrote_summary: string
  model: string | null
  /** A person's accept/decline, rendered into the log (not stored in agent_action_log). */
  is_decision: boolean
  tools: AgentRunTool[]
}

export interface AgentRunsResponse {
  runs: AgentRunRow[]
  agents: Array<{ agent: string; label: string }>
  has_more: boolean
}
