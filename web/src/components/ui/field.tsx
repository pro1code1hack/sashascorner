/**
 * Inputs, selects and the Field label wrapper: design-system.md §5.2.
 *
 * `Field` gives its control an id and wires label, hint and error to it through
 * context, so `<Field label="Pack size"><Input /></Field>` is fully labelled
 * without passing ids by hand.
 *
 * Quantities and money are STRINGS here (CLAUDE.md §10.10). Parse them with
 * lib/dec or components/confirm/numbers at submit time, never with Number().
 */
import { createContext, forwardRef, useContext, useId } from 'react'
import type { InputHTMLAttributes, ReactNode, SelectHTMLAttributes, TextareaHTMLAttributes } from 'react'
import { cx } from './cx'

type FieldCtx = { id: string; describedBy: string | undefined; invalid: boolean }
const FieldContext = createContext<FieldCtx | null>(null)

function useFieldProps(id: string | undefined, invalidProp: boolean | undefined) {
  const f = useContext(FieldContext)
  return {
    id: id ?? f?.id,
    'aria-describedby': f?.describedBy,
    'aria-invalid': invalidProp || f?.invalid || undefined,
  }
}

export function Field({
  label,
  hint,
  error,
  upper = false,
  className,
  children,
}: {
  label: ReactNode
  /** Meta under the control, `text-xs text-ink-2`. */
  hint?: ReactNode
  /** Inline validation, `text-sm text-bad-ink`. Marks the control invalid. */
  error?: ReactNode
  /** The recipe editor's uppercase label (11/700/.05em). Default is sentence case. */
  upper?: boolean
  className?: string
  children: ReactNode
}) {
  const id = useId()
  const hintId = hint ? `${id}-hint` : undefined
  const errId = error ? `${id}-err` : undefined
  const describedBy = [hintId, errId].filter(Boolean).join(' ') || undefined
  return (
    <FieldContext.Provider value={{ id, describedBy, invalid: Boolean(error) }}>
      <div className={cx('flex min-w-0 flex-col gap-1', className)}>
        <label
          htmlFor={id}
          className={cx(
            // Design: ink-3. Labels carry information, so ink-2 (§1.2).
            'text-ink-2',
            upper ? 'text-label font-bold uppercase tracking-[.05em]' : 'text-xs font-bold',
          )}
        >
          {label}
        </label>
        {children}
        {hint && (
          <div id={hintId} className="text-xs text-ink-2">
            {hint}
          </div>
        )}
        {error && (
          <div id={errId} className="text-sm text-bad-ink">
            {error}
          </div>
        )}
      </div>
    </FieldContext.Provider>
  )
}

export type InputSize = 'md' | 'sm' | 'xs'

const BOX: Record<InputSize, string> = {
  md: 'h-10 px-2.5 rounded-control border border-line-control text-md',
  sm: 'h-[38px] px-2.5 rounded-control border border-line-control text-base',
  xs: 'h-7 px-1.5 rounded-control border border-line-strong text-base',
}

const FOCUS = 'outline-none focus-visible:edge-brand'

export interface InputProps extends Omit<InputHTMLAttributes<HTMLInputElement>, 'size'> {
  size?: InputSize
  /** Money or quantity: right-aligned, tabular. */
  numeric?: boolean
  /** Differs from the saved value (recipe editor): alert border on alert wash. */
  changed?: boolean
  /** A value that must be set is not: alert border. */
  missing?: boolean
  /** The value is a guess: italic. */
  est?: boolean
}

export const Input = forwardRef<HTMLInputElement, InputProps>(function Input(
  { size = 'md', numeric, changed, missing, est, className, id, 'aria-invalid': ai, ...rest },
  ref,
) {
  const fp = useFieldProps(id, Boolean(ai) || missing)
  return (
    <input
      ref={ref}
      {...fp}
      inputMode={numeric ? 'decimal' : undefined}
      className={cx(
        'w-full min-w-0 bg-surface placeholder:text-ink-3 disabled:opacity-50',
        BOX[size],
        FOCUS,
        numeric && 'text-right fig',
        (changed || missing) && 'border-alert',
        changed && 'bg-alert-wash',
        est && 'italic',
        className,
      )}
      {...rest}
    />
  )
})

