/**
 * An ingredient's reference photo as a small square, the same slot the list's
 * letter badge used to fill (Ingredients, Stock, the recipe ingredient picker).
 * No photo, or a photo that fails to load, falls back to whatever `fallback`
 * the caller passes (the letter, the tier badge), on the same grey tile.
 */
import { useState } from 'react'
import type { ReactNode } from 'react'
import { cx } from '../../components/ui'
import { mediaSrc } from '../../lib/menu-api'

export function IngredientThumb({
  url,
  name,
  fallback,
  className = 'size-11 rounded-control',
  corner,
  decorative = false,
}: {
  url: string | null | undefined
  name: string
  fallback: ReactNode
  /** Size and radius; the default matches the list badge (44px, control radius). */
  className?: string
  /** Something small to pin to the bottom-right corner over the photo (the tier). */
  corner?: ReactNode
  /** alt="" where the name is already the text right beside it (the picker). */
  decorative?: boolean
}) {
  const [broken, setBroken] = useState<string | null>(null)
  const src = mediaSrc(url ?? null)
  const show = src !== null && broken !== src
  return (
    <span className={cx('relative grid flex-none place-items-center overflow-hidden bg-line-soft', className)}>
      {show ? (
        <>
          <img
            src={src}
            alt={decorative ? '' : name}
            loading="lazy"
            decoding="async"
            onError={() => setBroken(src)}
            className="absolute inset-0 size-full object-cover"
          />
          {corner && <span className="absolute bottom-0.5 right-0.5">{corner}</span>}
        </>
      ) : (
        fallback
      )}
    </span>
  )
}
