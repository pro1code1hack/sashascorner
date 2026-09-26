/**
 * Media-query hooks for the two layout breakpoints (design-system.md §4):
 * `compact` 900px (sidebar becomes a drawer, drawers become sheets below it)
 * and `wide` 1280px. Prefer the Tailwind variants `compact:` / `wide:` /
 * `max-compact:` in markup; use these hooks only when behaviour (not style)
 * differs, e.g. focus trapping in a sheet.
 */
import { useSyncExternalStore } from 'react'

export const COMPACT_QUERY = '(min-width: 900px)'
export const WIDE_QUERY = '(min-width: 1280px)'
export const REDUCED_MOTION_QUERY = '(prefers-reduced-motion: reduce)'

export function useMediaQuery(query: string): boolean {
  return useSyncExternalStore(
    (cb) => {
      const m = window.matchMedia(query)
      m.addEventListener('change', cb)
      return () => m.removeEventListener('change', cb)
    },
    () => window.matchMedia(query).matches,
    () => false,
  )
}

/** True at >= 900px: the sidebar is docked and drawers sit inline. */
export function useIsDocked(): boolean {
  return useMediaQuery(COMPACT_QUERY)
}
