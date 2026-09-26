/**
 * Banner and BannerStack: design-system.md §5.9, shell-agents.md §3.
 *
 * Two tones only: `stale` (neutral wash, grey dot) and `alert` (alert wash,
 * red dot). Below 640px the text takes line 1 and the action + × wrap to line
 * 2, right-aligned.
 */
import type { ReactNode } from 'react'
import { Button } from './button'
import { IconButton } from './button'
import { Dot } from './badges'
import { cx } from './cx'

export type BannerTone = 'stale' | 'alert'

export function Banner({
  tone,
  children,
  action,
  onDismiss,
}: {
  tone: BannerTone
  children: ReactNode
  action?: { label: string; onClick: () => void; pending?: boolean; pendingLabel?: string }
  /** Omit for a permanent strip (fixture mode). */
  onDismiss?: () => void
}) {
  return (
    <div
      role={tone === 'alert' ? 'alert' : 'status'}
      className={cx(
        'mx-4 mt-2.5 flex flex-none flex-wrap items-center gap-x-3 gap-y-1 rounded-card py-2 pl-4 pr-2 text-base',
        tone === 'alert' ? 'bg-alert-wash' : 'bg-wash',
      )}
    >
      <div className="flex min-w-0 flex-[1_1_100%] items-center gap-3 py-0.5 sm:flex-[1_1_0%]">
        <Dot tone={tone === 'alert' ? 'alert' : 'muted'} />
        <span className="min-w-0">{children}</span>
      </div>
      {(action || onDismiss) && (
        <div className="ml-auto flex flex-none items-center gap-1">
          {action && (
            <Button
              variant="on-wash"
              onClick={action.onClick}
              pending={action.pending}
              pendingLabel={action.pendingLabel}
            >
              {action.label}
            </Button>
          )}
          {onDismiss && <IconButton label="Dismiss" size={40} onClick={onDismiss} />}
        </div>
      )}
    </div>
  )
}

/** Stacks banners above the page, in the order given. Renders nothing when empty. */
export function BannerStack({ children }: { children: ReactNode }) {
  return <div className="flex flex-none flex-col">{children}</div>
}
