/**
 * Tables: design-system.md §5.6, on a real <table> for semantics.
 *
 * - Header: uppercase 11/700/.06em ink-3 by default (DECISIONS §2: uppercase
 *   headers as designed). `header="sentence"` gives the 13px ink-2 header the
 *   design uses in Agents, order history and Finance.
 * - Density: `dense` (~32-34px rows, Finance and history) or `comfortable`
 *   (52px rows, Stock).
 * - Numbers: `<Td numeric>` right-aligns with tabular figures. `est` makes it
 *   italic (an estimate). `alert` is a crossed threshold (red figure).
 * - Wide tables scroll inside themselves; the page never scrolls sideways.
 */
import { createContext, useContext } from 'react'
import type { KeyboardEvent, ReactNode, ThHTMLAttributes, TdHTMLAttributes } from 'react'
import { cx } from './cx'

type Density = 'dense' | 'comfortable'
type Header = 'upper' | 'sentence'
const TableCtx = createContext<{ density: Density; header: Header; stickyHeader: boolean }>({
  density: 'dense',
  header: 'upper',
  stickyHeader: false,
})

/** Horizontal scroll inside itself. */
export function ScrollX({ children, className }: { children: ReactNode; className?: string }) {
  return <div className={cx('scroll-x min-w-0 max-w-full', className)}>{children}</div>
}

export function Table({
  children,
  density = 'dense',
  header = 'upper',
  stickyHeader = false,
  minWidth,
  label,
  className,
}: {
  children: ReactNode
  density?: Density
  header?: Header
  /** Header sticks to the top of the nearest scrolling ancestor. */
  stickyHeader?: boolean
  /** Below this width the table scrolls inside itself, e.g. 640. */
  minWidth?: number
  /** Accessible caption (visually hidden). */
  label?: string
  className?: string
}) {
  return (
    <TableCtx.Provider value={{ density, header, stickyHeader }}>
      <ScrollX>
        {/* relative: the sr-only caption is absolutely placed; without a positioned
            table it anchors to <body> and stretches the whole document. */}
        <table
          className={cx('relative w-full border-collapse text-base', className)}
          style={minWidth !== undefined ? { minWidth } : undefined}
        >
          {label && <caption className="sr-only">{label}</caption>}
          {children}
        </table>
      </ScrollX>
    </TableCtx.Provider>
  )
}

export function THead({ children }: { children: ReactNode }) {
  return <thead>{children}</thead>
}

export function TBody({ children }: { children: ReactNode }) {
  return <tbody>{children}</tbody>
}

export interface ThProps extends ThHTMLAttributes<HTMLTableCellElement> {
  numeric?: boolean
  /** Fixed width, e.g. 20 for the remove-× column. */
  width?: number | string
}

export function Th({ numeric, width, className, children, style, ...rest }: ThProps) {
  const { header, density, stickyHeader } = useContext(TableCtx)
  return (
    <th
      scope="col"
      style={{ ...(width !== undefined ? { width } : {}), ...style }}
      className={cx(
        'whitespace-nowrap font-normal',
        numeric ? 'text-right' : 'text-left',
        header === 'upper'
          ? 'text-label font-bold uppercase tracking-[.06em] text-ink-3 py-2.5 border-b border-line-soft'
          : 'text-sm text-ink-2 py-1.5 border-b border-line',
        density === 'comfortable' ? 'px-5 first:pl-5' : 'px-2 first:pl-0 last:pr-0',
        // Positioned either way: a visually-hidden label inside must not escape the
        // table's scroll box and widen the page.
        stickyHeader ? 'sticky top-0 z-[1] bg-surface' : 'relative',
        className,
      )}
      {...rest}
    >
      {children}
    </th>
  )
}

export interface TrProps {
  children: ReactNode
  selected?: boolean
  /** Needs a look: alert wash. */
  flagged?: boolean
  /** Makes the row clickable and keyboard-operable (Enter / Space). */
  onClick?: () => void
  /** Accessible name for a clickable row: "Open Whole milk". */
  label?: string
  className?: string
}

