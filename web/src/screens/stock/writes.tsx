/**
 * One write at a time, with the server's sentence shown verbatim.
 *
 * FRONTEND-KIT rule 10: nothing is optimistic. A write runs, and on success the
 * caller's query keys are invalidated so the screen re-reads the server's
 * figures; on refusal the message the service wrote is shown as-is.
 *
 * `OutcomeLine` is the kit's `StatusLine` under the older `{ tone, text }`
 * shape: the live region is ALWAYS mounted, so it exists before its text
 * arrives (a `role="status"` that appears already filled is not reliably
 * announced). Other areas import it, so the shape stays.
 */
import { useState } from 'react'
import { useQueryClient } from '@tanstack/react-query'
import type { QueryKey } from '@tanstack/react-query'
import { StatusLine } from '../../components/ui'
import type { WriteResult } from '../../lib/api'
import { SHELL_QUERY_KEY } from '../../lib/shell-api'

export type Outcome = { tone: 'ok' | 'bad'; text: string } | null

export function useWrite() {
  const qc = useQueryClient()
  const [pending, setPending] = useState(false)
  const [outcome, setOutcome] = useState<Outcome>(null)

  async function run<T>(
    call: () => Promise<WriteResult<T>>,
    opts: { invalidate?: QueryKey[]; ok?: (data: T) => string | null; after?: (data: T) => void } = {},
  ): Promise<T | null> {
    setPending(true)
    setOutcome(null)
    try {
      const r = await call()
      if (r.kind === 'ok') {
        const text = opts.ok ? opts.ok(r.data) : null
        setOutcome(text ? { tone: 'ok', text } : null)
        for (const key of [...(opts.invalidate ?? []), SHELL_QUERY_KEY]) {
          await qc.invalidateQueries({ queryKey: key })
        }
        opts.after?.(r.data)
        return r.data
      }
      setOutcome({ tone: 'bad', text: r.message })
      return null
    } finally {
      setPending(false)
    }
  }

  return { pending, outcome, setOutcome, run }
}

/** The line under an action: what the server said. Always mounted; refusals verbatim. */
export function OutcomeLine({ outcome, className }: { outcome: Outcome; className?: string }) {
  return (
    <StatusLine
      outcome={outcome === null ? null : { kind: outcome.tone === 'bad' ? 'error' : 'ok', text: outcome.text }}
      className={className}
    />
  )
}
