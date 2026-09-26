/**
 * One layout, chosen — not two rendered and one hidden.
 *
 * The table and the row-list are genuinely different DOM, not a restyle, and
 * rendering both would announce every item twice to a screen reader and put
 * 600 row-fragments on the page when "show all" is on. So the breakpoint is
 * read in JS, the way `Composition.tsx` already does it for its per-size grid.
 */
import { useEffect, useState } from 'react'

export function useWide(px = 1024): boolean {
  const query = `(min-width: ${px}px)`
  const [wide, setWide] = useState(
    () => typeof window !== 'undefined' && window.matchMedia(query).matches,
  )
  useEffect(() => {
    const mq = window.matchMedia(query)
    const on = () => setWide(mq.matches)
    on()
    mq.addEventListener('change', on)
    return () => mq.removeEventListener('change', on)
  }, [query])
  return wide
}
