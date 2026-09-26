/**
 * Buttons: design-system.md §5.1. Always a real <button>; the design's
 * <div onClick> is not keyboard accessible.
 */
import { forwardRef, useEffect, useRef, useState } from 'react'
import type { ButtonHTMLAttributes, ReactNode } from 'react'
import { cx } from './cx'

export type ButtonVariant =
  | 'primary'
  | 'secondary'
  | 'outline'
  | 'danger'
  | 'danger-soft'
  | 'on-wash'
  | 'ghost'
  | 'link'
  | 'add'

export type ButtonSize = 'sm' | 'md' | 'lg'

/** The size each variant has in the design when none is asked for. */
const DEFAULT_SIZE: Record<ButtonVariant, ButtonSize> = {
  primary: 'md',
  secondary: 'md',
  outline: 'sm',
  danger: 'sm',
  'danger-soft': 'md',
  'on-wash': 'sm',
  ghost: 'md',
  link: 'md',
  add: 'md',
}

const HEIGHT: Record<ButtonSize, string> = { sm: 'h-8', md: 'h-10', lg: 'h-12' }

function variantClass(v: ButtonVariant, size: ButtonSize): string {
  switch (v) {
    case 'primary':
      return cx(
        'bg-brand text-white font-bold hover:bg-brand-ink',
        size === 'sm' && 'h-9 px-4 rounded-card-lg text-base',
        size === 'md' && 'h-10 px-4.5 rounded-button text-md',
        size === 'lg' && 'h-12 px-5 rounded-card text-lg',
      )
    case 'secondary':
      return cx(
        HEIGHT[size],
        'px-3.5 rounded-control border border-line-control bg-surface text-base font-semibold hover:bg-canvas',
      )
    case 'outline':
      return cx(
        HEIGHT[size],
        'px-3.5 rounded-button border border-line-strong bg-surface text-base hover:bg-canvas',
      )
    case 'danger':
      return cx(
        HEIGHT[size],
        'px-3.5 rounded-button border border-alert bg-surface text-alert font-bold hover:bg-alert-wash',
      )
    case 'danger-soft':
      return cx(HEIGHT[size], 'px-3.5 rounded-control bg-bad-wash text-bad-ink font-bold')
    case 'on-wash':
      return cx(HEIGHT[size], 'px-3.5 rounded-control bg-surface text-ink font-bold hover:bg-canvas')
    case 'ghost':
      return cx(HEIGHT[size], 'px-3 rounded-control text-ink-2 hover:bg-canvas')
    case 'link':
      // Design: ink-3. Raised to ink-2: 13px ink-3 fails contrast (§1.2).
      return 'text-sm text-ink-2 underline underline-offset-2 hover:text-ink'
    case 'add':
      return cx(
        HEIGHT[size],
        'px-3.5 rounded-control border-[1.5px] border-dashed border-line-strong bg-surface text-base font-semibold text-brand-ink hover:bg-canvas',
      )
  }
}

export interface ButtonProps extends ButtonHTMLAttributes<HTMLButtonElement> {
  variant?: ButtonVariant
  size?: ButtonSize
  /** Full width. */
  block?: boolean
  /** A write is in flight: disabled, label swapped, width kept. */
  pending?: boolean
  /** The label while pending, a verb in progress: "Applying…". */
  pendingLabel?: ReactNode
}

export const Button = forwardRef<HTMLButtonElement, ButtonProps>(function Button(
  {
    variant = 'secondary',
    size,
    block = false,
    pending = false,
    pendingLabel,
    disabled,
    className,
    children,
    type = 'button',
    ...rest
  },
  ref,
) {
  const s = size ?? DEFAULT_SIZE[variant]
  return (
    <button
      ref={ref}
      type={type}
      disabled={disabled || pending}
      aria-busy={pending || undefined}
      className={cx(
        'inline-flex items-center justify-center gap-1.5 whitespace-nowrap select-none',
        'transition-[background-color,border-color,color] disabled:opacity-50',
        variantClass(variant, s),
        block && 'w-full',
        className,
      )}
      {...rest}
    >
      {pendingLabel === undefined ? (
        children
      ) : (
        // Both labels share one grid cell, so the button is as wide as the
        // wider of the two and does not jump when the label swaps.
        <span className="grid">
          <span className={cx('[grid-area:1/1]', pending && 'invisible')}>{children}</span>
          <span className={cx('[grid-area:1/1]', !pending && 'invisible')} aria-hidden={!pending}>
            {pendingLabel}
          </span>
        </span>
      )}
    </button>
  )
})

export interface IconButtonProps extends ButtonHTMLAttributes<HTMLButtonElement> {
  /** Required: the accessible name, since the glyph is aria-hidden. */
  label: string
  /** `plain` is the bare ×; `wash` is the drawer close button. */
  tone?: 'plain' | 'wash'
  /** 36px (design) or 40px (touch targets: banner ×, hamburger). */
  size?: 36 | 40
}

export const IconButton = forwardRef<HTMLButtonElement, IconButtonProps>(function IconButton(
  { label, tone = 'plain', size = 36, className, children, type = 'button', ...rest },
  ref,
) {
  return (
    <button
      ref={ref}
      type={type}
      aria-label={label}
      title={label}
      className={cx(
        'relative grid flex-none place-items-center rounded-control text-lg leading-none transition-colors disabled:opacity-50',
        size === 36 ? 'size-9' : 'size-10',
        tone === 'wash' ? 'bg-wash text-ink-2 hover:bg-line' : 'text-ink-3 hover:bg-wash hover:text-ink-2',
        className,
      )}
      {...rest}
    >
      {children ?? <span aria-hidden="true">×</span>}
    </button>
  )
})

/**
 * Destructive action with no preview: the first press arms it ("Tap again to
 * …") for 4 seconds, the second press fires. design-system.md §5.1.
 */
export function ConfirmTwiceButton({
  children,
  armedLabel,
  onConfirm,
  variant = 'danger',
  size,
  pending,
  pendingLabel,
  disabled,
  className,
}: {
  children: ReactNode
  /** "Tap again to delete". */
  armedLabel: ReactNode
  onConfirm: () => void
  variant?: ButtonVariant
  size?: ButtonSize
  pending?: boolean
  pendingLabel?: ReactNode
  disabled?: boolean
  className?: string
}) {
  const [armed, setArmed] = useState(false)
  const timer = useRef<number | null>(null)
  useEffect(
    () => () => {
      if (timer.current !== null) window.clearTimeout(timer.current)
    },
    [],
  )
  return (
    <Button
      variant={variant}
      size={size}
      pending={pending}
      pendingLabel={pendingLabel}
      disabled={disabled}
      className={className}
      aria-live="polite"
      onClick={() => {
        if (!armed) {
          setArmed(true)
          timer.current = window.setTimeout(() => setArmed(false), 4000)
          return
        }
        if (timer.current !== null) window.clearTimeout(timer.current)
        setArmed(false)
        onConfirm()
      }}
    >
      {armed ? armedLabel : children}
    </Button>
  )
}
