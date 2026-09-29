/**
 * The shell area's data layer (shell-agents.md §3–§6):
 *
 * - `GET /api/shell`: the one cheap call the frame makes (sync line, badges,
 *   banners). Unknown renders as nothing, never as "0" or "all clear".
 * - `POST /api/sync` + `GET /api/sync/{id}`: "Sync now", single-flight on the
 *   server; `useSyncNow()` polls until the run finishes.
 * - `GET /api/settings`, `POST /api/auth/password`.
 * - `GET /api/setup`: the Setup checklist.
 * - `GET /api/agents/proposals`, accept/decline, `GET /api/agents/runs`.
 *
 * In fixture mode `request()` answers these from the recorded responses in
 * `web/fixtures/` (lib/fixtures); writes answer "offline".
 */
import { useCallback, useEffect, useRef, useState } from 'react'
import { useQuery, useQueryClient } from '@tanstack/react-query'
import { apiWrite, request, setCredential, type WriteResult } from './api'
import { navigate } from './router'
import type {
  AgentProposalsResponse,
  AgentRunsResponse,
  DecisionIn,
  DecisionResponse,
  PasswordChangeIn,
  PasswordChangeResponse,
  SettingsResponse,
  SetupResponse,
  SyncRun,
  SyncStarted,
} from './types/shell'

export type SyncAttemptStatus = 'RUNNING' | 'OK' | 'PARTIAL' | 'SKIPPED' | 'FAILED'

export interface ShellSync {
  lightspeed_configured: boolean
  last_ok_finished_at: string | null
  last_attempt: { status: SyncAttemptStatus; finished_at: string | null; detail: string | null } | null
  last_sale_at: string | null
  stale_after_hours: number
  is_stale: boolean
}

export interface ShellBanner {
  id: 'stale_sync' | 'lightspeed_not_connected' | 'cash_discrepancy' | string
  instance_key: string
  tone: 'stale' | 'alert'
  text: string
  action: { label: string; kind: 'sync_now' | 'navigate'; route?: string | null } | null
}

export interface ShellResponse {
  sync: ShellSync
  /** `shop_new` (online orders waiting) is optional: the Live orders screen counts itself when absent. */
  badges: { orders_waiting: number; proposals_waiting: number; shop_new?: number }
  banners: ShellBanner[]
  setup: { empty_install: boolean; open_steps: number }
}

export const SHELL_QUERY_KEY = ['shell'] as const
export const SETTINGS_QUERY_KEY = ['settings'] as const
export const SETUP_QUERY_KEY = ['setup'] as const
export const PROPOSALS_QUERY_KEY = ['agents', 'proposals'] as const
export const RUNS_QUERY_KEY = ['agents', 'runs'] as const

/** The shell's data, or `undefined` while unknown / unavailable. */
export function useShell(): ShellResponse | undefined {
  const q = useQuery({
    queryKey: SHELL_QUERY_KEY,
    queryFn: () => request<ShellResponse>('/api/shell'),
    staleTime: 60 * 1000,
    retry: false,
  })
  return q.data
}

/* ---------------------------------------------------------------- sync --- */

/** `POST /api/sync` (shell-agents.md §4.3). Returns the WriteResult. */
export function startSync(requestedBy?: string | null): Promise<WriteResult<SyncStarted>> {
  return apiWrite<SyncStarted>('/api/sync', { requested_by: requestedBy ?? null })
}

export type SyncState =
  | { kind: 'idle' }
  | { kind: 'starting' }
  | { kind: 'running'; runId: number }
  | { kind: 'done'; run: SyncRun }
  | { kind: 'refused'; message: string }

/**
 * "Sync now", end to end: start it, poll the run every 2s while RUNNING, then
 * refresh everything that reads sales or stock. Nothing is optimistic.
 */
