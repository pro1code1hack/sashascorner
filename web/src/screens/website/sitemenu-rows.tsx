/**
 * Up / down move buttons, shared by the Order online screens (Categories drawer,
 * Option groups, Banners & upsells).
 *
 * This file used to hold the Website › Website menu rows. That screen is gone
 * (DECISIONS.md 29, "one menu everywhere", 2026-09-29): the public website's
 * menu page reads the shop catalogue, which is edited on Menu items. Only the
 * move buttons were still imported, so only they remain.
 */

/* ---------------------------------------------------------- move buttons --- */

export interface Mover {
  /** False while filtering, or while the order is being saved. */
  enabled: boolean
  index: number
  count: number
  move: (from: number, to: number) => void
}

/** Up / down, keyboard-operable; ids let the screen put focus back after a move. */
export function MoveButtons({ name, mover, idBase }: { name: string; mover: Mover; idBase: string }) {
  const btn =
    'grid size-11 flex-none place-items-center rounded-control border border-line-control bg-surface text-md text-ink-2 outline-none hover:bg-canvas focus-visible:edge-brand disabled:opacity-50 sm:size-9'
  return (
    <span className="flex flex-none gap-1">
      <button
        type="button"
        id={`${idBase}-up`}
        className={btn}
        aria-label={`Move ${name} up`}
        title="Move up"
        disabled={!mover.enabled || mover.index === 0}
        onClick={() => mover.move(mover.index, mover.index - 1)}
      >
        <span aria-hidden="true">↑</span>
      </button>
      <button
        type="button"
        id={`${idBase}-down`}
        className={btn}
        aria-label={`Move ${name} down`}
        title="Move down"
        disabled={!mover.enabled || mover.index >= mover.count - 1}
        onClick={() => mover.move(mover.index, mover.index + 1)}
      >
        <span aria-hidden="true">↓</span>
      </button>
    </span>
  )
}

/** A stable DOM id for a row's move buttons (names may hold any character). */
export function domId(prefix: string, key: string): string {
  let h = 0
  for (let i = 0; i < key.length; i++) h = (Math.imul(31, h) + key.charCodeAt(i)) | 0
  return `${prefix}-${(h >>> 0).toString(36)}`
}
