/**
 * Hash routing: `#/stock`, `#/money/reconcile`, `#/recipes/proposal/abc?x=1`.
 *
 * Hash rather than history routing because the bundle is served as static
 * files behind Caddy and a reload must land on the same page
 * (shell-agents.md §1: `useState` routing lost the page on reload).
 *
 * The route table itself is in src/routes.tsx. This file only parses and
 * navigates.
 */
import { useSyncExternalStore } from 'react'

export interface Location {
  /** Normalised path without the hash: "/money/reconcile". "/" when empty. */
  path: string
  /** Path segments: ["money", "reconcile"]. */
  segments: string[]
  /** Query after "?" in the hash. */
  query: URLSearchParams
}

function readHash(): string {
  return window.location.hash
}

export function parseHash(hash: string): Location {
  const raw = hash.replace(/^#/, '')
  const [p = '', q = ''] = raw.split('?', 2)
  const segments = p.split('/').filter(Boolean).map(decodeURIComponent)
  return { path: '/' + segments.join('/'), segments, query: new URLSearchParams(q) }
}

function subscribe(cb: () => void): () => void {
  window.addEventListener('hashchange', cb)
  return () => window.removeEventListener('hashchange', cb)
}

/** The current location; re-renders on every hash change. */
export function useLocation(): Location {
  const hash = useSyncExternalStore(subscribe, readHash, () => '')
  // Parsing is cheap and the hash string is the stable snapshot.
  return parseHash(hash)
}

/** `href("/money/reconcile")` -> "#/money/reconcile". */
export function href(path: string, query?: Record<string, string | number | undefined>): string {
  const clean = '/' + path.replace(/^#?\/*/, '')
  const qs = query
    ? new URLSearchParams(
        Object.entries(query)
          .filter((e): e is [string, string | number] => e[1] !== undefined)
          .map(([k, v]) => [k, String(v)]),
      ).toString()
    : ''
  return `#${clean}${qs ? `?${qs}` : ''}`
}

/** Go somewhere. `replace` swaps the history entry (use for redirects). */
export function navigate(path: string, opts: { replace?: boolean; query?: Record<string, string | number | undefined> } = {}): void {
  const target = href(path, opts.query)
  if (opts.replace) {
    const url = `${window.location.pathname}${window.location.search}${target}`
    window.history.replaceState(null, '', url)
    window.dispatchEvent(new HashChangeEvent('hashchange'))
  } else {
    window.location.hash = target
  }
}
