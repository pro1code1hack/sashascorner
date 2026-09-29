/**
 * Loyalty card data layer: docs/loyalty/BACKOFFICE-V2.md.
 *
 * Live only (see members-api.ts for why: no sample members in Loyalty). Writes return
 * a `WriteResult`; nothing is optimistic, refusals are shown as the server wrote them.
 * A write that creates, erases or reconfigures invalidates the whole `['members']`
 * key; one that answers with the member refreshes only the list and the insights
 * (`useInvalidateLoyaltyLists`).
 */
import { useSyncExternalStore } from 'react'
import { useQuery, useQueryClient } from '@tanstack/react-query'
import { LIVE, apiWrite, request as rawRequest, type WriteResult } from './api'
import { MEMBERS_KEY, NOT_LIVE_MESSAGE, downloadMemberData } from './members-api'
import type {
  InsightsDays,
  Insights,
  LoyaltyCampaign,
  LoyaltyCampaignIn,
  LoyaltyCampaignsResponse,
  LoyaltyCampaignSendResult,
  LoyaltyMemberDetail,
  LoyaltyMembersResponse,
  LoyaltySegment,
  LoyaltySort,
  MemberCreateIn,
  MemberPatchIn,
  ProgramSettings,
  ProgramSettingsIn,
  SendLinkOut,
  StickerKey,
} from './types/loyalty'

export { MEMBERS_KEY, NOT_LIVE_MESSAGE, downloadMemberData }

const LIST_KEY = [...MEMBERS_KEY, 'v2-list'] as const
const INSIGHTS_KEY = [...MEMBERS_KEY, 'insights'] as const

function request<T>(path: string): Promise<T> {
  if (!LIVE) return Promise.reject(new Error(NOT_LIVE_MESSAGE))
  return rawRequest<T>(path)
}

export interface LoyaltyListQuery {
  q: string
  segment: LoyaltySegment
  sort: LoyaltySort
  limit: number
  offset: number
}

export function useLoyaltyMembers(p: LoyaltyListQuery) {
  const qs = new URLSearchParams()
  if (p.q.trim()) qs.set('q', p.q.trim())
  if (p.segment !== 'all') qs.set('segment', p.segment)
  qs.set('sort', p.sort)
  qs.set('limit', String(p.limit))
  if (p.offset > 0) qs.set('offset', String(p.offset))
  return useQuery({
    queryKey: [...LIST_KEY, p],
    queryFn: () => request<LoyaltyMembersResponse>(`/api/members?${qs.toString()}`),
    placeholderData: (prev) => prev,
    staleTime: 30 * 1000,
  })
}

/**
 * The tab badge: members with a free drink waiting. Every list response carries the
 * segment counts, so the badge reads the freshest list already in the cache and only
 * asks the server itself (limit=1) when no list has been fetched this session.
 */
export function useRewardReadyCount(): number | null {
  const qc = useQueryClient()
  const cache = qc.getQueryCache()
  const fromList = useSyncExternalStore(
    (cb) => cache.subscribe(cb),
    () => {
      let best: { at: number; n: number } | null = null
      for (const q of cache.findAll({ queryKey: LIST_KEY })) {
        const d = q.state.data as LoyaltyMembersResponse | undefined
        if (d && (best === null || q.state.dataUpdatedAt > best.at)) best = { at: q.state.dataUpdatedAt, n: d.counts.reward_ready }
      }
      return best === null ? null : best.n
    },
  )
  const own = useQuery({
    queryKey: [...LIST_KEY, 'badge'],
    queryFn: () => request<LoyaltyMembersResponse>('/api/members?sort=recent&limit=1'),
    enabled: fromList === null,
    staleTime: 60 * 1000,
  })
  return fromList ?? own.data?.counts.reward_ready ?? null
}

export function useLoyaltyMember(id: number) {
  return useQuery({
    queryKey: [...MEMBERS_KEY, 'v2-detail', id],
    queryFn: () => request<LoyaltyMemberDetail>(`/api/members/${id}`),
  })
}

