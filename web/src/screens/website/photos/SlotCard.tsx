/**
 * One place on the site that shows a photo (or a gallery of them): its shape,
 * whether it has a photo, the photos in order, the focal editor and Save.
 */
import { useRef, useState } from 'react'
import type { KeyboardEvent, PointerEvent as RPointerEvent } from 'react'
import { Button, IconButton, cx } from '../../../components/ui'
import { usePhotos } from './context'
import { FocalEditor } from './FocalEditor'
import { Img } from './Img'
import { aspectLabel, nameOf, pct, picFor, slotAnchor, type DraftItem, type Slot } from './model'
import { ACCEPT, UploadList, useUploader } from './upload'

export function SlotCard({ slot }: { slot: Slot }) {
  const p = usePhotos()
  const items = p.draft(slot.key)
  const dirty = p.isDirty(slot.key)
  const st = p.status(slot.key)
  const busy = st?.tone === 'busy'
  const up = useUploader(p.announce)
  const file = useRef<HTMLInputElement>(null)
  const anchor = slotAnchor(slot.key)

  const cap = slot.multiple ? slot.max : 1
  const room = cap - items.length
  const fill = slot.multiple ? `${items.length} of ${slot.max}` : items.length ? 'Has a photo' : 'Empty'

  const uploadInto = async (files: File[]) => {
    const done = await up.upload(files.slice(0, Math.max(1, slot.multiple ? room : 1)))
    if (done.length) p.place(slot.key, done.map((m) => m.id))
  }

  const statusText = busy ? st.text : dirty ? 'Not saved yet' : (st?.text ?? '')

  return (
    <article
      id={anchor}
      tabIndex={-1}
      aria-labelledby={`${anchor}-t`}
      className={cx(
        'flex min-w-0 flex-col gap-4 rounded-card border bg-surface px-4 py-4 outline-none focus-visible:ring-2 focus-visible:ring-brand',
        dirty ? 'border-alert' : 'border-line',
      )}
    >
      <header className="flex flex-wrap items-start justify-between gap-x-4 gap-y-2">
        <div className="min-w-0 flex-[1_1_16rem]">
          <h3 id={`${anchor}-t`} className="text-md font-extrabold">
            {slot.label}
          </h3>
          {slot.hint && <p className="text-base text-ink-2">{slot.hint}</p>}
        </div>
        <p className="flex flex-none items-center gap-2.5 text-sm text-ink-2">
          <span
            aria-hidden="true"
            className="inline-block h-4 rounded-[3px] border-[1.5px] border-ink-3"
            style={{ aspectRatio: slot.aspect }}
          />
          <span className="fig">Shape {aspectLabel(slot.aspect)}</span>
          <span className={cx('fig font-bold', items.length ? 'text-ink' : 'text-ink-2')}>{fill}</span>
        </p>
      </header>

      {slot.multiple && <Strip slot={slot} items={items} />}
      {!items.length && <EmptyFrame slot={slot} />}

      <div className="flex flex-wrap items-center gap-2">
        {room > 0 || !slot.multiple ? (
          <>
            <span className="text-base text-ink-2">
              {slot.multiple
                ? items.length
                  ? `Add up to ${room} more:`
                  : `Add up to ${room}:`
                : items.length
                  ? 'Replace:'
                  : 'Add a photo:'}
            </span>
            <Button className="max-compact:h-11" onClick={() => p.pick(slot.key)} disabled={up.busy}>
              Choose from library
            </Button>
            <Button className="max-compact:h-11" onClick={() => file.current?.click()} pending={up.busy} pendingLabel="Uploading…">
              Upload new
            </Button>
            <input
              ref={file}
              type="file"
              accept={ACCEPT}
              multiple={slot.multiple && room > 1}
              className="sr-only"
              tabIndex={-1}
              aria-hidden="true"
              onChange={(e) => {
                const files = [...(e.target.files ?? [])]
                e.target.value = ''
                if (files.length) void uploadInto(files)
              }}
            />
          </>
        ) : (
          <p className="text-base text-ink-2">Full: this place shows up to {slot.max}. Remove one to add another.</p>
        )}
        {!slot.multiple && items.length > 0 && (
          <Button
            variant="ghost"
            className="max-compact:h-11 compact:ml-auto"
            onClick={() => {
              p.change(slot.key, [])
              p.announce('Photo taken out of this place. Save to confirm.')
            }}
          >
            Take this photo out
          </Button>
        )}
      </div>
      <UploadList rows={up.rows} label={`Uploads for ${slot.label}`} />

      {items.length > 0 && (
        <div className="flex flex-col gap-2 border-t border-line-soft pt-4">
          {slot.multiple && (
            <p className="fig text-sm font-bold text-ink-2">
              Editing photo {Math.min(p.selected(slot.key), items.length - 1) + 1} of {items.length}
            </p>
          )}
          <FocalEditor slot={slot} idx={slot.multiple ? Math.min(p.selected(slot.key), items.length - 1) : 0} />
        </div>
      )}

      <footer className="flex flex-wrap items-center gap-x-3 gap-y-1 border-t border-line-soft pt-3">
        <Button
          variant="outline"
          size="md"
          className="max-compact:h-11"
          disabled={!dirty}
          pending={busy}
          pendingLabel="Saving…"
          onClick={() => void p.save(slot.key)}
        >
          Save
        </Button>
        {dirty && !busy && (
          <Button variant="ghost" className="max-compact:h-11" onClick={() => p.revert(slot.key)}>
            Undo changes
          </Button>
        )}
        <p
          role="status"
          className={cx(
            'min-w-0 flex-1 text-base',
            st?.tone === 'bad' ? 'text-bad-ink' : dirty && !busy ? 'font-semibold text-warn-ink' : 'text-ink-2',
          )}
        >
          {st?.tone === 'bad' ? st.text : statusText}
        </p>
      </footer>
    </article>
  )
}

