/**
 * What comes back from a write, rendered honestly.
 *
 * `WriteResult` has four arms and each one means something different to the
 * person at the keyboard, so none of them collapses into the others:
 *
 *  - `refused` — the backend read the number and will not have it. The message
 *    was written to be shown ("a 1-day life against a 2-day transit buffer
 *    leaves nothing usable on arrival, so no quantity could ever be ordered")
 *    and is printed verbatim, next to the field it is about. It is not a
 *    failure: nothing is broken and nothing was written.
 *  - `offline` — the screen is reading recorded fixtures. There is nothing to
 *    write to, which is a property of how the page was built, not a fault.
 *  - `failed` — the request itself did not land. Status included, because 401
 *    and 500 are different problems.
 *  - `ok` — handled by the caller, which knows what consequence to report.
 */
import { Panel } from '../ui'
import type { WriteResult } from '../../lib/api'

/**
 * Which field a refusal belongs under.
 *
 * Placement only. The message is never edited, reworded or truncated by this —
 * the cues decide where the backend's sentence appears, and a sentence that
 * matches nothing is shown whole beside the submit button instead. Insertion
 * order of `cues` is the match order.
 */
export function refusalField<K extends string>(
  message: string,
  cues: Record<K, readonly string[]>,
): K | null {
  const m = message.toLowerCase()
  for (const key of Object.keys(cues) as K[]) {
    for (const cue of cues[key]) {
      if (m.includes(cue)) return key
    }
  }
  return null
}

/** A refusal that no field claimed, plus the two non-refusal failure arms. */
export function Outcome<T>({
  result,
  /** True when a field is already printing the refusal verbatim. */
  refusalPlaced = false,
}: {
  result: WriteResult<T> | null
  refusalPlaced?: boolean
}) {
  if (result === null) return null
  if (result.kind === 'ok') return null
  if (result.kind === 'refused') {
    if (refusalPlaced) {
      return (
        <Panel tone="bad" title="Nothing was written">
          The figure above was refused, for the reason printed beside it. Change it and confirm
          again — no part of this was saved.
        </Panel>
      )
    }
    return (
      <Panel tone="bad" title="Nothing was written">
        {result.message}
      </Panel>
    )
  }
  if (result.kind === 'offline') {
    return (
      <Panel tone="warn" title="This screen has nothing to write to">
        {result.message}
      </Panel>
    )
  }
  return (
    <Panel tone="bad" title="The request did not land">
      {result.message}
      {result.status !== null && <> (HTTP {result.status})</>}{' '}
      Nothing was written, and nothing was half-written: the confirmation is one transaction.
    </Panel>
  )
}

/**
 * Said before anybody types, not after they press the button. In fixture mode
 * the form is disabled — a form that looks live and then shrugs is worse than
 * one that says up front what it is.
 */
export function FixtureNotice({ what }: { what: string }) {
  return (
    <Panel tone="warn" title="Recorded fixtures — the form is disabled">
      This build is reading the recorded responses in <span className="fig">web/fixtures/</span>,
      so there is no database behind it and {what} cannot be recorded here. Nothing below is
      broken; it is a demo of the screen. Point the app at the live API (
      <span className="fig">VITE_LIVE=1</span>) to confirm anything for real.
    </Panel>
  )
}