export function Tr({ children, selected, flagged, onClick, label, className }: TrProps) {
  const { density } = useContext(TableCtx)
  const onKey = onClick
    ? (e: KeyboardEvent) => {
        if (e.key === 'Enter' || e.key === ' ') {
          e.preventDefault()
          onClick()
        }
      }
    : undefined
  return (
    <tr
      onClick={onClick}
      onKeyDown={onKey}
      tabIndex={onClick ? 0 : undefined}
      aria-label={label}
      aria-selected={onClick ? Boolean(selected) : undefined}
      className={cx(
        'border-b',
        density === 'comfortable' ? 'border-line-row' : 'border-line',
        onClick && 'cursor-pointer',
        selected ? 'bg-brand-wash' : flagged ? 'bg-alert-wash' : onClick && 'hover:bg-canvas-2',
        onClick && 'focus-visible:outline-offset-[-2px]',
        className,
      )}
    >
      {children}
    </tr>
  )
}

export interface TdProps extends TdHTMLAttributes<HTMLTableCellElement> {
  numeric?: boolean
  /** An estimate: italic. */
  est?: boolean
  /** A crossed threshold: red figure. `strong` adds bold (drift >= 10%). */
  alert?: boolean
  strong?: boolean
  /** Secondary text: 13px ink-2, truncated. */
  secondary?: boolean
  /** The 20px remove-× column. */
  remove?: boolean
}

export function Td({ numeric, est, alert, strong, secondary, remove, className, children, ...rest }: TdProps) {
  const { density } = useContext(TableCtx)
  return (
    <td
      className={cx(
        'align-middle',
        density === 'comfortable' ? 'h-[52px] px-5 py-1.5' : 'px-2 py-[6px] first:pl-0 last:pr-0',
        numeric && 'fig whitespace-nowrap text-right',
        est && 'italic',
        alert && 'text-alert',
        strong && 'font-bold',
        secondary && 'max-w-0 truncate text-sm text-ink-2',
        remove && 'w-5 text-center text-ink-3',
        className,
      )}
      {...rest}
    >
      {children}
    </td>
  )
}

/** The name cell: primary line (600) plus an optional 13px sub-line. */
export function Cell({ top, sub, lead }: { top: ReactNode; sub?: ReactNode; lead?: ReactNode }) {
  return (
    <div className="flex min-w-0 items-center gap-2.5">
      {lead}
      <div className="min-w-0">
        <div className="truncate font-semibold">{top}</div>
        {sub !== undefined && <div className="truncate text-sm text-ink-2">{sub}</div>}
      </div>
    </div>
  )
}

/** Total row. `rule="heavy"` is the P&L 2px ink rule. */
export function TotalRow({ children, rule = 'line' }: { children: ReactNode; rule?: 'line' | 'heavy' }) {
  return (
    <tr
      className={cx(
        'text-md font-bold [&>td]:py-2.5',
        rule === 'heavy' ? 'border-t-2 border-ink' : 'border-t border-line',
      )}
    >
      {children}
    </tr>
  )
}

/** Label/value grid (cost grid, impact summary). Values right-aligned. */
export function KeyValueGrid({
  rows,
  className,
}: {
  rows: ReadonlyArray<{ key: string; label: ReactNode; value: ReactNode; est?: boolean; alert?: boolean }>
  className?: string
}) {
  return (
    <dl className={cx('grid grid-cols-[minmax(0,1fr)_auto] gap-x-2', className)}>
      {rows.map((r) => (
        <div key={r.key} className="contents">
          <dt className="border-b border-line-row py-1.5 text-ink-2">{r.label}</dt>
          <dd
            className={cx(
              'fig border-b border-line-row py-1.5 text-right',
              r.est && 'italic',
              r.alert && 'text-alert',
            )}
          >
            {r.value}
          </dd>
        </div>
      ))}
    </dl>
  )
}

/** The legend every screen with estimates repeats. */
export function EstLegend({ className }: { className?: string }) {
  return (
    <p className={cx('text-sm text-ink-2', className)}>
      Estimates in <em>italic</em>, counts upright.
    </p>
  )
}
