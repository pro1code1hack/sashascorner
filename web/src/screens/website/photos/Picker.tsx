/**
 * Choose photos from the library for one slot. One photo: a tap places it.
 * A gallery: tick several, then "Add N photos".
 */
import { useEffect, useRef, useState } from 'react'
import { Button, Drawer, cx } from '../../../components/ui'
import { usePhotos } from './context'
import { Img } from './Img'
import { goToSlot } from './model'

export function Picker() {
  const p = usePhotos()
  const key = p.pickerKey
  const slot = key ? p.slotMap.get(key) : undefined
  const [chosen, setChosen] = useState<number[]>([])
  const [forKey, setForKey] = useState<string | null>(null)
  // A new pick starts with nothing chosen.
  if (forKey !== key) {
    setForKey(key)
    setChosen([])
  }
  const left = slot && key ? slot.max - p.draft(key).length : 0

  return (
    <Drawer
      open={slot !== undefined}
      onClose={p.closePicker}
      title={slot ? `Choose ${slot.multiple ? 'photos' : 'a photo'} for ${slot.label}` : ''}
      context="Photo library"
      width={520}
      compactWidth={400}
      footer={
        slot?.multiple && key ? (
          <div className="flex w-full flex-wrap items-center gap-3">
            <span className="fig flex-1 text-base text-ink-2" aria-live="polite">
              {chosen.length} of up to {left} chosen
            </span>
            <Button
              variant="primary"
              disabled={chosen.length === 0}
              onClick={() => {
                const ids = [...chosen]
                p.closePicker()
                p.place(key, ids)
                window.requestAnimationFrame(() => goToSlot(key))
              }}
            >
              {chosen.length > 1 ? `Add ${chosen.length} photos` : 'Add photo'}
            </Button>
          </div>
        ) : undefined
      }
    >
      {slot && key && <PickerBody slotKey={key} chosen={chosen} setChosen={setChosen} />}
    </Drawer>
  )
}

function PickerBody({
  slotKey,
  chosen,
  setChosen,
}: {
  slotKey: string
  chosen: number[]
  setChosen: (ids: number[]) => void
}) {
  const p = usePhotos()
  const slot = p.slotMap.get(slotKey)
  const list = useRef<HTMLUListElement>(null)
  useEffect(() => {
    list.current?.querySelector<HTMLButtonElement>('button:not([disabled])')?.focus()
  }, [slotKey])
  if (!slot) return null
  const inSlot = new Set(p.draft(slotKey).map((i) => i.media_id))
  const left = slot.multiple ? slot.max - inSlot.size : 1

  if (!p.media.length) {
    return (
      <p className="text-base text-ink-2">
        Your library is empty. Close this and use “Upload new”, or add photos on the Photo library tab.
      </p>
    )
  }

  return (
    <ul ref={list} aria-label="Photos in the library" className="grid grid-cols-2 gap-3 compact:grid-cols-3">
      {p.media.map((m) => {
        const here = inSlot.has(m.id)
        const on = chosen.includes(m.id)
        const full = slot.multiple && !on && chosen.length >= left
        const caption = here ? 'Already here' : m.alt || m.original_name || `Photo ${m.id}`
        return (
          <li key={m.id} className="min-w-0">
            <button
              type="button"
              aria-pressed={slot.multiple ? on : undefined}
              disabled={here || full}
              onClick={() => {
                if (!slot.multiple) {
                  p.closePicker()
                  p.place(slotKey, [m.id])
                  window.requestAnimationFrame(() => goToSlot(slotKey))
                  return
                }
                setChosen(on ? chosen.filter((x) => x !== m.id) : [...chosen, m.id])
              }}
              className={cx(
                'flex w-full flex-col gap-1.5 rounded-control p-1.5 text-left transition-shadow disabled:cursor-not-allowed',
                on ? 'bg-brand-wash shadow-selected' : 'hover:bg-canvas',
              )}
            >
              <span className={cx('relative block aspect-square overflow-hidden rounded-control bg-wash', (here || full) && 'grayscale')}>
                <Img pic={m} sizes="(max-width: 900px) 45vw, 160px" alt="" />
                {on && (
                  <span className="absolute right-1.5 top-1.5 grid size-6 place-items-center rounded-full bg-brand text-sm font-bold text-surface" aria-hidden="true">
                    ✓
                  </span>
                )}
              </span>
              <span className={cx('line-clamp-2 text-sm', here ? 'font-bold text-ink-2' : 'text-ink')}>
                {caption}
                {on && <span className="sr-only"> (chosen)</span>}
              </span>
            </button>
          </li>
        )
      })}
    </ul>
  )
}
