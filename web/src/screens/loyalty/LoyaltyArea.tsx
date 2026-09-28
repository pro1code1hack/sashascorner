/**
 * Customers › Loyalty card (owner's design, 2026-09-28; docs/loyalty/BACKOFFICE-V2.md).
 *
 *   #/loyalty                          Members (the list)
 *   #/loyalty/members/<id>             one member's card
 *   #/loyalty/insights                 how the card is doing
 *   #/loyalty/messages                 lock-screen messages to members
 *   #/loyalty/programme                the deal, stickers, joining link, staff & devices
 *   #/loyalty/programme/catalogue      reward catalogue and other cards (phase 3)
 *
 * The retired Rewards pages (`#/rewards…`, `#/members…`) redirect here.
 */
import { useEffect } from 'react'
import { navigate, useLocation } from '../../lib/router'
import { Catalogue } from '../members/Catalogue'
import { InsightsScreen } from './InsightsScreen'
import { LoyaltyHeader } from './LoyaltyHeader'
import { MemberCardPage } from './MemberCardPage'
import { MembersScreen } from './MembersScreen'
import { MessagesScreen } from './MessagesScreen'
import { ProgrammeScreen } from './ProgrammeScreen'

export function LoyaltyArea() {
  const seg = useLocation().segments
  const sub = seg[1]
  if (sub === 'members' && seg[2] !== undefined && /^\d+$/.test(seg[2])) return <MemberCardPage memberId={Number(seg[2])} />
  if (sub === 'insights') return <InsightsScreen />
  if (sub === 'messages') return <MessagesScreen />
  if (sub === 'programme' && seg[2] === 'catalogue')
    return (
      <>
        <LoyaltyHeader current="programme" />
        <Catalogue />
      </>
    )
  if (sub === 'programme') return <ProgrammeScreen />
  return <MembersScreen />
}

/** Where each retired `#/rewards/...` or `#/members/...` path lives now. */
function loyaltyPathFor(segments: string[]): string {
  const [root, sub, id] = segments
  if (root === 'members') {
    if (sub === undefined) return '/loyalty'
    if (sub === 'programme') return '/loyalty/programme'
    if (sub === 'insights') return '/loyalty/insights'
    if (sub === 'campaigns') return '/loyalty/messages'
    if (sub === 'staff') return '/loyalty/programme'
    if (/^\d+$/.test(sub)) return `/loyalty/members/${sub}`
    return '/loyalty'
  }
  // root === 'rewards'
  if (sub === undefined) return '/loyalty/programme'
  if (sub === 'members') return id !== undefined && /^\d+$/.test(id) ? `/loyalty/members/${id}` : '/loyalty'
  if (sub === 'campaigns') return '/loyalty/messages'
  if (sub === 'insights' || sub === 'alerts') return '/loyalty/insights'
  if (sub === 'catalogue') return '/loyalty/programme/catalogue'
  return '/loyalty/programme'
}

export function LoyaltyRedirect() {
  const loc = useLocation()
  const target = loyaltyPathFor(loc.segments)
  useEffect(() => {
    navigate(target, { replace: true })
  }, [target])
  return null
}
