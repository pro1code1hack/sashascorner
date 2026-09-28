/**
 * The owner's unsaved slot edits, kept outside React Query so a background
 * refetch (or a hop to another screen and back) never throws them away.
 *
 * A slot has a draft only once somebody changed it; until then it shows the
 * saved state straight from the server. Dirty = draft differs from saved.
 */
import { useSyncExternalStore } from 'react'
import type { DraftItem } from './model'

export interface SlotStatus {
  text: string
  tone: 'ok' | 'bad' | 'busy'
}

interface State {
  drafts: ReadonlyMap<string, DraftItem[]>
  status: ReadonlyMap<string, SlotStatus>
}

let state: State = { drafts: new Map(), status: new Map() }
const subs = new Set<() => void>()

function set(next: Partial<State>) {
  state = { ...state, ...next }
  subs.forEach((f) => f())
}

export function useDraftState(): State {
  return useSyncExternalStore(
    (cb) => {
      subs.add(cb)
      return () => subs.delete(cb)
    },
    () => state,
  )
}

export function setDraft(key: string, items: DraftItem[]) {
  const drafts = new Map(state.drafts)
  drafts.set(key, items)
  const status = new Map(state.status)
  status.delete(key)
  set({ drafts, status })
}

export function dropDraft(key: string) {
  const drafts = new Map(state.drafts)
  drafts.delete(key)
  set({ drafts })
}

export function setStatus(key: string, s: SlotStatus | null) {
  const status = new Map(state.status)
  if (s) status.set(key, s)
  else status.delete(key)
  set({ status })
}

/** A deleted photo leaves every draft, so a later save cannot re-add it. */
export function dropMediaFromDrafts(id: number) {
  const drafts = new Map<string, DraftItem[]>()
  for (const [k, d] of state.drafts) drafts.set(k, d.filter((i) => i.media_id !== id))
  set({ drafts })
}
