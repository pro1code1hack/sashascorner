/**
 * Rewards (Sasha's Corner Rewards) data layer: docs/loyalty/CONTRACT.md §6.
 *
 * Reads are LIVE ONLY. Unlike the rest of the app, fixture mode does not answer
 * them from recorded JSON: those recordings were demo members, and the owner
 * asked for no fake data in Rewards. In fixture mode every read fails with a
 * sentence saying so, and the screens show it in place of figures.
 * Writes use `apiWrite` and return a `WriteResult`; nothing is optimistic, and a
 * refusal (a wrong manager PIN, a too-short reason) is shown as the server wrote it.
 */
import { useQuery, useQueryClient } from '@tanstack/react-query'
import { API_BASE, LIVE, apiWrite, authHeaders, request as rawRequest, type WriteResult } from './api'
import type {
  AdjustIn,
  AlertsResponse,
  Campaign,
  CampaignIn,
  CampaignSegment,
  CampaignSendResult,
  CampaignsResponse,
  DevicePairing,
  DevicesResponse,
  Eligibility,
  EligibilityPreview,
  LinkIn,
  MemberDetail,
  MenuFacets,
  PosCustomer,
  ProgramCreateIn,
  ProgramEditIn,
  ProgramFull,
  ProgramsResponse,
  MemberSegment,
  MemberSort,
  MembersResponse,
  MembersStats,
  StaffCreateIn,
  StaffPatchIn,
  StaffResponse,
  StaffRole,
  TargetsIn,
  TargetsResponse,
} from './types/members'

export const MEMBERS_KEY = ['members'] as const

/** Shown wherever a Rewards figure would be, when the app is not talking to the server. */
export const NOT_LIVE_MESSAGE =
  'Rewards only shows live data from the server, never sample members. Start the back office against the API (VITE_LIVE=1) to see it.'

function request<T>(path: string): Promise<T> {
  if (!LIVE) return Promise.reject(new Error(NOT_LIVE_MESSAGE))
  return rawRequest<T>(path)
}

export interface MembersQuery {
  q: string
  segment: MemberSegment
  sort: MemberSort
  limit: number
  offset: number
  /** Phase 3: a programme's slug; empty = the main card. */
  program?: string
}

function listPath(p: MembersQuery): string {
  const qs = new URLSearchParams()
  if (p.program) qs.set('program', p.program)
  if (p.q.trim()) qs.set('q', p.q.trim())
  if (p.segment !== 'all') qs.set('segment', p.segment)
  if (p.sort !== 'recent') qs.set('sort', p.sort)
  qs.set('limit', String(p.limit))
  if (p.offset > 0) qs.set('offset', String(p.offset))
  return `/api/members?${qs.toString()}`
}

export function useMembers(p: MembersQuery) {
  return useQuery({
    queryKey: [...MEMBERS_KEY, 'list', p],
    queryFn: () => request<MembersResponse>(listPath(p)),
    placeholderData: (prev) => prev,
    staleTime: 30 * 1000,
  })
}

export function useMember(id: number) {
  return useQuery({
    queryKey: [...MEMBERS_KEY, 'detail', id],
    queryFn: () => request<MemberDetail>(`/api/members/${id}`),
  })
}

export function useMembersStats(days = 90, program = '') {
  return useQuery({
    queryKey: [...MEMBERS_KEY, 'stats', days, program],
    queryFn: () => request<MembersStats>(`/api/members/stats?days=${days}${program ? `&program=${encodeURIComponent(program)}` : ''}`),
    staleTime: 5 * 60 * 1000,
  })
}

/** Phase 3: every programme with its rules and catalogue. */
export function usePrograms() {
  return useQuery({
    queryKey: [...MEMBERS_KEY, 'programs'],
    queryFn: () => request<ProgramsResponse>('/api/members/programs'),
  })
}

export function useMenuFacets() {
  return useQuery({
    queryKey: [...MEMBERS_KEY, 'menu-facets'],
    queryFn: () => request<MenuFacets>('/api/members/menu-facets'),
    staleTime: 10 * 60 * 1000,
  })
}