/** `£` prefix + numeric Input. `big` is the borderless figure inside a Tile. */
export const MoneyInput = forwardRef<HTMLInputElement, InputProps & { big?: boolean }>(
  function MoneyInput({ big = false, className, ...rest }, ref) {
    if (big) {
      return (
        <div className="flex items-baseline gap-0.5">
          <span className="text-sm text-ink-3" aria-hidden="true">
            £
          </span>
          <Input
            ref={ref}
            numeric
            className={cx(
              'h-auto! border-0! bg-transparent! px-0! text-left! text-xl font-extrabold tracking-[-.01em]',
              className,
            )}
            {...rest}
          />
        </div>
      )
    }
    return (
      <div className="relative">
        <span
          className="pointer-events-none absolute left-2.5 top-1/2 -translate-y-1/2 text-sm text-ink-3"
          aria-hidden="true"
        >
          £
        </span>
        <Input ref={ref} numeric className={cx('pl-6', className)} {...rest} />
      </div>
    )
  },
)

export const SearchInput = forwardRef<HTMLInputElement, Omit<InputProps, 'size'> & { label?: string }>(
  function SearchInput({ className, label = 'Search', placeholder = 'Search', ...rest }, ref) {
    return (
      <div
        className={cx(
          'flex h-10 min-w-0 items-center gap-2 rounded-button border border-line-control bg-surface px-3',
          'focus-within:edge-brand',
          className,
        )}
      >
        <span className="size-3 flex-none rounded-full border-2 border-ink-3" aria-hidden="true" />
        <input
          ref={ref}
          type="search"
          aria-label={label}
          placeholder={placeholder}
          className="h-full w-full min-w-0 bg-transparent text-md outline-none placeholder:text-ink-3 focus-visible:outline-none"
          {...rest}
        />
      </div>
    )
  },
)

/** Borderless entity name: ingredient, supplier, recipe. */
export const TitleInput = forwardRef<
  HTMLInputElement,
  InputHTMLAttributes<HTMLInputElement> & { variant?: 'page' | 'compact' }
>(function TitleInput({ variant = 'page', className, ...rest }, ref) {
  return (
    <input
      ref={ref}
      className={cx(
        // Focus is a 2px brand underline: the 1px border alone was too faint to find.
        'w-full min-w-0 border-b border-line bg-transparent py-0.5 font-extrabold tracking-[-.01em] text-ink',
        'outline-none focus-visible:border-brand focus-visible:shadow-focus-underline',
        variant === 'page' ? 'text-3xl' : 'text-2xl',
        className,
      )}
      {...rest}
    />
  )
})

export interface SelectProps extends Omit<SelectHTMLAttributes<HTMLSelectElement>, 'size'> {
  size?: InputSize
  /** `role` is the component-role picker in the recipe editor. */
  variant?: 'box' | 'role'
}

export const Select = forwardRef<HTMLSelectElement, SelectProps>(function Select(
  { size = 'md', variant = 'box', className, id, children, ...rest },
  ref,
) {
  const fp = useFieldProps(id, false)
  return (
    <select
      ref={ref}
      {...fp}
      className={cx(
        'min-w-0 bg-surface disabled:opacity-50',
        variant === 'role'
          ? 'h-[38px] w-[130px] rounded-control border border-canvas bg-canvas px-2 text-xs font-bold tracking-[.04em]'
          : cx('w-full', BOX[size]),
        FOCUS,
        className,
      )}
      {...rest}
    >
      {children}
    </select>
  )
})

export const Textarea = forwardRef<HTMLTextAreaElement, TextareaHTMLAttributes<HTMLTextAreaElement>>(
  function Textarea({ className, id, ...rest }, ref) {
    const fp = useFieldProps(id, false)
    return (
      <textarea
        ref={ref}
        {...fp}
        className={cx(
          'min-h-20 w-full min-w-0 rounded-control border border-line-control bg-surface px-2.5 py-2 text-md placeholder:text-ink-3',
          FOCUS,
          className,
        )}
        {...rest}
      />
    )
  },
)

/** The login password box: 48px, radius 14, 17px text. */
export const PasswordInput = forwardRef<HTMLInputElement, InputHTMLAttributes<HTMLInputElement>>(
  function PasswordInput({ className, id, ...rest }, ref) {
    const fp = useFieldProps(id, false)
    return (
      <input
        ref={ref}
        type="password"
        {...fp}
        className={cx(
          'h-12 w-full rounded-card border border-line-strong bg-surface px-3.5 text-lg placeholder:text-ink-3',
          FOCUS,
          className,
        )}
        {...rest}
      />
    )
  },
)
