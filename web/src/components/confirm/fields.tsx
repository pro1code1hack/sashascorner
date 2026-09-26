/**
 * Form pieces for the two confirmation screens. Nothing here is generic: it is
 * the smallest set of controls that says what these two forms have to say.
 *
 * The kit (`components/ui.tsx`) has no input, no label and no toggle, because
 * until now the dashboard was read-only. These are built from the same tokens
 * rather than a second palette — zinc-800 hairline, sunk well, blue focus from
 * the global `:focus-visible` rule — and every label is sentence case.
 *
 * Two rules the plain HTML would otherwise lose:
 *  - A refusal written by the backend renders verbatim under the field it is
 *    about (`error`), never rewritten and never swapped for "something went
 *    wrong". The sentence IS the instruction.
 *  - A pre-filled figure that nobody has confirmed is marked as a guess
 *    (`guess`), typographically rather than in colour, so the owner can see
 *    they are editing fiction rather than a record.
 */
import type { ReactNode } from 'react'
import { ISO_DAYS } from './numbers'

const INPUT =
  'fig w-full rounded-control border bg-sunk px-2.5 py-1.5 text-[0.875rem] text-ink ' +
  'placeholder:text-ink-5 placeholder:not-italic disabled:cursor-not-allowed disabled:opacity-50'

function ring(error: boolean): string {
  return error ? 'border-bad/60' : 'border-line-2'
}

/**
 * One labelled control: a real `<label>` bound by id for a single input, a
 * heading plus `aria-labelledby` for a group of buttons or radios. The hint is
 * referenced by `aria-describedby` and the error is announced.
 */
export function Field({
  id,
  label,
  hint,
  guess,
  error,
  group = false,
  children,
}: {
  id: string
  label: ReactNode
  hint?: ReactNode
  /** What the seeder guessed, when that is what the control is pre-filled with. */
  guess?: ReactNode
  /** Verbatim from the backend, or a local parse message. Never summarised. */
  error?: string | null
  /** True when the control is a group of buttons or radios rather than one
   *  input: `<label for>` only points at a labelable element, so a group gets a
   *  plain heading that it references with `aria-labelledby` instead. */
  group?: boolean
  children: ReactNode
}) {
  const heading = 'block text-[0.75rem] font-medium text-ink-2'
  return (
    <div className="min-w-0">
      {group ? (
        <div id={`${id}-label`} className={heading}>
          {label}
        </div>
      ) : (
        <label htmlFor={id} className={heading}>
          {label}
        </label>
      )}
      <div className="mt-1.5">{children}</div>
      {guess !== undefined && (
        <p className="mt-1 text-[0.6875rem] leading-[16px] text-ink-4">
          seeded guess:{' '}
          <span className="fig underline decoration-dotted decoration-ink-5 underline-offset-[3px]">
            {guess}
          </span>
        </p>
      )}
      {hint && (
        <p id={`${id}-hint`} className="mt-1 text-[0.6875rem] leading-[16px] text-ink-4">
          {hint}
        </p>
      )}
      {error != null && error !== '' && (
        <p
          id={`${id}-error`}
          role="alert"
          className="mt-1.5 text-[0.75rem] leading-[16px] text-bad-ink"
        >
          {error}
        </p>
      )}
    </div>
  )
}

/** A number typed as text. `inputMode` gets the phone keypad without `type=number`,
 *  whose spinners and scroll-wheel behaviour lose figures on a laptop. */
export function TextInput({
  id,
  value,
  onChange,
  placeholder,
  disabled,
  invalid = false,
  hint = false,
  numeric = false,
  prefix,
  suffix,
  width = 'full',
}: {
  id: string
  value: string
  onChange: (v: string) => void
  placeholder?: string
  disabled?: boolean
  invalid?: boolean
  hint?: boolean
  numeric?: boolean
  prefix?: string
  suffix?: string
  width?: 'full' | 'short'
}) {
  const input = (
    <input
      id={id}
      type="text"
      inputMode={numeric ? 'decimal' : 'text'}
      autoComplete="off"
      value={value}
      disabled={disabled}
      aria-invalid={invalid || undefined}
      aria-describedby={
        [hint ? `${id}-hint` : null, invalid ? `${id}-error` : null].filter(Boolean).join(' ') ||
        undefined
      }
      onChange={(e) => onChange(e.target.value)}
      placeholder={placeholder}
      className={`${INPUT} ${ring(invalid)} ${prefix ? 'pl-6' : ''} ${suffix ? 'pr-12' : ''}`}
    />
  )
  return (
    <div className={`relative ${width === 'short' ? 'max-w-[10rem]' : ''}`}>
      {prefix && (
        <span className="pointer-events-none absolute left-2.5 top-1/2 -translate-y-1/2 text-[0.875rem] text-ink-4">
          {prefix}
        </span>
      )}
      {input}
      {suffix && (
        <span className="pointer-events-none absolute right-2.5 top-1/2 -translate-y-1/2 text-[0.75rem] text-ink-4">
          {suffix}
        </span>
      )}
    </div>
  )
}

/**
 * The delivery week as seven toggles rather than a text box. "1,3,5" is a thing
 * you can mistype into an unsatisfiable cover window; a row of days is not.
 */
