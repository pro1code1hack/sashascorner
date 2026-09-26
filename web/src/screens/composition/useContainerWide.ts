/**
 * Does the *pane* have room, rather than does the *window*.
 *
 * The per-size grid transposes below a certain width — sizes become rows, which
 * is the only way three editable figure columns fit a narrow column. The old
 * version asked the viewport (`min-width: 640px`), and that is the wrong
 * question in a three-pane layout: at a 1024px window the centre pane is about
 * 212px wide while the media query happily reports "wide", so a 400px grid was
 * laid out in a 212px column and the page scrolled sideways. Measuring the
 * container asks the question that actually decides it.
 *
 * It starts narrow and widens after the first measurement, because narrow is the
 * layout that cannot overflow.
 */
import { useEffect, useRef, useState, type RefObject } from 'react'

export function useContainerWide(px: number): [RefObject<HTMLDivElement>, boolean] {
  const ref = useRef<HTMLDivElement>(null)
  const [wide, setWide] = useState(false)

  useEffect(() => {
    const el = ref.current
    if (!el) return
    const measure = () => setWide(el.getBoundingClientRect().width >= px)
    measure()
    if (typeof ResizeObserver === 'undefined') {
      window.addEventListener('resize', measure)
      return () => window.removeEventListener('resize', measure)
    }
    const ro = new ResizeObserver(measure)
    ro.observe(el)
    return () => ro.disconnect()
  }, [px])

  return [ref, wide]
}
