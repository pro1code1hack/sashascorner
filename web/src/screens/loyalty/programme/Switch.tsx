/**
 * A settings row with the design's blue switch: a bold label, a muted line under it,
 * and the switch on the right. (The kit's Toggle is the green stock-screen one; the
 * Loyalty design draws these in the brand blue.)
 */
import { useId } from 'react'
import { cx } from '../../../components/ui'

export function SwitchRow({
  label,
  hint,
  checked,
  onChange,
  disabled,
}: {
  label: string
  hint: string
  checked: boolean
  onChange: (next: boolean) => void
  disabled?: boolean
}) {
  const id = useId()
  return (
    <div className="flex items-center gap-4 border-t border-line-soft py-3.5">
      <div className="min-w-0 flex-1">
        <div id={`${id}-l`} className="text-base font-bold">
          {label}
        </div>
        <div id={`${id}-h`} className="text-sm text-ink-2">
          {hint}
        </div>
      </div>
      <button
        type="button"
        role="switch"
        aria-checked={checked}
        aria-labelledby={`${id}-l`}
        aria-describedby={`${id}-h`}
        disabled={disabled}
        onClick={() => onChange(!checked)}
        className="flex-none rounded-full disabled:opacity-50"
      >
        <span
          aria-hidden="true"
          className={cx(
            'relative block h-[26px] w-[44px] rounded-full transition-colors motion-reduce:transition-none',
            checked ? 'bg-brand' : 'bg-line-strong',
          )}
        >
          <span
            className={cx(
              'absolute top-[3px] size-5 rounded-full bg-surface shadow-knob transition-[left] motion-reduce:transition-none',
              checked ? 'left-[21px]' : 'left-[3px]',
            )}
          />
        </span>
      </button>
    </div>
  )
}
