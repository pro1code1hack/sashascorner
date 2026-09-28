/**
 * Rewards › Members (Sasha's Corner Rewards), docs/loyalty/CONTRACT.md §6.
 *
 *   #/rewards/members          the list: search, segments, sort, alerts strip
 *   #/rewards/members/<id>     one member: card, ledger, rewards, consent, adjust,
 *                              data export, erase
 *
 * The other Rewards pages are their own routes (routes.tsx, "Rewards" group):
 * Programme (#/rewards), Reward catalogue, Campaigns, Insights, Staff & devices, Alerts.
 */
import { useEffect } from 'react'
import { navigate, useLocation } from '../../lib/router'
import { MemberPage } from './MemberPage'
import { MembersList } from './MembersList'

export function MembersArea() {
  const sub = useLocation().segments[2]
  if (sub !== undefined && /^\d+$/.test(sub)) return <MemberPage memberId={Number(sub)} />
  return <MembersList />
}

/** Where each retired `#/members/...` path lives now. */
function rewardsPathFor(segments: string[]): string {
  const sub = segments[1]
  if (sub === undefined) return '/rewards/members'
  if (sub === 'programme') return '/rewards'
  if (sub === 'insights' || sub === 'campaigns' || sub === 'staff') return `/rewards/${sub}`
  if (/^\d+$/.test(sub)) return `/rewards/members/${sub}`
  return '/rewards/members'
}

/** `#/members/...` (the old Members area) -> the same page under `#/rewards/...`. */
export function MembersRedirect() {
  const loc = useLocation()
  const target = rewardsPathFor(loc.segments)
  const qs = loc.query.toString()
  useEffect(() => {
    navigate(target, { replace: true, query: Object.fromEntries(new URLSearchParams(qs).entries()) })
  }, [target, qs])
  return null
}