export function DayToggles({
  id,
  selected,
  onChange,
  disabled,
  invalid = false,
}: {
  id: string
  selected: readonly number[]
  onChange: (days: number[]) => void
  disabled?: boolean
  invalid?: boolean
}) {
  const set = new Set(selected)
  const toggle = (iso: number) => {
    const next = new Set(set)
    if (next.has(iso)) next.delete(iso)
    else next.add(iso)
    onChange([...next].sort((a, b) => a - b))
  }
  return (
    <div
      id={id}
      role="group"
      aria-labelledby={`${id}-label`}
      aria-describedby={`${id}-hint`}
      aria-invalid={invalid || undefined}
      className="flex flex-wrap items-center gap-1"
    >
      {ISO_DAYS.map((d) => {
        const on = set.has(d.iso)
        return (
          <button
            key={d.iso}
            type="button"
            disabled={disabled}
            aria-pressed={on}
            aria-label={d.long}
            onClick={() => toggle(d.iso)}
            className={`rounded-control border px-2.5 py-1.5 text-[0.75rem] font-medium transition-colors disabled:cursor-not-allowed disabled:opacity-50 ${
              on
                ? 'border-brand bg-brand-wash text-ink'
                : `${ring(invalid)} bg-surface text-ink-4 hover:text-ink-2`
            }`}
          >
            {d.short}
          </button>
        )
      })}
      <button
        type="button"
        disabled={disabled}
        onClick={() => onChange(selected.length === 7 ? [] : [1, 2, 3, 4, 5, 6, 7])}
        className="ml-1 rounded-control border border-transparent px-2 py-1.5 text-[0.6875rem] text-ink-4 transition-colors hover:bg-raised hover:text-ink disabled:cursor-not-allowed disabled:opacity-50"
      >
        {selected.length === 7 ? 'clear' : 'all seven'}
      </button>
    </div>
  )
}

/**
 * A choice between named alternatives, as real radios: arrow keys work, the
 * group is one tab stop, and there is no third option pretending to be one.
 */
export function Choice<T extends string>({
  id,
  name,
  value,
  options,
  onChange,
  disabled,
}: {
  id: string
  name: string
  value: T
  options: readonly { value: T; label: string; detail: string }[]
  onChange: (v: T) => void
  disabled?: boolean
}) {
  return (
    <div
      id={id}
      role="radiogroup"
      aria-labelledby={`${id}-label`}
      aria-describedby={`${id}-hint`}
      className="grid gap-1"
    >
      {options.map((o) => (
        <label
          key={o.value}
          className={`flex cursor-pointer items-start gap-2.5 rounded-control border px-3 py-2 transition-colors ${
            value === o.value
              ? 'border-brand bg-brand-wash'
              : 'border-line-2 bg-surface hover:border-ink-5'
          } ${disabled ? 'cursor-not-allowed opacity-50' : ''}`}
        >
          <input
            type="radio"
            name={name}
            value={o.value}
            checked={value === o.value}
            disabled={disabled}
            onChange={() => onChange(o.value)}
            className="mt-[3px] accent-brand"
          />
          <span className="min-w-0">
            <span className="block text-[0.875rem] leading-[16px] text-ink">{o.label}</span>
            <span className="mt-0.5 block text-[0.6875rem] leading-[16px] text-ink-4">{o.detail}</span>
          </span>
        </label>
      ))}
    </div>
  )
}

/**
 * The field grid. `[&>*]:min-w-0` is load-bearing and not decoration: a grid
 * item's own `min-width: auto` floors at min-content, so one wide control
 * pushes the whole page sideways rather than shrinking inside its track. Tracks
 * are `minmax(0,1fr)` for the same reason.
 */
export function FieldGrid({ children }: { children: ReactNode }) {
  return (
    <div className="grid grid-cols-[minmax(0,1fr)] gap-x-5 gap-y-4 [&>*]:min-w-0 sm:grid-cols-[minmax(0,1fr)_minmax(0,1fr)]">
      {children}
    </div>
  )
}

/**
 * A consent control: one deliberate decision, not a styling of `<input>`.
 *
 * Three screens needed this and each was about to build its own. The shape is
 * the one that proved itself on the import-review screen: a bordered block that
 * tints when checked, taking `children` rather than a label string, so the
 * CONSEQUENCE of agreeing can sit inside the control next to the tick. The one
 * caller that matters — accepting the lowest quantity where legacy rows disagree
 * — is arbitrary by construction, and the reader should see the resulting
 * figures before agreeing rather than after.
 *
 * A native checkbox with `accent-*`, not a replaced one: label association,
 * keyboard behaviour, screen readers and forced-colours modes all come free.
 */
export function OptIn({
  id,
  checked,
  onChange,
  disabled,
  tone = 'warn',
  children,
}: {
  id: string
  checked: boolean
  onChange: (v: boolean) => void
  disabled?: boolean
  tone?: 'warn' | 'brand'
  children: ReactNode
}) {
  const on = tone === 'warn' ? 'border-warn/40 bg-warn-wash' : 'border-brand/40 bg-brand-wash'
  const accent = tone === 'warn' ? 'accent-warn' : 'accent-brand'
  return (
    <label
      htmlFor={id}
      className={`flex max-w-[80ch] items-start gap-2.5 rounded-control border px-3 py-2.5 transition-colors ${
        checked ? on : 'border-line-2 bg-surface hover:border-ink-5'
      } ${disabled ? 'cursor-not-allowed opacity-50' : 'cursor-pointer'}`}
    >
      <input
        id={id}
        type="checkbox"
        checked={checked}
        disabled={disabled}
        onChange={(e) => onChange(e.target.checked)}
        className={`mt-[3px] ${accent}`}
      />
      <span className="min-w-0">{children}</span>
    </label>
  )
}
