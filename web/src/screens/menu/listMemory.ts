/**
 * The Menu list's last filters, so "‹ Menu" on an item page goes back to the
 * list as it was left (category, sort, page). Module memory for the tab only:
 * a reload starts from the URL like everything else.
 */
let last: Record<string, string> = {}

export function rememberMenuListQuery(q: URLSearchParams): void {
  last = Object.fromEntries(q.entries())
}

export function lastMenuListQuery(): Record<string, string> {
  return last
}
