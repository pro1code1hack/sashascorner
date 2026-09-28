/** Words shared by the Messages list and composer. */
import type { LoyaltyCampaignSegment } from '../../../lib/types/loyalty'

/** Who a segment is, as the end of "to …". A notice to ALL reaches everyone with a card. */
export function segmentPhrase(segment: LoyaltyCampaignSegment, isPromo: boolean): string {
  switch (segment) {
    case 'ALL_OPTED_IN':
      return isPromo ? 'everyone opted in' : 'everyone with a card'
    case 'LAPSED_30':
      return 'lapsed 30+ days'
    case 'REWARD_READY':
      return 'reward ready, not used'
    case 'NEW_30':
      return 'new, last 30 days'
    case 'BIRTHDAY':
      return 'birthday this month'
  }
}

const DAY = new Intl.DateTimeFormat('en-GB', {
  weekday: 'short',
  day: 'numeric',
  month: 'short',
  timeZone: 'Europe/London',
})
const TIME = new Intl.DateTimeFormat('en-GB', {
  hour: '2-digit',
  minute: '2-digit',
  hour12: false,
  timeZone: 'Europe/London',
})

/** "Wed 30 Sept". */
export function weekdayDate(iso: string | Date): string {
  return DAY.format(typeof iso === 'string' ? new Date(iso) : iso).replace(',', '')
}

/** "Wed 30 Sept, 10:00". */
export function weekdayDateTime(iso: string | Date): string {
  const d = typeof iso === 'string' ? new Date(iso) : iso
  return `${weekdayDate(d)}, ${TIME.format(d)}`
}

export function people(n: number): string {
  return `${n} ${n === 1 ? 'person' : 'people'}`
}
