/**
 * Supplier vocabularies. C13: the backend's order channels, labelled; the
 * design's Phone / Message are display distinctions the adapters do not act on.
 */
import type { OrderChannel, Unit } from '../../lib/types/stock'

export const CHANNELS: ReadonlyArray<{ value: OrderChannel; label: string }> = [
  { value: 'PORTAL', label: 'Portal' },
  { value: 'EMAIL', label: 'Email' },
  { value: 'MANUAL', label: 'Phone / in store / message' },
  { value: 'EDI', label: 'EDI' },
  { value: 'BROWSER_AGENT', label: 'Portal (browser agent)' },
]

export const KINDS: readonly string[] = [
  'Foodservice',
  'Wholesale',
  'Packaging',
  'Cakes & bakery',
  'Supermarket',
  'Online',
  'Specialist',
  'Custom',
]

/** The design's unit picker (L, ml, kg, g, unit) over the backend's units. */
export const UNITS: ReadonlyArray<{ value: Unit; label: string }> = [
  { value: 'L', label: 'L' },
  { value: 'ML', label: 'ml' },
  { value: 'KG', label: 'kg' },
  { value: 'G', label: 'g' },
  { value: 'EACH', label: 'unit' },
]

export function channelLabel(c: OrderChannel | string): string {
  return CHANNELS.find((x) => x.value === c)?.label ?? String(c)
}

/** A light check; the server has the final word. Undefined when fine or blank. */
export function emailError(raw: string): string | undefined {
  const v = raw.trim()
  if (v === '') return undefined
  return /^[^\s@]+@[^\s@]+\.[^\s@]+$/.test(v) ? undefined : 'That does not look like an email address.'
}
