/**
 * Members (Sasha's Corner Rewards) data layer: docs/loyalty/CONTRACT.md §6.
 *
 * Reads go through `request()` inside `useQuery`, so fixture mode answers them
 * from `web/fixtures/members-*.json`. Writes use `apiWrite` and return a
 * `WriteResult`; nothing is optimistic, and a refusal (a wrong manager PIN, a
 * too-short reason) is shown as the server wrote it.
 */
import { useQuery, useQueryClient } from '@tanstack/react-query'
import { apiWrite, request, type WriteResult } from './api'
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
  LoyaltyProgram,
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
  ProgramIn,
  StaffCreateIn,
  StaffPatchIn,
  StaffResponse,
  StaffRole,
} from './types/members'

export const MEMBERS_KEY = ['members'] as const

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

export function useProgram() {
  return useQuery({
    queryKey: [...MEMBERS_KEY, 'program'],
    queryFn: () => request<LoyaltyProgram>('/api/members/program'),
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
  saveProgram: (body: ProgramIn): Promise<WriteResult<LoyaltyProgram>> =>
    apiWrite<LoyaltyProgram>('/api/members/program', body, 'PUT'),
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
}
