import type { CSSProperties } from 'react'
import { cx } from '../../../components/ui'
import { mediaSrcset, mediaUrl } from '../../../lib/website-api'
import type { Pic } from './model'

/**
 * A site photo. Every URL goes through `mediaUrl` / `mediaSrcset` (the site's
 * `/api/media-files/…` is served here at `/api/website-media/…`). The blurred
 * preview sits behind it while the real file loads.
 */
export function Img({
  pic,
  sizes,
  alt,
  className,
  style,
  eager = false,
}: {
  pic: Pic
  sizes: string
  alt: string
  className?: string
  style?: CSSProperties
  eager?: boolean
}) {
  return (
    <img
      src={mediaUrl(pic.src)}
      srcSet={pic.srcset ? mediaSrcset(pic.srcset) : undefined}
      sizes={pic.srcset ? sizes : undefined}
      width={pic.width || undefined}
      height={pic.height || undefined}
      alt={alt}
      loading={eager ? 'eager' : 'lazy'}
      decoding="async"
      draggable={false}
      className={cx('block size-full object-cover', className)}
      style={{
        ...(pic.blur ? { backgroundImage: `url("${pic.blur}")`, backgroundSize: 'cover' } : {}),
        ...style,
      }}
    />
  )
}
