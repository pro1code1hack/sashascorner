/**
 * Members (Sasha's Corner Rewards), docs/loyalty/CONTRACT.md row E.
 *
 *   #/members              the list: search, segments, sort, alerts strip
 *   #/members/<id>         one member: card, ledger, rewards, consent, adjust, erase
 *   #/members/insights     the 90-day success metrics
 *   #/members/campaigns    compose and send lock-screen messages
 *   #/members/staff        staff users, PINs, scanner devices, alerts
 *   #/members/programme    the stamp rules (manager PIN to change)
 */
import { useLocation } from '../../lib/router'
import { Campaigns } from './Campaigns'
import { Insights } from './Insights'
import { MemberPage } from './MemberPage'
import { MembersList } from './MembersList'
import { Programme } from './Programme'
import { Staff } from './Staff'

export function MembersArea() {
  const sub = useLocation().segments[1]
  if (sub === 'insights') return <Insights />
  if (sub === 'campaigns') return <Campaigns />
  if (sub === 'staff') return <Staff />
  if (sub === 'programme') return <Programme />
  if (sub !== undefined && /^\d+$/.test(sub)) return <MemberPage memberId={Number(sub)} />
  return <MembersList />
}