export function useSyncNow(): {
  state: SyncState
  busy: boolean
  start: (requestedBy?: string | null) => Promise<void>
  reset: () => void
} {
  const qc = useQueryClient()
  const [state, setState] = useState<SyncState>({ kind: 'idle' })
  const alive = useRef(true)
  useEffect(() => {
    alive.current = true
    return () => {
      alive.current = false
    }
  }, [])

  const start = useCallback(
    async (requestedBy?: string | null) => {
      setState({ kind: 'starting' })
      const r = await startSync(requestedBy)
      if (r.kind !== 'ok') {
        if (alive.current) setState({ kind: 'refused', message: r.message })
        return
      }
      const runId = r.data.run_id
      if (alive.current) setState({ kind: 'running', runId })
      // 15 minutes at 2s: the server abandons a RUNNING row after 15 minutes.
      for (let i = 0; i < 450 && alive.current; i++) {
        await new Promise((res) => setTimeout(res, 2000))
        try {
          const run = await request<SyncRun>(`/api/sync/${runId}`)
          if (run.status !== 'RUNNING') {
            if (alive.current) setState({ kind: 'done', run })
            break
          }
        } catch {
          /* transient: keep polling */
        }
      }
      for (const key of [SHELL_QUERY_KEY, SETTINGS_QUERY_KEY, SETUP_QUERY_KEY, ['stock'], ['takings']]) {
        void qc.invalidateQueries({ queryKey: key })
      }
    },
    [qc],
  )
  const busy = state.kind === 'starting' || state.kind === 'running'
  return { state, busy, start, reset: () => setState({ kind: 'idle' }) }
}

/** One sentence for a finished or refused sync, for a status line. */
export function syncOutcomeText(state: SyncState): string | null {
  if (state.kind === 'refused') return state.message
  if (state.kind !== 'done') return null
  const run = state.run
  switch (run.status) {
    case 'OK':
      return `Synced. ${run.lines_ingested ?? 0} new sale line${run.lines_ingested === 1 ? '' : 's'}.`
    case 'PARTIAL':
      return `Synced part of the window: ${run.detail ?? 'the till stopped answering'}.`
    case 'SKIPPED':
      return `Nothing synced: ${run.detail ?? 'skipped'}.`
    case 'FAILED':
      return `The sync failed: ${run.detail ?? 'no reason recorded'}.`
    default:
      return null
  }
}

/* ------------------------------------------------------------ settings --- */

export function useSettings() {
  return useQuery({
    queryKey: SETTINGS_QUERY_KEY,
    queryFn: () => request<SettingsResponse>('/api/settings'),
    staleTime: 30 * 1000,
  })
}

/**
 * Change the shared password. On success this device keeps working: the
 * server revoked every session and issued this one a fresh token, stored here.
 */
export async function changePassword(body: PasswordChangeIn): Promise<WriteResult<PasswordChangeResponse>> {
  const r = await apiWrite<PasswordChangeResponse>('/api/auth/password', body)
  if (r.kind === 'ok') setCredential({ kind: 'session', value: r.data.token })
  return r
}

/* --------------------------------------------------------------- setup --- */

export function useSetup() {
  return useQuery({
    queryKey: SETUP_QUERY_KEY,
    queryFn: () => request<SetupResponse>('/api/setup'),
    staleTime: 30 * 1000,
  })
}

/* -------------------------------------------------------------- agents --- */

export function useProposals() {
  return useQuery({
    queryKey: PROPOSALS_QUERY_KEY,
    queryFn: () => request<AgentProposalsResponse>('/api/agents/proposals?limit=6'),
    staleTime: 30 * 1000,
  })
}

export function decideProposal(
  id: number,
  decision: 'accept' | 'decline',
  body: DecisionIn,
): Promise<WriteResult<DecisionResponse>> {
  return apiWrite<DecisionResponse>(`/api/agents/proposals/${id}/${decision}`, body)
}

export function fetchRuns(params: { agent?: string | null; before?: string | null; limit?: number }) {
  const q = new URLSearchParams()
  if (params.agent) q.set('agent', params.agent)
  if (params.before) q.set('before', params.before)
  q.set('limit', String(params.limit ?? 30))
  return request<AgentRunsResponse>(`/api/agents/runs?${q.toString()}`)
}

/**
 * Follow a server-authored route ("#/stock?filter=shelf_life") through the
 * router. Banners, Setup steps and proposals all carry routes as strings.
 */
export function goToRoute(route: string): void {
  const [path = '/', qs = ''] = route.replace(/^#/, '').split('?')
  const query: Record<string, string> = {}
  new URLSearchParams(qs).forEach((v, k) => {
    query[k] = v
  })
  navigate(path, { query })
}

/* ---- per-session, per-instance banner dismissal (shell-agents.md §3) ---- */

const DISMISSED = 'cafeops.banner.dismissed'

export function readDismissed(): Set<string> {
  try {
    const raw = sessionStorage.getItem(DISMISSED)
    return new Set(raw ? (JSON.parse(raw) as string[]) : [])
  } catch {
    return new Set()
  }
}

export function writeDismissed(s: Set<string>): void {
  try {
    sessionStorage.setItem(DISMISSED, JSON.stringify([...s]))
  } catch {
    /* private mode: dismissal lasts this page only */
  }
}
