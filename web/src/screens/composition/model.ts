/**
 * The small facts the editor and its grid both need. No formatting and no
 * presentation: quantities are strings from the API, edited as strings, and sent
 * back as strings, and nothing here parses one into a float.
 */
import type { ComponentRole } from '../../lib/types'

export const ROLE_ORDER: ComponentRole[] = [
  'COFFEE',
  'MILK',
  'BASE',
  'FLAVOUR',
  'TOPPING',
  'PACKAGING',
  'SUNDRY',
]

/** What may be typed into a quantity cell. Not what is a valid quantity. */
export const QTY_INPUT = /^\d*\.?\d*$/

export function cellKey(componentId: number, size: string): string {
  return `${componentId}:${size}`
}

export interface DirtyCell {
  componentId: number
  size: string
  from: string
  to: string
  valid: boolean
}

/** `COFFEE` → `Coffee`. The role is a data value; shouting it is not. */
export function roleLabel(role: string): string {
  return role.charAt(0) + role.slice(1).toLowerCase()
}
