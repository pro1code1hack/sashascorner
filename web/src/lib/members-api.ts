/**
 * Rewards (Sasha's Corner Rewards) data layer: docs/loyalty/CONTRACT.md §6.
 *
 * What is left here is what the Loyalty card screens still read through it: the
 * query key root, staff and devices, the reward catalogue (programmes) and the
 * subject-access export. Members, insights and messages moved to loyalty-api.ts.
 *
 * Reads are LIVE ONLY. Unlike the rest of the app, fixture mode does not answer
 * them from recorded JSON: those recordings were demo members, and the owner
 * asked for no fake data in Rewards. In fixture mode every read fails with a
 * sentence saying so, and the screens show it in place of figures.
 * Writes use `apiWrite` and return a `WriteResult`; nothing is optimistic, and a
 * refusal (a wrong manager PIN, a too-short reason) is shown as the server wrote it.
 */
import { useQuery } from '@tanstack/react-query'
import { API_BASE, LIVE, apiWrite, authHeaders, request as rawRequest, type WriteResult } from './api'
import type {
  DevicePairing,
  DevicesResponse,
  ProgramEditIn,
  ProgramFull,
  ProgramsResponse,
  StaffCreateIn,
  StaffPatchIn,
  StaffResponse,
  StaffRole,
} from './types/members'

export const MEMBERS_KEY = ['members'] as const

/** Shown wherever a Rewards figure would be, when the app is not talking to the server. */
export const NOT_LIVE_MESSAGE =
  'Rewards only shows live data from the server, never sample members. Start the back office against the API (VITE_LIVE=1) to see it.'

function request<T>(path: string): Promise<T> {
  if (!LIVE) return Promise.reject(new Error(NOT_LIVE_MESSAGE))
  return rawRequest<T>(path)
}

/** Phase 3: every programme with its rules and catalogue. */
export function usePrograms() {
  return useQuery({
    queryKey: [...MEMBERS_KEY, 'programs'],
    queryFn: () => request<ProgramsResponse>('/api/members/programs'),
  })
}

/** Roles arrive in either case (the API writes roles lower-case); the screens use the enum's upper case. */
const upper = <T extends string>(v: string): T => v.toUpperCase() as T

export function useStaff() {
  return useQuery({
    queryKey: [...MEMBERS_KEY, 'staff'],
    queryFn: () => request<StaffResponse>('/api/members/staff'),
    select: (d): StaffResponse => ({ users: d.users.map((u) => ({ ...u, role: upper<StaffRole>(u.role) })) }),
  })
}

export function useDevices() {
  return useQuery({ queryKey: [...MEMBERS_KEY, 'devices'], queryFn: () => request<DevicesResponse>('/api/members/devices') })
}

export const membersApi = {
  addStaff: (body: StaffCreateIn): Promise<WriteResult<unknown>> => apiWrite<unknown>('/api/members/staff', body),
  patchStaff: (id: number, body: StaffPatchIn): Promise<WriteResult<unknown>> =>
    apiWrite<unknown>(`/api/members/staff/${id}`, body, 'PATCH'),
  pairDevice: (name: string): Promise<WriteResult<DevicePairing>> =>
    apiWrite<DevicePairing>('/api/members/devices', { name }),
  revokeDevice: (id: number): Promise<WriteResult<null>> =>
    apiWrite<null>(`/api/members/devices/${id}`, undefined, 'DELETE'),
  editProgram: (id: number, body: ProgramEditIn): Promise<WriteResult<ProgramFull>> =>
    apiWrite<ProgramFull>(`/api/members/programs/${id}`, body, 'PUT'),
}

/**
 * Download everything held about one member (a subject access request) as a
 * JSON file. The endpoint needs the auth header, so it is fetched and handed to
 * the browser as a blob rather than linked. Resolves to an error sentence, or null.
 */
export async function downloadMemberData(id: number, firstName: string): Promise<string | null> {
  if (!LIVE) return NOT_LIVE_MESSAGE
  try {
    const res = await fetch(`${API_BASE}/api/members/${id}/export`, { headers: authHeaders() })
    if (!res.ok) {
      let detail = `The server answered ${res.status}.`
      try {
        const j = (await res.json()) as { detail?: unknown }
        if (typeof j.detail === 'string') detail = j.detail
      } catch {
        /* not JSON */
      }
      return detail
    }
    const blob = await res.blob()
    const url = URL.createObjectURL(blob)
    const a = document.createElement('a')
    const safe = firstName.toLowerCase().replace(/[^a-z0-9]+/g, '-').replace(/^-|-$/g, '') || 'member'
    a.href = url
    a.download = `rewards-member-${id}-${safe}.json`
    document.body.appendChild(a)
    a.click()
    a.remove()
    window.setTimeout(() => URL.revokeObjectURL(url), 1000)
    return null
  } catch (e) {
    return e instanceof Error ? e.message : String(e)
  }
}
