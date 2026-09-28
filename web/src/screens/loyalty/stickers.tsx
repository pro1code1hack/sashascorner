/**
 * The eight stamp stickers (docs/loyalty/BACKOFFICE-V2.md §2). The art is
 * `assets/pass/stickers/slot-N.svg`, copied to `web/public/stickers/`.
 */
import { cx } from '../../components/ui'
import type { Sticker, StickerKey } from '../../lib/types/loyalty'

export const STICKERS: readonly Sticker[] = [
  { key: 'cat', name: 'Cat', url: '/stickers/slot-1.svg' },
  { key: 'seal', name: 'Seal', url: '/stickers/slot-2.svg' },
  { key: 'matcha', name: 'Matcha', url: '/stickers/slot-3.svg' },
  { key: 'boba', name: 'Boba', url: '/stickers/slot-4.svg' },
  { key: 'cake', name: 'Cake', url: '/stickers/slot-5.svg' },
  { key: 'knight', name: 'Knight', url: '/stickers/slot-6.svg' },
  { key: 'latte', name: 'Rose latte', url: '/stickers/slot-7.svg' },
  { key: 'star', name: 'Star', url: '/stickers/slot-8.svg' },
]

const BY_KEY = new Map(STICKERS.map((s) => [s.key, s]))

export function stickerOf(key: StickerKey | string | null | undefined): Sticker | null {
  return key ? (BY_KEY.get(key as StickerKey) ?? null) : null
}

export function stickerName(key: StickerKey | string | null | undefined): string {
  return stickerOf(key)?.name ?? ''
}

/** The sticker art alone. Decorative unless `label` is given. */
export function StickerImg({
  sticker,
  size = 28,
  label,
  className,
}: {
  sticker: StickerKey | string | null | undefined
  size?: number
  label?: string
  className?: string
}) {
  const s = stickerOf(sticker)
  if (!s) return null
  return (
    <img
      src={s.url}
      width={size}
      height={size}
      alt={label ?? ''}
      aria-hidden={label ? undefined : true}
      draggable={false}
      className={cx('select-none', className)}
    />
  )
}
