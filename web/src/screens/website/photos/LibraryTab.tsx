/**
 * "Photo library": every uploaded photo, its description, where it is used,
 * and delete (refused while it is on the site, then offered with force).
 */
import { useQueryClient } from '@tanstack/react-query'
import { useEffect, useRef, useState } from 'react'
import type { DragEvent } from 'react'
import { Button, ConfirmTwiceButton, Field, Input, SectionHead, cx } from '../../../components/ui'
import { useInvalidateWebsite } from '../../../lib/website-api'
import { usePhotos } from './context'
import { dropMediaFromDrafts } from './drafts'
import { Img } from './Img'
import { MEDIA_KEY, deleteMedia, kb, patchAlt, usePutMedia, type Media } from './model'
import { ACCEPT, UploadList, useUploader } from './upload'

export function LibraryTab({ onPlace }: { onPlace: (key: string) => void }) {
  const p = usePhotos()
  const up = useUploader(p.announce)
  const input = useRef<HTMLInputElement>(null)
  const [over, setOver] = useState(false)
  const depth = useRef(0)
  const n = p.media.length

  // A photo dropped outside the zone would open in the tab and lose unsaved work.
  useEffect(() => {
    const stop = (e: globalThis.DragEvent) => e.preventDefault()
    window.addEventListener('dragover', stop)
    window.addEventListener('drop', stop)
    return () => {
      window.removeEventListener('dragover', stop)
      window.removeEventListener('drop', stop)
    }
  }, [])

  const onDrop = (e: DragEvent) => {
    e.preventDefault()
    depth.current = 0
    setOver(false)
    const files = [...e.dataTransfer.files]
    if (files.length) void up.upload(files)
  }

  return (
    <div className="flex flex-col gap-5">
      <p className="max-w-[70ch] text-base text-ink-2">
        Every photo you upload lands here first. Give each one a short description: it is read aloud to people who
        can’t see it, and helps Google.
      </p>

      <div
        onDragEnter={(e) => {
          e.preventDefault()
          depth.current++
          setOver(true)
        }}
        onDragOver={(e) => e.preventDefault()}
        onDragLeave={() => {
          depth.current = Math.max(0, depth.current - 1)
          if (depth.current === 0) setOver(false)
        }}
        onDrop={onDrop}
        className={cx(
          'flex flex-col items-center gap-2 rounded-card border-[1.5px] border-dashed px-4 py-6 text-center',
          over ? 'border-brand bg-brand-wash' : 'border-line-strong bg-canvas',
        )}
      >
        <p className="text-lg font-bold">
          <span className="max-compact:hidden">Drag photos here</span>
          <span className="compact:hidden">Add photos</span>
        </p>
        <p className="text-base text-ink-2 max-compact:hidden">or</p>
        <Button variant="primary" onClick={() => input.current?.click()} pending={up.busy} pendingLabel="Uploading…" className="max-compact:h-11">
          Choose photos…
        </Button>
        <p className="text-sm text-ink-2">JPEG, PNG or WebP, up to 15 MB each. Phone photos are fine as they are.</p>
        <input
          ref={input}
          type="file"
          accept={ACCEPT}
          multiple
          className="sr-only"
          tabIndex={-1}
          aria-hidden="true"
          onChange={(e) => {
            const files = [...(e.target.files ?? [])]
            e.target.value = ''
            if (files.length) void up.upload(files)
          }}
        />
      </div>
      <UploadList rows={up.rows} label="Uploads" />

      <SectionHead
        className="border-b border-line pb-2"
        right={
          n > 0 ? (
            <span className="fig text-base text-ink-2">
              {n} {n === 1 ? 'photo' : 'photos'}
            </span>
          ) : undefined
        }
      >
        In the library
      </SectionHead>
      {n === 0 ? (
        <p className="text-base text-ink-2">No photos yet. Add a few above, then place them on the site.</p>
      ) : (
        <ul aria-label="Photos in the library" className="grid grid-cols-1 gap-4 sm:grid-cols-2 wide:grid-cols-3">
          {p.media.map((m) => (
            <MediaCard key={m.id} m={m} onPlace={onPlace} />
          ))}
        </ul>
      )}
    </div>
  )
}

