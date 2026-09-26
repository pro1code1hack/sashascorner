/**
 * Debounced server preview for a draft. The draft is identified by `key` (a
 * stable JSON of what would be sent); a preview is only "for this draft" when
 * its key matches the current one. Apply buttons use exactly that test, so a
 * reviewer can never apply something other than what the panel shows.
 */
import { useEffect, useRef, useState } from 'react'
import type { SendResult } from '../../../lib/menu-api'
import type { PreviewStatus } from './Impact'

export interface PreviewState<T> {
  status: PreviewStatus
  data: T | null
  key: string | null
}

export function usePreview<T>(key: string | null, run: () => Promise<SendResult<T>>, delay = 350): PreviewState<T> {
  const [state, setState] = useState<PreviewState<T>>({ status: { kind: 'idle' }, data: null, key: null })
  const runRef = useRef(run)
  runRef.current = run
  useEffect(() => {
    if (key === null) {
      setState({ status: { kind: 'idle' }, data: null, key: null })
      return
    }
    let cancelled = false
    setState((s) => ({ ...s, status: { kind: 'loading' } }))
    const timer = window.setTimeout(async () => {
      const r = await runRef.current()
      if (cancelled) return
      if (r.kind === 'ok') setState({ status: { kind: 'ready' }, data: r.data, key })
      else setState({ status: { kind: r.kind, message: r.message }, data: null, key })
    }, delay)
    return () => {
      cancelled = true
      window.clearTimeout(timer)
    }
  }, [key, delay])
  return state
}