function EmptyFrame({ slot }: { slot: Slot }) {
  return (
    <div
      className="grid w-[min(100%,420px)] place-items-center rounded-control border-[1.5px] border-dashed border-line-strong bg-canvas px-4 text-center"
      style={{ aspectRatio: slot.aspect }}
    >
      <p className="flex flex-col gap-1 text-base">
        <strong>No photo yet</strong>
        {slot.hint && <span className="text-sm text-ink-2">{slot.hint}</span>}
      </p>
    </div>
  )
}

/* -------------------------------------------------- gallery strip --- */

function Strip({ slot, items }: { slot: Slot; items: DraftItem[] }) {
  const p = usePhotos()
  const list = useRef<HTMLOListElement>(null)
  const [drag, setDrag] = useState<{ from: number; over: number } | null>(null)
  const sel = Math.min(p.selected(slot.key), Math.max(0, items.length - 1))

  const focus = (selector: string) => {
    // After React re-renders the strip.
    window.requestAnimationFrame(() => list.current?.querySelector<HTMLElement>(selector)?.focus())
  }

  const move = (from: number, to: number, keep: 'left' | 'right' | 'grip') => {
    if (to < 0 || to >= items.length || from === to) return
    const next = [...items]
    const [it] = next.splice(from, 1)
    if (!it) return
    next.splice(to, 0, it)
    p.select(slot.key, sel === from ? to : sel)
    p.change(slot.key, next)
    // Keep focus on the same control, or its twin when that one is now disabled.
    const want = keep === 'left' && to === 0 ? 'right' : keep === 'right' && to === next.length - 1 ? 'left' : keep
    focus(`[data-f="${want}-${to}"]`)
    p.announce(`Moved to position ${to + 1} of ${next.length}. Save to confirm.`)
  }

  const remove = (i: number) => {
    const next = items.filter((_, j) => j !== i)
    if (sel >= next.length) p.select(slot.key, Math.max(0, next.length - 1))
    p.change(slot.key, next)
    if (next.length) focus(`[data-f="remove-${Math.min(i, next.length - 1)}"]`)
    p.announce(`Photo taken out. ${next.length} left. Save to confirm.`)
  }

  const indexAt = (x: number, y: number): number | null => {
    const el = document.elementFromPoint(x, y)?.closest<HTMLElement>('[data-idx]')
    if (!el || !list.current?.contains(el)) return null
    const n = Number(el.dataset.idx)
    return Number.isFinite(n) ? n : null
  }

  const gripDown = (i: number, e: RPointerEvent<HTMLButtonElement>) => {
    if (e.button !== 0) return
    e.preventDefault()
    const x0 = e.clientX
    const y0 = e.clientY
    let started = false
    let over = i
    const onMove = (ev: PointerEvent) => {
      if (!started && Math.hypot(ev.clientX - x0, ev.clientY - y0) < 5) return
      started = true
      const at = indexAt(ev.clientX, ev.clientY)
      if (at !== null) over = at
      setDrag({ from: i, over })
    }
    const onUp = () => {
      window.removeEventListener('pointermove', onMove)
      window.removeEventListener('pointerup', onUp)
      window.removeEventListener('pointercancel', onUp)
      setDrag(null)
      if (started && over !== i) move(i, over, 'grip')
    }
    window.addEventListener('pointermove', onMove)
    window.addEventListener('pointerup', onUp)
    window.addEventListener('pointercancel', onUp)
  }

  const gripKey = (i: number, e: KeyboardEvent<HTMLButtonElement>) => {
    if (e.key === 'ArrowLeft' || e.key === 'ArrowUp') {
      e.preventDefault()
      move(i, i - 1, 'grip')
    } else if (e.key === 'ArrowRight' || e.key === 'ArrowDown') {
      e.preventDefault()
      move(i, i + 1, 'grip')
    }
  }

  return (
    <ol
      ref={list}
      aria-label={`${slot.label}, in the order shown on the site`}
      className="grid grid-cols-[repeat(auto-fill,minmax(150px,1fr))] gap-3"
    >
      {items.map((it, i) => {
        const m = picFor(slot, p.mediaMap, it.media_id)
        const name = nameOf(m, `Photo ${i + 1}`)
        const on = i === sel
        return (
          <li
            key={it.media_id}
            data-idx={i}
            className={cx(
              'flex min-w-0 flex-col gap-1.5 rounded-control p-1.5',
              drag && drag.over === i && drag.from !== i ? 'bg-brand-wash' : '',
              drag && drag.from === i ? 'bg-wash' : '',
            )}
          >
            <button
              type="button"
              aria-pressed={on}
              onClick={() => p.select(slot.key, i)}
              className={cx(
                'relative w-full overflow-hidden rounded-control bg-wash',
                on ? 'shadow-selected' : 'shadow-raised',
              )}
              style={{ aspectRatio: slot.aspect }}
            >
              {m && (
                <Img pic={m} sizes="160px" alt="" style={{ objectPosition: `${pct(it.focal.x)} ${pct(it.focal.y)}` }} />
              )}
              <span className="fig absolute left-1.5 top-1.5 grid size-6 place-items-center rounded-full bg-surface text-xs font-bold text-ink">
                {i + 1}
              </span>
              <span className="sr-only">
                Photo {i + 1}: {name}. {on ? 'Open in the editor below.' : 'Edit its focal point and description.'}
              </span>
            </button>
            <div className="grid grid-cols-2 justify-items-center compact:flex compact:items-center compact:justify-between">
              <IconButton
                label={`Move photo ${i + 1} left`}
                data-f={`left-${i}`}
                disabled={i === 0}
                onClick={() => move(i, i - 1, 'left')}
                className="max-compact:size-11"
              >
                <span aria-hidden="true">←</span>
              </IconButton>
              <IconButton
                label={`Drag to reorder photo ${i + 1}, or use the arrow keys`}
                data-f={`grip-${i}`}
                onPointerDown={(e) => gripDown(i, e)}
                onKeyDown={(e) => gripKey(i, e)}
                className="cursor-grab touch-none max-compact:size-11"
              >
                <span aria-hidden="true">⠿</span>
              </IconButton>
              <IconButton
                label={`Move photo ${i + 1} right`}
                data-f={`right-${i}`}
                disabled={i === items.length - 1}
                onClick={() => move(i, i + 1, 'right')}
                className="max-compact:size-11"
              >
                <span aria-hidden="true">→</span>
              </IconButton>
              <IconButton
                label={`Take photo ${i + 1} out of this place`}
                data-f={`remove-${i}`}
                onClick={() => remove(i)}
                className="max-compact:size-11"
              />
            </div>
          </li>
        )
      })}
      {Array.from({ length: Math.max(0, slot.max - items.length) }, (_, k) => (
        <li key={`hole-${k}`} aria-hidden="true" className="p-1.5">
          <span
            className="fig grid w-full place-items-center rounded-control border-[1.5px] border-dashed border-line-strong text-sm text-ink-2"
            style={{ aspectRatio: slot.aspect }}
          >
            {items.length + k + 1}
          </span>
        </li>
      ))}
    </ol>
  )
}
