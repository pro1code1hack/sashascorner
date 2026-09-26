/**
 * "Who's using this?": the per-device operator name (DECISIONS §6,
 * stock-orders-suppliers.md C10).
 *
 * Not a login. The app has one shared password; this is the name a person
 * types once on a device so the writes that need a human name (counts,
 * receipts, checklist answers, write-offs, proposal decisions) can carry one:
 * `counted_by`, `received_by`, `responded_by`, `requested_by`, `decided_by`,
 * `actor`. Any write that needs it is disabled until it is set.
 *
 * localStorage, because it belongs to the device rather than the tab, and
 * every access is wrapped: a private window throws rather than returning null.
 * A module-level copy keeps it working for the page even when storage fails.
 */
import { useSyncExternalStore } from 'react'

const STORE = 'cafeops.operator'
const MAX_LEN = 60

let memory: string | null = null
const listeners = new Set<() => void>()

function read(): string | null {
  try {
    const v = localStorage.getItem(STORE)
    if (v !== null) return v
  } catch {
    /* fall through */
  }
  return memory
}

/** Trim and bound a typed name. Returns null for a blank one. */
export function normaliseOperator(raw: string): string | null {
  const v = raw.replace(/\s+/g, ' ').trim().slice(0, MAX_LEN)
  return v === '' ? null : v
}

export function getOperator(): string | null {
  return read()
}

export function setOperator(raw: string | null): void {
  const v = raw === null ? null : normaliseOperator(raw)
  memory = v
  try {
    if (v === null) localStorage.removeItem(STORE)
    else localStorage.setItem(STORE, v)
  } catch {
    /* private mode; the name lasts this page only */
  }
  listeners.forEach((l) => l())
}

function subscribe(l: () => void): () => void {
  listeners.add(l)
  const onStorage = (e: StorageEvent) => {
    if (e.key === STORE) l()
  }
  window.addEventListener('storage', onStorage)
  return () => {
    listeners.delete(l)
    window.removeEventListener('storage', onStorage)
  }
}

/**
 * `const [name, setName] = useOperator()`. `name` is null until set. Every
 * component using it re-renders when it changes, in this tab or another.
 */
export function useOperator(): [string | null, (name: string | null) => void] {
  const name = useSyncExternalStore(subscribe, read, () => null)
  return [name, setOperator]
}
