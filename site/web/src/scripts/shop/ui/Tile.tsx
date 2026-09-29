// A choosable tile: size, milk, cup, coffee bean. One native input (radio or checkbox)
// per tile, visually hidden, so keyboard and screen readers get the real thing.
import type { ComponentChildren } from 'preact';
import { Tick } from './Segmented';

interface Props {
  name: string;
  value: string;
  kind: 'single' | 'multi';
  checked: boolean;
  disabled?: boolean;
  onChange: (checked: boolean) => void;
  /** The tile's title. */
  title: string;
  /** Second line: "+£0.30", "182 kcal", a tagline. */
  meta?: ComponentChildren;
  /** Third line, italic: an option's description. */
  tagline?: string | null;
  photo?: string | null;
  layout?: 'tile' | 'photo' | 'row';
  soldOut?: boolean;
}

export function Tile({ name, value, kind, checked, disabled, onChange, title, meta, tagline, photo, layout = 'tile', soldOut }: Props) {
  const off = disabled || soldOut;
  return (
    <label class={['sh-tile', `sh-tile--${layout}`, checked && 'is-on', off && 'is-off'].filter(Boolean).join(' ')}>
      <input
        class="sr-only"
        type={kind === 'single' ? 'radio' : 'checkbox'}
        name={name}
        value={value}
        checked={checked}
        disabled={off}
        onChange={(e) => onChange((e.currentTarget as HTMLInputElement).checked)}
      />
      {layout === 'photo' && (
        <span class="sh-tile__photo">
          {photo ? <img src={photo} alt="" loading="lazy" /> : <span class="sh-tile__ph" aria-hidden="true">{title.trim()[0]?.toUpperCase()}</span>}
        </span>
      )}
      {layout === 'row' && (
        <span class="sh-tile__box" aria-hidden="true">
          <Tick />
        </span>
      )}
      <span class="sh-tile__text">
        <span class="sh-tile__title">{title}</span>
        {soldOut ? <span class="sh-tile__meta">Sold out</span> : meta ? <span class="sh-tile__meta">{meta}</span> : null}
        {tagline && <span class="sh-tile__tag">{tagline}</span>}
      </span>
      {layout !== 'row' && (
        <span class="sh-tile__mark" aria-hidden="true">
          <Tick />
        </span>
      )}
    </label>
  );
}
