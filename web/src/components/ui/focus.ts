/**
 * Focus management for the two overlay-like surfaces: the phone sheet
 * (Drawer below 900px) and the off-canvas sidebar.
 *
 * While `active`: Tab cycles inside `ref`, Esc calls `onEscape`, and focus
 * moves into the container. When it deactivates, focus returns to whatever
 * had it before (the row or hamburger that opened it).
 */
import { useEffect, useRef } from 'react'
import type { RefObject } from 'react'

const FOCUSABLE =
  'a[href],button:not([disabled]),input:not([disabled]),select:not([disabled]),textarea:not([disabled]),[tabindex]:not([tabindex="-1"])'

export function useFocusTrap(
  ref: RefObject<HTMLElement | null>,
  active: boolean,
  onEscape?: () => void,
  opts: { trap?: boolean } = {},
): void {
  const trap = opts.trap ?? true
  const escRef = useRef(onEscape)
  escRef.current = onEscape

  useEffect(() => {
    if (!active) return
    const opener = document.activeElement as HTMLElement | null
    const el = ref.current
    if (trap && el && !el.contains(document.activeElement)) {
      const first = el.querySelector<HTMLElement>(FOCUSABLE)
      ;(first ?? el).focus()
    }
    const onKey = (e: KeyboardEvent) => {
      if (e.key === 'Escape' && escRef.current) {
        e.stopPropagation()
        escRef.current()
        return
      }
      if (!trap || e.key !== 'Tab' || !ref.current) return
      const items = Array.from(ref.current.querySelectorAll<HTMLElement>(FOCUSABLE)).filter(
        (n) => n.offsetParent !== null,
      )
      if (items.length === 0) return
      const first = items[0]!
      const last = items[items.length - 1]!
      if (e.shiftKey && document.activeElement === first) {
        e.preventDefault()
        last.focus()
      } else if (!e.shiftKey && document.activeElement === last) {
        e.preventDefault()
        first.focus()
      }
    }
    document.addEventListener('keydown', onKey)
    return () => {
      document.removeEventListener('keydown', onKey)
      if (trap && opener && document.contains(opener)) opener.focus()
    }
  }, [active, trap, ref])
}