export function usePosCustomers(enabled: boolean) {
  return useQuery({
    queryKey: [...MEMBERS_KEY, 'pos-customers'],
    queryFn: () => request<{ customers: PosCustomer[] }>('/api/members/pos-customers'),
    enabled,
  })
}

/** Roles and segments arrive in either case (the API writes roles lower-case); the screens use the enum's upper case. */
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

export function useCampaigns() {
  return useQuery({
    queryKey: [...MEMBERS_KEY, 'campaigns'],
    queryFn: () => request<CampaignsResponse>('/api/members/campaigns'),
    select: (d): CampaignsResponse => ({ ...d, campaigns: d.campaigns.map((c) => ({ ...c, segment: upper<CampaignSegment>(c.segment) })) }),
  })
}

export function useTargets(program = '') {
  return useQuery({
    queryKey: [...MEMBERS_KEY, 'targets', program],
    queryFn: () => request<TargetsResponse>(`/api/members/targets${program ? `?program=${encodeURIComponent(program)}` : ''}`),
    staleTime: 5 * 60 * 1000,
  })
}

export function useAlerts() {
  return useQuery({
    queryKey: [...MEMBERS_KEY, 'alerts'],
    queryFn: () => request<AlertsResponse>('/api/members/alerts'),
    staleTime: 60 * 1000,
  })
}

/** Refetch everything under Members after a write. */
export function useInvalidateMembers(): () => Promise<void> {
  const qc = useQueryClient()
  return () => qc.invalidateQueries({ queryKey: MEMBERS_KEY })
}

export const membersApi = {
  adjust: (id: number, body: AdjustIn): Promise<WriteResult<MemberDetail>> =>
    apiWrite<MemberDetail>(`/api/members/${id}/adjust`, body),
  erase: (id: number): Promise<WriteResult<null>> => apiWrite<null>(`/api/members/${id}`, undefined, 'DELETE'),
  addStaff: (body: StaffCreateIn): Promise<WriteResult<unknown>> => apiWrite<unknown>('/api/members/staff', body),
  patchStaff: (id: number, body: StaffPatchIn): Promise<WriteResult<unknown>> =>
    apiWrite<unknown>(`/api/members/staff/${id}`, body, 'PATCH'),
  pairDevice: (name: string): Promise<WriteResult<DevicePairing>> =>
    apiWrite<DevicePairing>('/api/members/devices', { name }),
  revokeDevice: (id: number): Promise<WriteResult<null>> =>
    apiWrite<null>(`/api/members/devices/${id}`, undefined, 'DELETE'),
  createCampaign: (body: CampaignIn): Promise<WriteResult<Campaign>> =>
    apiWrite<Campaign>('/api/members/campaigns', body),
  sendCampaign: (id: number): Promise<WriteResult<CampaignSendResult>> =>
    apiWrite<CampaignSendResult>(`/api/members/campaigns/${id}/send`, {}),
  // phase 3
  createProgram: (body: ProgramCreateIn): Promise<WriteResult<ProgramFull>> =>
    apiWrite<ProgramFull>('/api/members/programs', body),
  editProgram: (id: number, body: ProgramEditIn): Promise<WriteResult<ProgramFull>> =>
    apiWrite<ProgramFull>(`/api/members/programs/${id}`, body, 'PUT'),
  previewEligibility: (body: Eligibility): Promise<WriteResult<EligibilityPreview>> =>
    apiWrite<EligibilityPreview>('/api/members/eligibility-preview', body),
  link: (id: number, body: LinkIn): Promise<WriteResult<MemberDetail>> =>
    apiWrite<MemberDetail>(`/api/members/${id}/lightspeed`, body, 'PUT'),
  unlink: (id: number): Promise<WriteResult<MemberDetail>> =>
    apiWrite<MemberDetail>(`/api/members/${id}/lightspeed`, undefined, 'DELETE'),
  saveTargets: (body: TargetsIn): Promise<WriteResult<TargetsResponse>> =>
    apiWrite<TargetsResponse>('/api/members/targets', body, 'PUT'),
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
