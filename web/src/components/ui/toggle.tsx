/**
 * Toggle, Checkbox, Stepper, SizeTile: design-system.md §5.3.
 */
import type { ReactNode } from 'react'
import { cx } from './cx'

/** On/off switch. Track `ok` when on (the one place green is a fill). */
export function Toggle({
  checked,
  onChange,
  label,
  disabled,
  className,
}: {
  checked: boolean
  onChange: (next: boolean) => void
  /** Visible label to the right. If omitted, pass `aria-label` via `label` anyway. */
  label: ReactNode
  disabled?: boolean
  className?: string
}) {
  return (
    <button
      type="button"
      role="switch"
      aria-checked={checked}
      disabled={disabled}
      onClick={() => onChange(!checked)}
      className={cx('inline-flex items-center gap-2.5 rounded-full disabled:opacity-50', className)}
    >
      <span
        className={cx(
          'relative h-6 w-10 flex-none rounded-full transition-colors',
          checked ? 'bg-ok' : 'bg-line-strong',
        )}
        aria-hidden="true"
      >
        <span
          className={cx(
            'absolute top-[3px] size-[18px] rounded-full bg-white shadow-knob transition-[left]',
            checked ? 'left-[19px]' : 'left-[3px]',
          )}
        />
      </span>
      <span className="text-base font-semibold">{label}</span>
    </button>
  )
}

export function Checkbox({
  checked,
  onChange,
  label,
  alert = false,
  disabled,
  className,
}: {
  checked: boolean
  onChange: (next: boolean) => void
  label: ReactNode
  /** Terms-confirmed variant: border and label in `alert` while unchecked. */
  alert?: boolean
  disabled?: boolean
  className?: string
}) {
  const warn = alert && !checked
  return (
    <button
      type="button"
      role="checkbox"
      aria-checked={checked}
      disabled={disabled}
      onClick={() => onChange(!checked)}
      className={cx('inline-flex items-center gap-2 text-left disabled:opacity-50', className)}
    >
      <span
        className={cx(
          'grid size-[18px] flex-none place-items-center rounded-xs border-[1.5px] bg-surface',
          warn ? 'border-alert' : 'border-line-strong',
        )}
        aria-hidden="true"
      >
        {checked && <span className="text-xs font-extrabold leading-none text-brand-ink">✓</span>}
      </span>
      <span className={cx('text-base', warn && 'font-bold text-alert')}>{label}</span>
    </button>
  )
}

/**
 * Round − / + buttons around a value. The value is a string the caller owns;
 * the stepper only asks to go down or up, so quantity arithmetic stays in
 * lib/dec.
 */
export function Stepper({
  value,
  onDecrement,
  onIncrement,
  label,
  size = 'sm',
  canDecrement = true,
  canIncrement = true,
  children,
}: {
  value: ReactNode
  onDecrement: () => void
  onIncrement: () => void
  /** What is being stepped, for the button labels: "packs". */
  label: string
  size?: 'sm' | 'lg'
  canDecrement?: boolean
  canIncrement?: boolean
  /** Replace the value with an input (count mode). */
  children?: ReactNode
}) {
  const btn = cx(
    'grid flex-none place-items-center rounded-full border bg-surface leading-none hover:bg-canvas disabled:opacity-50',
    size === 'sm' ? 'size-[22px] border-line-strong text-sm' : 'size-[50px] border-line text-2xl',
  )
  return (
    <span className="inline-flex items-center gap-1.5">
      <button type="button" className={btn} onClick={onDecrement} disabled={!canDecrement} aria-label={`Fewer ${label}`}>
        <span aria-hidden="true">−</span>
      </button>
      {children ?? (
        <span
          className={cx('fig text-center', size === 'sm' ? 'min-w-11 text-base' : 'min-w-20 text-2xl')}
          aria-live="polite"
        >
          {value}
        </span>
      )}
      <button type="button" className={btn} onClick={onIncrement} disabled={!canIncrement} aria-label={`More ${label}`}>
        <span aria-hidden="true">+</span>
      </button>
    </span>
  )
}

/** A size choice (S / M / XL / One) with its price underneath. */
export function SizeTile({
  label,
  sub,
  active,
  onClick,
  add = false,
}: {
  label: ReactNode
  sub?: ReactNode
  active?: boolean
  onClick?: () => void
  /** The dashed "+ size" tile. */
  add?: boolean
}) {
  return (
    <button
      type="button"
      onClick={onClick}
      aria-pressed={add ? undefined : Boolean(active)}
      className={cx(
        'flex h-[52px] min-w-16 flex-col items-center justify-center rounded-button px-3 transition-colors',
        add
          ? 'border-[1.5px] border-dashed border-line-strong text-brand-ink hover:bg-canvas'
          : active
            ? 'border border-brand-line bg-brand-wash text-brand-ink'
            : 'border border-line-control bg-surface hover:bg-canvas',
      )}
    >
      <span className="text-md font-extrabold">{label}</span>
      {sub !== undefined && <span className="fig text-xs opacity-80">{sub}</span>}
    </button>
  )
}
