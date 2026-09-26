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

/**
 * One MediaQueryList and one subscribe function per query, so a re-render
 * (a FilterBar on every search keystroke) neither re-queries nor resubscribes.
 */
const stores = new Map<string, { subscribe: (cb: () => void) => () => void; get: () => boolean }>()

function store(query: string) {
  let s = stores.get(query)
  if (s === undefined) {
    const m = window.matchMedia(query)
    s = {
      subscribe: (cb) => {
        m.addEventListener('change', cb)
        return () => m.removeEventListener('change', cb)
      },
      get: () => m.matches,
    }
    stores.set(query, s)
  }
  return s
}

export function useMediaQuery(query: string): boolean {
  const s = store(query)
  return useSyncExternalStore(s.subscribe, s.get, () => false)
}

/** True at >= 900px: the sidebar is docked and drawers sit inline. */
export function useIsDocked(): boolean {
  return useMediaQuery(COMPACT_QUERY)
}
