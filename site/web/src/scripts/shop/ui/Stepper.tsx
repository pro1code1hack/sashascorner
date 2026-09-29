// Quantity: minus, number, plus. 44px targets; the number is a live region for readers.
interface Props {
  value: number;
  min?: number;
  max?: number;
  onChange: (n: number) => void;
  label?: string;
  /** Inverted for the olive bottom bar. */
  onDark?: boolean;
  /** What pressing minus at `min` means, e.g. "Remove". Shown as the button's label. */
  minLabel?: string;
  onMin?: () => void;
}

export function Stepper({ value, min = 1, max = 20, onChange, label = 'Quantity', onDark, minLabel, onMin }: Props) {
  const atMin = value <= min;
  const atMax = value >= max;
  return (
    <div class={['sh-step', onDark && 'on-dark sh-step--dark'].filter(Boolean).join(' ')} role="group" aria-label={label}>
      <button
        type="button"
        class="sh-step__btn"
        aria-label={atMin && minLabel ? minLabel : 'One fewer'}
        disabled={atMin && !onMin}
        onClick={() => (atMin ? onMin?.() : onChange(value - 1))}
      >
        <svg viewBox="0 0 20 20" width="18" height="18" aria-hidden="true" focusable="false">
          <path d="M4 10h12" stroke="currentColor" stroke-width="2.2" stroke-linecap="round" />
        </svg>
      </button>
      <output class="sh-step__n num" aria-live="polite" aria-label={label}>
        {value}
      </output>
      <button type="button" class="sh-step__btn" aria-label="One more" disabled={atMax} onClick={() => onChange(value + 1)}>
        <svg viewBox="0 0 20 20" width="18" height="18" aria-hidden="true" focusable="false">
          <path d="M4 10h12M10 4v12" stroke="currentColor" stroke-width="2.2" stroke-linecap="round" />
        </svg>
      </button>
    </div>
  );
}
