/**
 * Drawer: design-system.md §5.8. The only overlay-like surface; the design has
 * no modal anywhere.
 *
 * >= 900px: a right-hand panel INSIDE the layout (`flex-none border-l`), so
 * the list beside it stays visible and usable. Place it as the last child of a
 * `flex min-h-0 flex-1` row.
 * < 900px: a full-screen sheet (`fixed inset-0`), header sticky, focus
 * trapped, Esc closes, focus returns to the element that opened it.
 */
import { useId, useRef } from 'react'
import type { ReactNode } from 'react'
import { useIsDocked } from '../../lib/media'
import { IconButton } from './button'
import { cx } from './cx'
import { useFocusTrap } from './focus'

export function Drawer({
  open,
  onClose,
  title,
  context,
  width = 420,
  compactWidth = 380,
  tone = 'surface',
  titleSize = 'md',
  footer,
  children,
}: {
  open: boolean
  onClose: () => void
  /** Heading. `titleSize="lg"` is the stock-detail 22px title. */
  title: ReactNode
  /** Context line above/beside the title, `text-sm text-ink-2`. */
  context?: ReactNode
  /** Width at >= 1280px. Design: 420 menu item, 400 stock detail, 380 cost panel. */
  width?: number
  /** Width at 900–1279px. Design: 380 menu item, 320 cost panel. */
  compactWidth?: number
  /** `canvas` for stock detail and the recipe cost panel. */
  tone?: 'surface' | 'canvas'
  titleSize?: 'md' | 'lg'
  /** Footer actions; buttons inside should be `className="flex-1"`. */
  footer?: ReactNode
  children: ReactNode
}) {
  const docked = useIsDocked()
  const ref = useRef<HTMLElement>(null)
  const titleId = useId()
  // Sheet: trap focus. Docked: Esc still closes, focus is left alone.
  useFocusTrap(ref, open, onClose, { trap: !docked })
  if (!open) return null

  return (
    <aside
      ref={ref}
      role={docked ? 'complementary' : 'dialog'}
      aria-modal={docked ? undefined : true}
      aria-labelledby={titleId}
      tabIndex={-1}
      style={{ ['--dw' as string]: `${width}px`, ['--dwc' as string]: `${compactWidth}px` }}
      className={cx(
        'flex min-h-0 flex-col outline-none',
        tone === 'canvas' ? 'bg-canvas' : 'bg-surface',
        docked
          ? 'w-[var(--dwc)] flex-none border-l border-line-soft wide:w-[var(--dw)]'
          : 'fixed inset-0 z-40',
      )}
    >
      <div className="sticky top-0 z-[1] flex flex-none items-center gap-2 border-b border-line-soft bg-inherit px-4 py-3">
        <div className="min-w-0 flex-1">
          {context && <div className="truncate text-sm text-ink-2">{context}</div>}
          <h2
            id={titleId}
            className={cx(
              'truncate font-extrabold tracking-[-.01em]',
              titleSize === 'lg' ? 'text-2xl' : 'text-lg',
            )}
          >
            {title}
          </h2>
        </div>
        <IconButton label="Close" tone="wash" onClick={onClose} />
      </div>
      <div className="flex min-h-0 flex-1 flex-col gap-4.5 overflow-y-auto px-4.5 pb-6 pt-4">{children}</div>
      {footer && (
        <div className="flex flex-none gap-2 border-t border-line-soft px-4.5 pb-4 pt-3.5">{footer}</div>
      )}
    </aside>
  )
}
