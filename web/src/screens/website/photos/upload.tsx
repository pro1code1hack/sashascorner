/**
 * Uploads, one file at a time, each with its own progress row. Ported from the
 * site's scripts/admin/library.ts `uploadFiles`.
 *
 * The site keeps the original and makes its own 480/960/1600 variants, and it
 * de-duplicates by the file's hash, so a photo is sent as it is. The one
 * exception: a file over the 15 MB limit is shrunk in the browser first rather
 * than refused (a phone's full-size export can be larger than that).
 *
 * The site answers 200 rather than 201 for a file already in the library;
 * `siteUpload` does not pass the status on, so a duplicate is recognised by
 * its id already being in the library.
 */
import { useQueryClient } from '@tanstack/react-query'
import { useCallback, useState } from 'react'
import { cx } from '../../../components/ui'
import { siteUpload } from '../../../lib/website-api'
import { MEDIA_KEY, kb, normMedia, usePutMedia, type Media } from './model'

export const MAX_BYTES = 15 * 1024 * 1024
export const TYPES = ['image/jpeg', 'image/png', 'image/webp']
export const ACCEPT = TYPES.join(',')

export interface UploadRow {
  id: number
  name: string
  /** 0..100 while sending. */
  progress: number
  state: 'waiting' | 'sending' | 'processing' | 'ok' | 'bad'
  message: string
}

let rowSeq = 0

/** Shrink an over-limit photo to at most 3000px on its long edge. */
async function shrinkLarge(file: File): Promise<Blob> {
  const bitmap = await createImageBitmap(file)
  const scale = Math.min(1, 3000 / Math.max(bitmap.width, bitmap.height))
  const canvas = document.createElement('canvas')
  canvas.width = Math.max(1, Math.round(bitmap.width * scale))
  canvas.height = Math.max(1, Math.round(bitmap.height * scale))
  const ctx = canvas.getContext('2d')
  if (!ctx) return file
  ctx.drawImage(bitmap, 0, 0, canvas.width, canvas.height)
  const out = await new Promise<Blob | null>((resolve) => canvas.toBlob(resolve, 'image/jpeg', 0.9))
  return out ?? file
}

export function useUploader(announce: (text: string) => void) {
  const qc = useQueryClient()
  const putMedia = usePutMedia()
  const [rows, setRows] = useState<UploadRow[]>([])
  const [busy, setBusy] = useState(false)

  const patch = (id: number, p: Partial<UploadRow>) => setRows((rs) => rs.map((r) => (r.id === id ? { ...r, ...p } : r)))

  const upload = useCallback(
    async (files: File[]): Promise<Media[]> => {
      if (!files.length) return []
      setBusy(true)
      const fresh: UploadRow[] = files.map((f) => ({
        id: ++rowSeq,
        name: f.name,
        progress: 0,
        state: 'waiting',
        message: 'Waiting…',
      }))
      // Failures from the last batch stay until this one starts.
      setRows(fresh)
      announce(`Uploading ${files.length} ${files.length === 1 ? 'photo' : 'photos'}.`)
      const done: Media[] = []
      const failed: string[] = []
      let dups = 0

      for (const [i, f] of files.entries()) {
        const row = fresh[i]
        if (!row) continue
        if (!TYPES.includes(f.type)) {
          patch(row.id, { state: 'bad', message: 'Not a photo we can use. Please choose a JPEG, PNG or WebP image.' })
          failed.push(f.name)
          continue
        }
        let body: Blob = f
        if (f.size > MAX_BYTES) {
          patch(row.id, { state: 'sending', message: `Large (${kb(f.size)}): making it smaller first…` })
          body = await shrinkLarge(f).catch(() => f)
          if (body.size > MAX_BYTES) {
            patch(row.id, {
              state: 'bad',
              message: `Too large (${kb(f.size)}). The limit is 15 MB. Try exporting it smaller from your phone or computer.`,
            })
            failed.push(f.name)
            continue
          }
        }
        const known = new Set((qc.getQueryData<Media[]>(MEDIA_KEY) ?? []).map((m) => m.id))
        patch(row.id, { state: 'sending', message: 'Uploading…' })
        const form = new FormData()
        form.append('file', body, body === f ? f.name : f.name.replace(/\.\w+$/, '') + '.jpg')
        form.append('alt', '')
        const r = await siteUpload<unknown>('/media', form, (p) =>
          patch(row.id, {
            progress: Math.round(p * 100),
            state: p >= 1 ? 'processing' : 'sending',
            message: p >= 1 ? 'Processing…' : `${Math.round(p * 100)}%`,
          }),
        )
        if (r.kind === 'ok') {
          const m = normMedia(r.data)
          const dup = known.has(m.id)
          if (dup) dups++
          putMedia(m)
          done.push(m)
          patch(row.id, { progress: 100, state: 'ok', message: dup ? 'Already in your library' : 'Added' })
        } else {
          patch(row.id, { state: 'bad', message: `Couldn’t upload: ${r.message}` })
          failed.push(f.name)
        }
      }

      const parts: string[] = []
      const added = done.length - dups
      if (added) parts.push(`${added} ${added === 1 ? 'photo' : 'photos'} added to the library.`)
      if (dups) parts.push(`${dups} ${dups === 1 ? 'was' : 'were'} already in the library.`)
      if (failed.length) parts.push(`${failed.length} couldn’t be added: ${failed.join(', ')}.`)
      announce(parts.join(' '))
      setBusy(false)
      // Clear the successful rows after a while; keep failures until the next batch.
      const ids = new Set(fresh.map((r) => r.id))
      window.setTimeout(() => setRows((rs) => rs.filter((r) => !(ids.has(r.id) && r.state === 'ok'))), 6000)
      return done
    },
    // `patch` only uses the state setter, so it needs no dependency.
    [qc, putMedia, announce],
  )

  return { rows, busy, upload }
}

export function UploadList({ rows, label }: { rows: UploadRow[]; label: string }) {
  if (!rows.length) return null
  return (
    <ul aria-label={label} className="flex flex-col gap-1.5">
      {rows.map((r) => (
        <li
          key={r.id}
          className={cx(
            'grid grid-cols-[minmax(0,1fr)_auto] items-center gap-x-3 gap-y-1 rounded-control border px-3 py-2 text-base',
            r.state === 'bad' ? 'border-alert bg-alert-wash' : 'border-line bg-surface',
          )}
        >
          <span className="truncate font-semibold">{r.name}</span>
          <span className={cx('text-sm', r.state === 'bad' ? 'col-span-2 text-bad-ink' : 'fig text-ink-2')}>
            {r.message}
          </span>
          {(r.state === 'sending' || r.state === 'processing' || r.state === 'waiting') && (
            <progress
              className="col-span-2 h-1.5 w-full accent-brand"
              max={100}
              value={r.progress}
              aria-label={`Uploading ${r.name}`}
            />
          )}
        </li>
      ))}
    </ul>
  )
}