function MediaCard({ m, onPlace }: { m: Media; onPlace: (key: string) => void }) {
  const p = usePhotos()
  const invalidate = useInvalidateWebsite()
  const putMedia = usePutMedia()
  const qc = useQueryClient()
  const [alt, setAlt] = useState(m.alt)
  const [state, setState] = useState<{ tone: 'busy' | 'ok' | 'bad'; text: string } | null>(null)
  const [refusal, setRefusal] = useState<string | null>(null)
  const [deleting, setDeleting] = useState(false)
  const editing = useRef(false)

  // Follow the server's copy unless the owner is typing in this box.
  useEffect(() => {
    if (!editing.current) setAlt(m.alt)
  }, [m.alt])

  // Saved when the owner leaves the box or presses Enter, never mid-sentence.
  const flush = async () => {
    const next = alt.trim()
    if (next === m.alt) return
    setState({ tone: 'busy', text: 'Saving…' })
    const r = await patchAlt(m.id, next)
    if (r.kind === 'ok') {
      putMedia(r.data)
      setState({ tone: 'ok', text: 'Saved' })
      void invalidate()
    } else {
      setState({ tone: 'bad', text: `Couldn’t save: ${r.message}` })
      p.announce(`Couldn’t save the description for ${m.original_name || 'this photo'}.`)
    }
  }

  const remove = async (force: boolean) => {
    setDeleting(true)
    const r = await deleteMedia(m.id, force)
    setDeleting(false)
    if (r.kind === 'ok') {
      dropMediaFromDrafts(m.id)
      qc.setQueryData<Media[]>(MEDIA_KEY, (old) => old?.filter((x) => x.id !== m.id))
      p.announce(`Photo deleted${force ? ' and removed from the site' : ''}.`)
      void invalidate()
      return
    }
    // 409: it is on the site. The site's sentence names the places.
    setRefusal(r.message)
  }

  const meta = [m.original_name, m.width && m.height ? `${m.width} × ${m.height}` : '', kb(m.bytes)].filter(Boolean).join(' · ')
  const name = m.alt || m.original_name || `Photo ${m.id}`
  const inUse = m.usage.length > 0
  // Offer force only when the refusal is the "it is on the site" one.
  const canForce = refusal !== null && (inUse || /force/i.test(refusal))

  return (
    <li className={cx('flex min-w-0 flex-col overflow-hidden rounded-card border bg-surface', m.alt ? 'border-line' : 'border-alert')}>
      <div className="aspect-[4/3] bg-wash">
        <Img pic={m} sizes="(max-width: 640px) 92vw, (max-width: 1280px) 45vw, 360px" alt={name} />
      </div>
      <div className="flex flex-col gap-3 px-3.5 py-3">
        <Field
          label={m.alt ? 'Description' : 'Description — needed'}
          error={state?.tone === 'bad' ? state.text : undefined}
          hint={state && state.tone !== 'bad' ? state.text : 'Saved when you leave the box or press Enter.'}
        >
          <Input
            type="text"
            value={alt}
            maxLength={300}
            autoComplete="off"
            placeholder="e.g. Blue matcha latte on the counter"
            missing={!m.alt && !alt}
            onFocus={() => {
              editing.current = true
            }}
            onChange={(e) => {
              setAlt(e.target.value)
              setState(null)
            }}
            onBlur={() => {
              editing.current = false
              void flush()
            }}
            onKeyDown={(e) => {
              if (e.key === 'Enter') {
                e.preventDefault()
                void flush()
              }
            }}
          />
        </Field>

        <div className="text-base">
          {inUse ? (
            <>
              <span className="text-ink-2">Used in </span>
              {m.usage.map((u, i) => (
                <span key={u.slot_key}>
                  {i > 0 && ', '}
                  <button
                    type="button"
                    onClick={() => onPlace(u.slot_key)}
                    className="font-semibold text-brand-ink underline-offset-2 hover:underline"
                  >
                    {u.label ?? p.slotMap.get(u.slot_key)?.label ?? u.slot_key}
                  </button>
                </span>
              ))}
            </>
          ) : (
            <span className="text-ink-2">Not used on the site yet</span>
          )}
        </div>
        {meta && <p className="fig break-words text-sm text-ink-2">{meta}</p>}

        {refusal === null ? (
          <div>
            <ConfirmTwiceButton
              variant="ghost"
              armedLabel="Tap again to delete"
              onConfirm={() => void remove(false)}
              pending={deleting}
              pendingLabel="Deleting…"
              className="max-compact:h-11"
            >
              Delete photo
            </ConfirmTwiceButton>
          </div>
        ) : (
          <div className="flex flex-col gap-2 rounded-button border-[1.5px] border-dashed border-alert px-3 py-2.5">
            <p className="text-base font-bold">{canForce ? 'This photo is on the website' : 'Couldn’t delete'}</p>
            <p className="text-base">{refusal}</p>
            {canForce && (
              <p className="text-sm text-ink-2">Deleting it will leave those places empty until you choose another photo.</p>
            )}
            <div className="flex flex-wrap gap-2">
              {canForce && (
                <ConfirmTwiceButton
                  armedLabel="Tap again to remove and delete"
                  onConfirm={() => void remove(true)}
                  pending={deleting}
                  pendingLabel="Deleting…"
                  className="max-compact:h-11"
                >
                  Remove from those places and delete
                </ConfirmTwiceButton>
              )}
              <Button variant="ghost" size="sm" className="max-compact:h-11" onClick={() => setRefusal(null)} disabled={deleting}>
                Keep it
              </Button>
            </div>
          </div>
        )}
      </div>
    </li>
  )
}
