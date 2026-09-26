/**
 * Pagination: "1–50 of 1,925" plus previous/next and a page-size select.
 * Generic; the screen owns `page` and `pageSize` and asks the server for them.
 */
import { cx } from './cx'

function group(n: number): string {
  return String(n).replace(/\B(?=(\d{3})+(?!\d))/g, ',')
}

export function Pagination({
  page,
  pageSize,
  total,
  onPage,
  onPageSize,
  sizes = [25, 50, 100, 200],
  noun = 'rows',
  className,
}: {
  page: number
  pageSize: number
  total: number
  onPage: (p: number) => void
  onPageSize?: (n: number) => void
  sizes?: readonly number[]
  noun?: string
  className?: string
}) {
  const pages = Math.max(1, Math.ceil(total / pageSize))
  const first = total === 0 ? 0 : (page - 1) * pageSize + 1
  const last = Math.min(total, page * pageSize)
  const btn =
    'flex h-8 min-w-8 items-center justify-center rounded-control border border-line-control bg-surface px-2.5 text-base ' +
    'hover:bg-canvas disabled:cursor-default disabled:opacity-40 disabled:hover:bg-surface ' +
    'outline-none focus-visible:edge-brand'
  return (
    <nav aria-label="Pages" className={cx('flex flex-wrap items-center gap-x-3 gap-y-2 text-base', className)}>
      <span className="fig text-ink-2">
        {total === 0 ? `No ${noun}` : `${group(first)}–${group(last)} of ${group(total)} ${noun}`}
      </span>
      <div className="ml-auto flex items-center gap-1.5">
        {onPageSize && (
          <select
            aria-label="Rows per page"
            value={pageSize}
            onChange={(e) => onPageSize(Number(e.target.value))}
            className="mr-1.5 h-8 rounded-control border border-line-control bg-surface px-1.5 text-sm outline-none focus-visible:edge-brand"
          >
            {sizes.map((s) => (
              <option key={s} value={s}>
                {s} / page
              </option>
            ))}
          </select>
        )}
        <button type="button" className={btn} disabled={page <= 1} onClick={() => onPage(1)} aria-label="First page">
          «
        </button>
        <button type="button" className={btn} disabled={page <= 1} onClick={() => onPage(page - 1)} aria-label="Previous page">
          ‹
        </button>
        <span className="fig px-1.5 text-ink-2" aria-live="polite">
          {page} / {pages}
        </span>
        <button type="button" className={btn} disabled={page >= pages} onClick={() => onPage(page + 1)} aria-label="Next page">
          ›
        </button>
        <button type="button" className={btn} disabled={page >= pages} onClick={() => onPage(pages)} aria-label="Last page">
          »
        </button>
      </div>
    </nav>
  )
}
