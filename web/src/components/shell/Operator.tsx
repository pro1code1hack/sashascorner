/**
 * "Who's using this?" affordances (DECISIONS §6, stock-orders-suppliers.md C10).
 *
 * - `OperatorControl`: the sidebar line. Shows the name, or asks for one;
 *   editable inline.
 * - `OperatorNeeded`: drop it next to a write that needs a name. Renders
 *   nothing once a name is set; otherwise a one-line inline form, so the
 *   person never has to leave the screen to unblock the button.
 *
 * Screens read the name with `useOperator()` from lib/operator and pass it as
 * `counted_by` / `received_by` / `responded_by` / `decided_by` / `actor`.
 * Any write that needs it stays disabled while it is null.
 */
import { useId, useState } from 'react'
import type { FormEvent } from 'react'
import { normaliseOperator, useOperator } from '../../lib/operator'
import { Button, cx } from '../ui'

function NameForm({
  initial,
  onDone,
  onCancel,
  compact = false,
  autoFocus = false,
}: {
  initial: string
  onDone: (name: string) => void
  onCancel?: () => void
  compact?: boolean
  /** Only when the person asked to edit; never on first render (it would steal focus from the page). */
  autoFocus?: boolean
}) {
  const [value, setValue] = useState(initial)
  const id = useId()
  const submit = (e: FormEvent) => {
    e.preventDefault()
    const v = normaliseOperator(value)
    if (v !== null) onDone(v)
  }
  return (
    <form onSubmit={submit} className={cx('flex min-w-0 items-center gap-1.5', compact && 'flex-wrap')}>
      <label htmlFor={id} className="sr-only">
        Your name
      </label>
      <input
        id={id}
        autoFocus={autoFocus}
        value={value}
        maxLength={60}
        onChange={(e) => setValue(e.target.value)}
        onKeyDown={(e) => {
          if (e.key === 'Escape' && onCancel) {
            e.stopPropagation()
            onCancel()
          }
        }}
        placeholder="Your name"
        autoComplete="off"
        className="h-8 min-w-0 flex-1 rounded-control border border-line-strong bg-surface px-2 text-base outline-none placeholder:text-ink-3 focus-visible:border-brand focus-visible:ring-3 focus-visible:ring-brand-wash"
      />
      <Button type="submit" variant="outline" size="sm" disabled={normaliseOperator(value) === null}>
        Save
      </Button>
    </form>
  )
}

/** Sidebar footer line: "Using as Sasha · Change" or "Who's using this?". */
export function OperatorControl() {
  const [name, setName] = useOperator()
  const [editing, setEditing] = useState(false)

  if (editing || name === null) {
    return (
      <div className="flex flex-col gap-1 px-2.5 py-1.5">
        <div className="text-xs font-bold text-ink-2">Who&rsquo;s using this?</div>
        <NameForm
          compact
          autoFocus={editing}
          initial={name ?? ''}
          onDone={(v) => {
            setName(v)
            setEditing(false)
          }}
          onCancel={name === null ? undefined : () => setEditing(false)}
        />
      </div>
    )
  }
  return (
    <div className="flex min-h-9 items-center gap-1.5 px-2.5 text-sm text-ink-2">
      <span className="min-w-0 truncate">
        Using as <span className="font-bold text-ink">{name}</span>
      </span>
      <button
        type="button"
        onClick={() => setEditing(true)}
        className="flex-none rounded-sm px-1 text-sm text-brand-ink underline underline-offset-2"
        aria-label={`Change who is using this device (now ${name})`}
      >
        Change
      </button>
    </div>
  )
}

/**
 * Inline prompt for a write that needs a name. `what` finishes the sentence
 * "Say who you are to …": "record a count".
 */
export function OperatorNeeded({ what }: { what: string }) {
  const [name, setName] = useOperator()
  if (name !== null) return null
  return (
    <div className="flex flex-col gap-1.5 rounded-button bg-canvas px-3 py-2.5 text-base">
      <span className="text-ink-2">Say who you are to {what}. It is remembered on this device.</span>
      <NameForm initial="" onDone={setName} />
    </div>
  )
}