export function useInsights(days: InsightsDays) {
  return useQuery({
    queryKey: [...INSIGHTS_KEY, days],
    queryFn: () => request<Insights>(`/api/members/insights?days=${days}`),
    placeholderData: (prev) => prev,
    staleTime: 60 * 1000,
  })
}

export function useLoyaltyCampaigns() {
  return useQuery({
    queryKey: [...MEMBERS_KEY, 'v2-campaigns'],
    queryFn: () => request<LoyaltyCampaignsResponse>('/api/members/campaigns'),
  })
}

export function useProgramSettings() {
  return useQuery({
    queryKey: [...MEMBERS_KEY, 'v2-program'],
    queryFn: () => request<ProgramSettings>('/api/members/program'),
  })
}

/** Refetch everything under Loyalty after a write. */
export function useInvalidateLoyalty(): () => Promise<void> {
  const qc = useQueryClient()
  return () => qc.invalidateQueries({ queryKey: MEMBERS_KEY })
}

/**
 * After a write that answered with the member (a stamp, a free drink, an undo): the
 * caller has already put the answer in the detail cache, so only the list (with its
 * badge counts) and the insights need a refetch, not the programme or the messages.
 */
export function useInvalidateLoyaltyLists(): () => Promise<void> {
  const qc = useQueryClient()
  return () => Promise.all([qc.invalidateQueries({ queryKey: LIST_KEY }), qc.invalidateQueries({ queryKey: INSIGHTS_KEY })]).then(() => undefined)
}

export const loyaltyApi = {
  createMember: (body: MemberCreateIn): Promise<WriteResult<LoyaltyMemberDetail>> =>
    apiWrite<LoyaltyMemberDetail>('/api/members', body),
  patchMember: (id: number, body: MemberPatchIn): Promise<WriteResult<LoyaltyMemberDetail>> =>
    apiWrite<LoyaltyMemberDetail>(`/api/members/${id}`, body, 'PATCH'),
  addStamp: (id: number, sticker?: StickerKey): Promise<WriteResult<LoyaltyMemberDetail>> =>
    apiWrite<LoyaltyMemberDetail>(`/api/members/${id}/stamp`, sticker ? { sticker } : {}),
  giveReward: (id: number, rewardId?: number): Promise<WriteResult<LoyaltyMemberDetail>> =>
    apiWrite<LoyaltyMemberDetail>(`/api/members/${id}/give-reward`, rewardId ? { reward_id: rewardId } : {}),
  undoLast: (id: number): Promise<WriteResult<LoyaltyMemberDetail>> =>
    apiWrite<LoyaltyMemberDetail>(`/api/members/${id}/undo-last`, {}),
  setSticker: (id: number, slot: number, sticker: StickerKey): Promise<WriteResult<LoyaltyMemberDetail>> =>
    apiWrite<LoyaltyMemberDetail>(`/api/members/${id}/stickers`, { slot, sticker }, 'PUT'),
  sendLink: (id: number): Promise<WriteResult<SendLinkOut>> =>
    apiWrite<SendLinkOut>(`/api/members/${id}/send-link`, {}),
  erase: (id: number): Promise<WriteResult<null>> => apiWrite<null>(`/api/members/${id}`, undefined, 'DELETE'),

  createCampaign: (body: LoyaltyCampaignIn): Promise<WriteResult<LoyaltyCampaign>> =>
    apiWrite<LoyaltyCampaign>('/api/members/campaigns', body),
  sendCampaign: (id: number): Promise<WriteResult<LoyaltyCampaignSendResult>> =>
    apiWrite<LoyaltyCampaignSendResult>(`/api/members/campaigns/${id}/send`, {}),
  cancelCampaign: (id: number): Promise<WriteResult<LoyaltyCampaign>> =>
    apiWrite<LoyaltyCampaign>(`/api/members/campaigns/${id}/cancel`, {}),

  saveProgram: (body: ProgramSettingsIn): Promise<WriteResult<ProgramSettings>> =>
    apiWrite<ProgramSettings>('/api/members/program', body, 'PUT'),
}
