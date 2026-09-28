/**
 * Focal point + per-place description for one photo in one slot. The owner taps
 * (or drags, or uses the arrow keys) on the most important part of the photo;
 * the two crop previews beside it show what the page and a narrow phone keep.
 */
import { useId, useRef } from 'react'
import type { KeyboardEvent, PointerEvent } from 'react'
import { Button, Field, Input } from '../../../components/ui'
import { usePhotos } from './context'
import { Img } from './Img'
import { aspectLabel, clamp01, focalWords, pct, phoneAspect, picFor, type Focal, type Slot } from './model'

export function FocalEditor({ slot, idx }: { slot: Slot; idx: number }) {
  const p = usePhotos()
  const items = p.draft(slot.key)
  const it = items[idx]
  const helpId = useId()
  const pad = useRef<HTMLDivElement>(null)
  const dragging = useRef(false)
  if (!it) return null
  const m = picFor(slot, p.mediaMap, it.media_id)
  if (!m) return <p className="text-base text-ink-2">This photo is no longer in the library.</p>
  const libAlt = p.mediaMap.get(it.media_id)?.alt ?? ''

  const r = m.width && m.height ? m.width / m.height : 4 / 3
  const pos = `${pct(it.focal.x)} ${pct(it.focal.y)}`
  const phone = phoneAspect(slot.aspect)

  const set = (f: Focal, say = false) => {
    const cur = p.draft(slot.key)
    const next = [...cur]
    const at = next[idx]
    if (!at) return
    const focal = { x: clamp01(f.x), y: clamp01(f.y) }
    next[idx] = { ...at, focal }
    p.change(slot.key, next)
    if (say) p.announce(`Focal point ${focalWords(focal)}.`)
  }

  const fromPointer = (e: PointerEvent<HTMLDivElement>) => {
    const box = e.currentTarget.getBoundingClientRect()
    set({ x: (e.clientX - box.left) / box.width, y: (e.clientY - box.top) / box.height })
  }

  const onKey = (e: KeyboardEvent<HTMLDivElement>) => {
    const step = e.shiftKey ? 0.1 : 0.02
    const d: Record<string, [number, number]> = {
      ArrowLeft: [-step, 0],
      ArrowRight: [step, 0],
      ArrowUp: [0, -step],
      ArrowDown: [0, step],
    }
    const mv = d[e.key]
    if (mv) {
      e.preventDefault()
      set({ x: it.focal.x + mv[0], y: it.focal.y + mv[1] }, true)
    } else if (e.key === 'Home' || e.key === 'c' || e.key === 'C') {
      e.preventDefault()
      set({ x: 0.5, y: 0.5 }, true)
    }
  }

  return (
    <div className="flex flex-col gap-4">
      <div className="flex flex-wrap items-start gap-5">
        <div className="flex min-w-0 max-w-full flex-[1_1_260px] flex-col gap-2">
          <div className="text-md font-bold">Tap the most important part of the photo</div>
          <div
            ref={pad}
            tabIndex={0}
            role="application"
            aria-roledescription="focal point picker"
            aria-label={`Focal point for ${slot.label}: ${focalWords(it.focal)}`}
            aria-describedby={helpId}
            onKeyDown={onKey}
            onPointerDown={(e) => {
              if (e.button !== 0) return
              e.preventDefault()
              e.currentTarget.focus()
              e.currentTarget.setPointerCapture(e.pointerId)
              dragging.current = true
              fromPointer(e)
            }}
            onPointerMove={(e) => {
              if (dragging.current) fromPointer(e)
            }}
            onPointerUp={() => {
              if (!dragging.current) return
              dragging.current = false
              p.announce(`Focal point ${focalWords(p.draft(slot.key)[idx]?.focal ?? it.focal)}. Save to confirm.`)
            }}
            onPointerCancel={() => {
              dragging.current = false
            }}
            className="relative max-w-full cursor-crosshair touch-none select-none overflow-hidden rounded-control bg-wash outline-none focus-visible:ring-2 focus-visible:ring-brand focus-visible:ring-offset-2"
            style={{ aspectRatio: `${m.width || 4}/${m.height || 3}`, width: `min(100%, ${Math.round(420 * r)}px)` }}
          >
            <Img pic={m} sizes="(max-width: 700px) 90vw, 420px" alt="" />
            <Crosshair focal={it.focal} />
          </div>
          <div className="flex flex-wrap items-center gap-x-3 gap-y-1">
            <output className="fig text-base font-semibold" aria-hidden="true">
              {Math.round(it.focal.x * 100)}% across · {Math.round(it.focal.y * 100)}% down
            </output>
            <Button variant="ghost" size="sm" className="min-h-11 compact:min-h-0" onClick={() => set({ x: 0.5, y: 0.5 }, true)}>
              Centre it
            </Button>
          </div>
          <p id={helpId} className="text-sm text-ink-2">
            The crop keeps this spot in view. With a keyboard: arrow keys move it, Shift moves further, C centres it.
          </p>
        </div>

        <div className="flex min-w-0 flex-[1_1_220px] flex-wrap items-start gap-4">
          <figure className="flex w-[min(100%,240px)] flex-col gap-1.5">
            <div className="overflow-hidden rounded-control bg-wash" style={{ aspectRatio: slot.aspect }}>
              <Img pic={m} sizes="240px" alt="" style={{ objectPosition: pos }} />
            </div>
            <figcaption className="text-sm text-ink-2">On the page ({aspectLabel(slot.aspect)})</figcaption>
          </figure>
          <figure className="flex w-[min(100%,120px)] flex-col gap-1.5">
            <div className="overflow-hidden rounded-control bg-wash" style={{ aspectRatio: phone }}>
              <Img pic={m} sizes="120px" alt="" style={{ objectPosition: pos }} />
            </div>
            <figcaption className="text-sm text-ink-2">Narrow phone crop ({aspectLabel(phone)})</figcaption>
          </figure>
        </div>
      </div>

      <Field
        label="Description for this place (optional)"
        hint={
          !libAlt && !it.alt
            ? 'This photo has no description yet. Add one here, or in the library, for people using screen readers.'
            : 'Leave empty to use the description from the library.'
        }
      >
        <Input
          type="text"
          maxLength={300}
          autoComplete="off"
          value={it.alt}
          placeholder={libAlt ? `Uses the photo’s description: “${libAlt}”` : 'Describe the photo as it appears here'}
          onChange={(e) => {
            const next = [...p.draft(slot.key)]
            const at = next[idx]
            if (!at) return
            next[idx] = { ...at, alt: e.target.value }
            p.change(slot.key, next)
          }}
        />
      </Field>
    </div>
  )
}

/** Two hairlines and a ring, drawn in currentColor on a surface halo so it reads on any photo. */
function Crosshair({ focal }: { focal: Focal }) {
  return (
    <span
      aria-hidden="true"
      className="pointer-events-none absolute text-surface"
      style={{ left: pct(focal.x), top: pct(focal.y) }}
    >
      <span className="absolute -left-5 -top-px h-0.5 w-10 bg-current shadow-[0_0_0_1px_var(--color-ink)]" />
      <span className="absolute -left-px -top-5 h-10 w-0.5 bg-current shadow-[0_0_0_1px_var(--color-ink)]" />
      <span className="absolute -left-3.5 -top-3.5 size-7 rounded-full border-2 border-current shadow-[0_0_0_1px_var(--color-ink),inset_0_0_0_1px_var(--color-ink)]" />
    </span>
  )
}
