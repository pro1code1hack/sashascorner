/**
 * PageHeader, SectionHead, Toolbar, PageBody: design-system.md §5.10.
 *
 * Layout contract with the shell: the shell gives each screen a flex column
 * that fills the main area (`min-h-0 flex-1`). A screen renders
 * `<PageHeader/>` (flex-none), optional `<Toolbar/>` (flex-none), then one
 * `<PageBody/>` that owns the scroll. Two-pane screens put their panes in a
 * `flex min-h-0 flex-1` row instead of PageBody, each pane scrolling itself.
 */
import type { ReactNode } from 'react'
import { cx } from './cx'

export function PageHeader({
  title,
  subtitle,
  saved,
  actions,
}: {
  title: ReactNode
  /** A sentence about what the page is. Keep the design's voice. */
  subtitle?: ReactNode
  /** "Saved 2 min ago" style status. */
  saved?: ReactNode
  /** Primary action(s), right-aligned. */
  actions?: ReactNode
}) {
  return (
    <header className="flex flex-none flex-wrap items-center gap-x-4.5 gap-y-2 border-b border-line px-5 py-3">
      <div className="flex min-w-0 flex-[1_1_16rem] flex-wrap items-baseline gap-x-4.5 gap-y-0.5">
        <h1 className="text-2xl font-extrabold tracking-[-.01em]">{title}</h1>
        {subtitle && <div className="min-w-0 text-base text-ink-2">{subtitle}</div>}
      </div>
      {(saved || actions) && (
        <div className="flex flex-none items-center gap-3">
          {saved && <div className="text-base text-ink-2">{saved}</div>}
          {actions}
        </div>
      )}
    </header>
  )
}

/** Section heading inside a page or drawer: 16px 800. `size="panel"` is 18px. */
export function SectionHead({
  children,
  right,
  size = 'section',
  as: As = 'h2',
  className,
}: {
  children: ReactNode
  right?: ReactNode
  size?: 'section' | 'panel'
  as?: 'h2' | 'h3'
  className?: string
}) {
  return (
    <div className={cx('flex items-center justify-between gap-3', className)}>
      <As className={cx('font-extrabold tracking-[-.01em]', size === 'panel' ? 'text-xl' : 'text-lg')}>
        {children}
      </As>
      {right}
    </div>
  )
}

/** Filter row under the header. */
export function Toolbar({ children, className }: { children: ReactNode; className?: string }) {
  return (
    <div
      className={cx(
        'flex flex-none flex-wrap items-center gap-2.5 border-b border-line-soft px-5 py-3',
        className,
      )}
    >
      {children}
    </div>
  )
}

/** Meta strip under the toolbar ("as of …"), canvas-2. */
export function MetaStrip({ children }: { children: ReactNode }) {
  return <div className="flex-none bg-canvas-2 px-5 py-1.5 text-sm text-ink-2">{children}</div>
}

/** The scrolling content area of a page. */
export function PageBody({
  children,
  className,
  flush = false,
}: {
  children: ReactNode
  className?: string
  /** No padding (full-bleed tables that pad their own rows). */
  flush?: boolean
}) {
  return (
    <div className={cx('min-h-0 min-w-0 flex-1 overflow-y-auto', !flush && 'px-4 py-4 sm:px-5 compact:px-6', className)}>
      {children}
    </div>
  )
}

/**
 * The figures strip: a <dl> of big numbers with hairline dividers, never a card grid
 * (CLAUDE.md §10). Used by Sales, Transactions, Expenses, shop Insights and item sales.
 */
export function Figures({ items, className }: { items: ReadonlyArray<{ label: string; value: ReactNode; sub?: ReactNode; strong?: boolean }>; className?: string }) {
  return (
    <dl className={cx('flex flex-wrap gap-y-3 border-b border-line py-3', className)}>
      {items.map((it, i) => (
        <div
          key={it.label}
          className={cx('min-w-[96px] pr-5', i > 0 && 'border-l border-line pl-5', 'max-sm:border-0! max-sm:pl-0! max-sm:w-1/2')}
        >
          <dt className="text-xs font-bold uppercase tracking-[.06em] text-ink-3">{it.label}</dt>
          <dd className={cx('fig whitespace-nowrap tracking-[-.01em]', it.strong ? 'text-2xl font-extrabold' : 'text-xl font-bold')}>
            {it.value}
          </dd>
          {it.sub && <dd className="text-sm text-ink-2">{it.sub}</dd>}
        </div>
      ))}
    </dl>
  )
}

