/**
 * The product photo slot (spec §V2.2/§V2.3, A5). Click or drop an image; it is
 * downscaled in the browser to at most 1200px and encoded as WebP 0.85 (the
 * design's image-slot behaviour), then uploaded as the raw body. The server
 * checks the bytes, not this client's claim, and stores it by content hash.
 */
import { useRef, useState } from 'react'
import type { DragEvent } from 'react'
import { Button, cx } from '../../components/ui'
import { menuApi, mediaSrc, useInvalidateMenu } from '../../lib/menu-api'
import { useOperator } from '../../lib/operator'

const MAX_EDGE = 1200

async function shrink(file: File): Promise<Blob> {
  const bitmap = await createImageBitmap(file)
  const scale = Math.min(1, MAX_EDGE / Math.max(bitmap.width, bitmap.height))
  const w = Math.max(1, Math.round(bitmap.width * scale))
  const h = Math.max(1, Math.round(bitmap.height * scale))
  const canvas = document.createElement('canvas')
  canvas.width = w
  canvas.height = h
  const ctx = canvas.getContext('2d')
  if (!ctx) return file
  ctx.drawImage(bitmap, 0, 0, w, h)
  const out = await new Promise<Blob | null>((resolve) => canvas.toBlob(resolve, 'image/webp', 0.85))
  if (out && out.type === 'image/webp') return out
  const jpeg = await new Promise<Blob | null>((resolve) => canvas.toBlob(resolve, 'image/jpeg', 0.85))
  return jpeg ?? file
}

export function PhotoView({ url, placeholder, className }: { url: string | null; placeholder: string; className?: string }) {
  const src = mediaSrc(url)
  return (
    <div className={cx('grid place-items-center overflow-hidden bg-line-soft', className)}>
      {src ? (
        <img src={src} alt="" className="absolute inset-0 size-full object-cover" loading="lazy" />
      ) : (
        <>
          {/* The design's empty slot: a dashed ring and a picture glyph. */}
          <span aria-hidden="true" className="pointer-events-none absolute inset-0 border-[1.5px] border-dashed border-line-strong" />
          <span className="flex flex-col items-center gap-1.5 px-3 text-center text-sm font-medium text-ink-2">
            <svg
              aria-hidden="true"
              width="28"
              height="28"
              viewBox="0 0 24 24"
              fill="none"
              stroke="currentColor"
              strokeWidth="1.6"
              strokeLinecap="round"
              strokeLinejoin="round"
              className="opacity-60"
            >
              <rect x="3" y="3" width="18" height="18" rx="2" />
              <circle cx="8.5" cy="8.5" r="1.5" />
              <path d="m21 15-5-5L5 21" />
            </svg>
            {placeholder}
          </span>
        </>
      )}
    </div>
  )
}

export function PhotoSlot({ menuItemId, url, name }: { menuItemId: number; url: string | null; name: string }) {
  const input = useRef<HTMLInputElement>(null)
  const [operator] = useOperator()
  const [busy, setBusy] = useState(false)
  const [message, setMessage] = useState<string | null>(null)
  const [over, setOver] = useState(false)
  const invalidate = useInvalidateMenu()

  const upload = async (file: File | undefined) => {
    if (!file) return
    setMessage(null)
    setBusy(true)
    try {
      const blob = await shrink(file).catch(() => file)
      const r = await menuApi.uploadPhoto(menuItemId, blob, operator)
      if (r.kind === 'ok') await invalidate()
      else setMessage(r.message)
    } finally {
      setBusy(false)
    }
  }

  const onDrop = (e: DragEvent) => {
    e.preventDefault()
    setOver(false)
    void upload(e.dataTransfer.files[0])
  }

  return (
    <div className="flex flex-col gap-1.5">
      <button
        type="button"
        onClick={() => input.current?.click()}
        onDragOver={(e) => {
          e.preventDefault()
          setOver(true)
        }}
        onDragLeave={() => setOver(false)}
        onDrop={onDrop}
        aria-label={url ? `Replace the photo of ${name}` : `Add a photo of ${name}`}
        className={cx('relative h-[170px] w-full flex-none overflow-hidden rounded-card', over && 'ring-2 ring-brand')}
      >
        <PhotoView url={url} placeholder={busy ? 'Uploading…' : 'Drop a photo of this item'} className="absolute inset-0" />
      </button>
      <input
        ref={input}
        type="file"
        accept="image/webp,image/jpeg,image/png"
        className="sr-only"
        tabIndex={-1}
        onChange={(e) => {
          void upload(e.target.files?.[0])
          e.target.value = ''
        }}
      />
      {(url || message) && (
        <div className="flex items-center gap-2">
          {message && (
            <span role="alert" className="flex-1 text-sm text-bad-ink">
              {message}
            </span>
          )}
          {url && (
            <Button
              variant="link"
              className="ml-auto"
              onClick={async () => {
                const r = await menuApi.clearPhoto(menuItemId)
                if (r.kind === 'ok') await invalidate()
                else setMessage(r.message)
              }}
            >
              Remove photo
            </Button>
          )}
        </div>
      )}
    </div>
  )
}
