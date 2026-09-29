// A product or category photo, or the brand placeholder (sage/oat with the initial)
// when there is none. Never a stock photo standing in for the café's food. A
// `borrowed` photo (a category showing one of its products) is marked so the tile can
// treat it as a hint rather than the category's own picture.
import { initial } from '../format';

interface Props {
  src: string | null | undefined;
  alt: string;
  /** Name for the placeholder initial. */
  name: string;
  class?: string;
  eager?: boolean;
  sizes?: string;
  borrowed?: boolean;
}

export function Photo({ src, alt, name, class: cls, eager, sizes, borrowed }: Props) {
  if (src) {
    return (
      <img
        class={['sh-photo', borrowed && 'sh-photo--borrowed', cls].filter(Boolean).join(' ')}
        src={src}
        alt={alt}
        loading={eager ? 'eager' : 'lazy'}
        decoding="async"
        sizes={sizes}
      />
    );
  }
  return (
    <span class={['sh-photo sh-photo--ph', cls].filter(Boolean).join(' ')} role="img" aria-label={alt || name}>
      <span aria-hidden="true">{initial(name)}</span>
    </span>
  );
}
