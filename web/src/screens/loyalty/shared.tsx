/**
 * Pieces the Programme tab's rule forms share: the manager PIN, the write runner
 * with its always-mounted outcome line, and the white Panel every Loyalty page is
 * built from.
 */
import { useId, useState } from 'react'
import type { ReactNode } from 'react'
import { Button, Field, Input, StatusLine, cx } from '../../components/ui'
import type { Outcome } from '../../components/ui'
import { href } from '../../lib/router'
import type { WriteResult } from '../../lib/api'
import type { StaffRole } from '../../lib/types/members'

export const ROLE_LABEL: Record<StaffRole, string> = { STAFF: 'Staff', MANAGER: 'Manager', OWNER: 'Owner' }

/* ----------------------------------------------------------------- PINs --- */

export const PIN_RE = /^\d{4,6}$/

/** A 4–6 digit PIN box: masked, digits only, never autofilled. */
export function PinInput({
  value,
  onChange,
  label,
  className,
}: {
  value: string
  onChange: (v: string) => void
  label?: string
  className?: string
}) {
  return (
    <Input
      type="password"
      inputMode="numeric"
      autoComplete="off"
      maxLength={6}
      aria-label={label}
      placeholder="4–6 digits"
      value={value}
      onChange={(e) => {
        const v = e.target.value.replace(/\D/g, '')
        onChange(v.slice(0, 6))
      }}
      className={cx('fig', className)}
    />
  )
}

/**
 * The foot of a rules form: the manager PIN (only once a manager exists; before
 * that the back-office password is the guard, or nothing could ever be saved on
 * a fresh install), the change count and the save button.
 */
export function RulesSave({
  pinRequired,
  pin,
  setPin,
  changes,
  canSave,
  busy,
  label,
  outcome,
}: {
  pinRequired: boolean
  pin: string
  setPin: (v: string) => void
  changes: number
  canSave: boolean
  busy: boolean
  label: string
  outcome: Outcome | null
}) {
  const count = changes === 0 ? 'Nothing changed yet.' : `${changes} ${changes === 1 ? 'change' : 'changes'} to save.`
  return (
    <div className="flex flex-col gap-2">
      <div className="flex flex-wrap items-end gap-3">
        {pinRequired && (
          <Field label="Manager PIN" hint="A manager or owner approves rule changes.">
            <div className="w-36">
              <PinInput value={pin} onChange={setPin} />
            </div>
          </Field>
        )}
        <Button type="submit" variant="primary" disabled={!canSave} pending={busy} pendingLabel="Saving…" className={pinRequired ? 'mb-5' : undefined}>
          {label}
        </Button>
        <span className={cx('text-sm text-ink-2', pinRequired && 'mb-7')}>{count}</span>
      </div>
      {!pinRequired && (
        <p className="text-sm text-ink-2">
          No manager has a PIN yet, so changes save with the back-office password alone.{' '}
          <a href={href('/loyalty/programme')} className="font-bold text-brand-ink underline">
            Add a manager
          </a>{' '}
          under Staff & devices to have rule changes approved with a PIN.
        </p>
      )}
      <StatusLine outcome={outcome} />
    </div>
  )
}

/* -------------------------------------------------------------- writes --- */

/** A write's state: busy flag, the last outcome (for a `StatusLine`), and a runner. */
export function useWriteState() {
  const [busy, setBusy] = useState(false)
  const [outcome, setOutcome] = useState<Outcome | null>(null)
  /** Resolves to `{ data }` when the write landed (data is `null` for a 204), else `null`. */
  async function run<T>(fn: () => Promise<WriteResult<T>>, ok: (data: T) => string | void): Promise<{ data: T } | null> {
    setBusy(true)
    setOutcome(null)
    const r = await fn()
    setBusy(false)
    if (r.kind === 'ok') {
      const text = ok(r.data)
      if (text) setOutcome({ kind: 'ok', text })
      return { data: r.data }
    }
    setOutcome({ kind: 'error', text: r.message })
    return null
  }
  return { busy, outcome, setOutcome, run }
}

/* --------------------------------------------------------------- panel --- */

/**
 * A white panel with its heading, as on the item and order pages. The section is
 * labelled by its heading; `as="h3"` when the panel sits inside an h2 section
 * (Staff & devices under Programme).
 */
export function Panel({
  title,
  right,
  children,
  className,
  id,
  as: As = 'h2',
}: {
  title?: ReactNode
  right?: ReactNode
  children: ReactNode
  className?: string
  /** Heading id; generated when omitted. */
  id?: string
  as?: 'h2' | 'h3'
}) {
  const auto = useId()
  const headingId = id ?? `${auto}-h`
  return (
    <section
      aria-labelledby={title ? headingId : undefined}
      className={cx('min-w-0 rounded-card-lg border border-line bg-surface px-4 py-4 sm:px-5', className)}
    >
      {(title || right) && (
        <div className="mb-3 flex flex-wrap items-baseline justify-between gap-x-3 gap-y-1">
          {title && (
            <As id={headingId} className="text-lg font-extrabold tracking-[-.01em]">
              {title}
            </As>
          )}
          {right}
        </div>
      )}
      {children}
    </section>
  )
}
