/**
 * The sticker set: the enabled stickers in scanner order, then the ones switched off.
 * Tap (or Enter/Space) switches one on or off; drag an enabled tile onto another to
 * reorder, or focus it and use ← → (the keyboard path, since dragging is not for
 * everybody). Dragging uses pointer events, not HTML5 drag and drop, so it works with
 * a finger on the iPad as well as a mouse. At least one sticker stays on: a stamp has to be something.
 */
import { useEffect, useRef, useState } from 'react'
import type { KeyboardEvent, PointerEvent } from 'react'
import { cx } from '../../../components/ui'
import type { Sticker, StickerKey } from '../../../lib/types/loyalty'
import { StickerImg } from '../stickers'

export function StickerSet({
  catalogue,
  enabled,
  onChange,
}: {
  catalogue: readonly Sticker[]
  enabled: StickerKey[]
  onChange: (next: StickerKey[]) => void
}) {
  const [dragging, setDragging] = useState<StickerKey | null>(null)
  const [over, setOver] = useState<StickerKey | null>(null)
  const [note, setNote] = useState<string | null>(null)
  /** The tile just moved with the keyboard: it keeps focus across the re-render. */
  const focusKey = useRef<StickerKey | null>(null)
  const refs = useRef(new Map<StickerKey, HTMLButtonElement>())
  /** Where a press started; it becomes a drag once the pointer moves 6px. */
  const press = useRef<{ key: StickerKey; x: number; y: number; id: number } | null>(null)
  /** Set when a drag ended, so the click that follows it does not toggle the tile. */
  const swallowClick = useRef(false)

  useEffect(() => {
    if (focusKey.current) refs.current.get(focusKey.current)?.focus()
    focusKey.current = null
  }, [enabled])

  const byKey = new Map(catalogue.map((s) => [s.key, s]))
  const on = enabled.filter((k) => byKey.has(k))
  const off = catalogue.filter((s) => !on.includes(s.key)).map((s) => s.key)
  const tiles = [...on, ...off]

  const toggle = (k: StickerKey) => {
    if (on.includes(k)) {
      if (on.length === 1) {
        setNote('Keep at least one sticker on: every stamp needs something to show.')
        return
      }
      setNote(null)
      onChange(on.filter((x) => x !== k))
    } else {
      setNote(null)
      onChange([...on, k])
    }
  }

  const move = (k: StickerKey, to: number) => {
    const from = on.indexOf(k)
    if (from < 0 || to < 0 || to >= on.length || to === from) return
    const next = on.filter((x) => x !== k)
    next.splice(to, 0, k)
    onChange(next)
  }

  const tileAt = (x: number, y: number): StickerKey | null => {
    const el = document.elementFromPoint(x, y)?.closest<HTMLElement>('[data-sticker]')
    return (el?.dataset.sticker as StickerKey | undefined) ?? null
  }

  const onPointerDown = (e: PointerEvent<HTMLButtonElement>, k: StickerKey) => {
    swallowClick.current = false
    if (!on.includes(k) || e.button !== 0) return
    press.current = { key: k, x: e.clientX, y: e.clientY, id: e.pointerId }
  }

  const onPointerMove = (e: PointerEvent<HTMLButtonElement>) => {
    const p = press.current
    if (!p || p.id !== e.pointerId) return
    if (!dragging) {
      if (Math.hypot(e.clientX - p.x, e.clientY - p.y) < 6) return
      setDragging(p.key)
      e.currentTarget.setPointerCapture(e.pointerId)
    }
    const t = tileAt(e.clientX, e.clientY)
    setOver(t && on.includes(t) ? t : null)
  }

  const onPointerUp = (e: PointerEvent<HTMLButtonElement>) => {
    const p = press.current
    press.current = null
    if (!p || !dragging) return
    const t = tileAt(e.clientX, e.clientY)
    if (t && on.includes(t)) move(p.key, on.indexOf(t))
    swallowClick.current = true
    setDragging(null)
    setOver(null)
  }

  const cancelDrag = () => {
    press.current = null
    setDragging(null)
    setOver(null)
  }

  const onKey = (e: KeyboardEvent, k: StickerKey) => {
    const i = on.indexOf(k)
    if (i < 0) return
    const d = e.key === 'ArrowLeft' || e.key === 'ArrowUp' ? -1 : e.key === 'ArrowRight' || e.key === 'ArrowDown' ? 1 : 0
    if (d === 0) return
    e.preventDefault()
    move(k, i + d)
    focusKey.current = k
  }

  return (
    <div>
      <ul className="m-0 grid list-none grid-cols-[repeat(auto-fill,minmax(5.5rem,1fr))] gap-2.5 p-0" aria-label="Stickers">
        {tiles.map((k) => {
          const s = byKey.get(k)
          if (!s) return null
          const idx = on.indexOf(k)
          const isOn = idx >= 0
          return (
            <li key={k}>
              <button
                ref={(el) => {
                  if (el) refs.current.set(k, el)
                  else refs.current.delete(k)
                }}
                type="button"
                aria-pressed={isOn}
                aria-label={`${s.name}: ${isOn ? `in the set, number ${idx + 1} of ${on.length}` : 'off'}`}
                data-sticker={k}
                onPointerDown={(e) => onPointerDown(e, k)}
                onPointerMove={onPointerMove}
                onPointerUp={onPointerUp}
                onPointerCancel={cancelDrag}
                onClick={() => {
                  if (swallowClick.current) {
                    swallowClick.current = false
                    return
                  }
                  toggle(k)
                }}
                onKeyDown={(e) => onKey(e, k)}
                className={cx(
                  'flex w-full flex-col items-center gap-1 rounded-card border-[1.5px] px-1.5 pb-2 pt-2.5 transition-colors motion-reduce:transition-none',
                  isOn
                    ? 'border-brand bg-brand-wash hover:bg-[#e3e9fb]'
                    : 'border-dashed border-line-strong bg-surface hover:bg-canvas',
                  isOn && 'cursor-grab touch-none active:cursor-grabbing',
                  dragging === k && 'opacity-50',
                  over === k && dragging !== k && 'shadow-selected',
                )}
              >
                <span className={cx('grid size-12 place-items-center rounded-full bg-surface', isOn ? 'shadow-seg' : 'opacity-60')}>
                  <StickerImg sticker={k} size={32} />
                </span>
                <span className={cx('text-base font-bold', !isOn && 'text-ink-2')}>{s.name}</span>
                <span className="fig text-xs text-ink-3">{isOn ? `in the set · ${idx + 1}` : 'off'}</span>
              </button>
            </li>
          )
        })}
      </ul>
      <p className="mt-2.5 text-xs text-ink-3">
        Keyboard: focus a sticker in the set and press ← or → to move it; Enter switches it on or off.
      </p>
      {note && (
        <p role="status" className="mt-1.5 text-sm text-bad-ink">
          {note}
        </p>
      )}
    </div>
  )
}
