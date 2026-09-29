/**
 * StatusLine: the one way to say what just happened after a write.
 *
 * Always mounted, so the live region exists BEFORE its text arrives (a
 * `role="status"` that mounts already containing text is not reliably
 * announced). Two regions: polite for outcomes, assertive for refusals.
 *
 *   const [outcome, setOutcome] = useState<Outcome | null>(null)
 *   <StatusLine outcome={outcome} />
 *   setOutcome({ kind: 'ok', text: 'Saved.' })
 *
 * Tones follow the design law: an ok outcome is plain ink-2 (green only lives in a
 * pill or the toggle track); an error is bad-ink; `undo` renders a link button.
 */
import type { ReactNode } from 'react'
import { Button } from './button'
import { cx } from './cx'

export interface Outcome {
  kind: 'ok' | 'error' | 'info'
  text: ReactNode
  /** "Undo" (or another verb) beside the sentence. */
  action?: { label: string; onClick: () => void }
}

export function StatusLine({ outcome, className }: { outcome: Outcome | null; className?: string }) {
  const err = outcome?.kind === 'error' ? outcome : null
  const ok = outcome && outcome.kind !== 'error' ? outcome : null
  const line = (o: Outcome | null) =>
    o === null ? null : (
      <>
        <span className={cx(o.kind === 'error' ? 'text-bad-ink' : 'text-ink-2')}>{o.text}</span>
        {o.action && (
          <Button variant="link" className="ml-2 inline-flex min-h-11 items-center compact:min-h-0" onClick={o.action.onClick}>
            {o.action.label}
          </Button>
        )}
      </>
    )
  return (
    <div className={cx('text-sm', outcome !== null && 'min-h-5', className)}>
      <p role="status" aria-live="polite">
        {line(ok)}
      </p>
      <p role="alert">{line(err)}</p>
    </div>
  )
}
