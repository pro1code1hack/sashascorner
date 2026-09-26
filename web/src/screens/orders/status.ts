/**
 * Order status words and tones (spec C14; FRONTEND-KIT StatusTag):
 * Waiting is alert; Draft, Received, Bought and Cancelled are ink-3;
 * Confirmed and Sent are ink.
 */
import type { StatusTone } from '../../components/ui'
import type { POStatus } from '../../lib/types/stock'

export function statusWord(status: POStatus | string): { label: string; tone: StatusTone } {
  switch (status) {
    case 'DRAFT':
      return { label: 'Draft (in Telegram /orders)', tone: 'ink-3' }
    case 'PENDING_CONFIRM':
      return { label: 'Waiting in Telegram', tone: 'alert' }
    case 'CONFIRMED':
      return { label: 'Confirmed', tone: 'ink' }
    case 'SENT':
      return { label: 'Sent', tone: 'ink' }
    case 'RECEIVED':
      return { label: 'Received', tone: 'ink-3' }
    case 'CANCELLED':
      return { label: 'Cancelled', tone: 'ink-3' }
    default:
      return { label: String(status), tone: 'ink-3' }
  }
}
