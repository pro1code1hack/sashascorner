// A two-or-more way switch (Takeaway / Eat in): a radiogroup of buttons, the chosen one
// filled olive with a tick. Arrow keys move between them like native radios.
interface Option<T extends string> {
  value: T;
  label: string;
  disabled?: boolean;
}
interface Props<T extends string> {
  label: string;
  options: Option<T>[];
  value: T;
  onChange: (v: T) => void;
  class?: string;
}

export function Segmented<T extends string>({ label, options, value, onChange, class: cls }: Props<T>) {
  const enabled = options.filter((o) => !o.disabled);
  const onKey = (e: KeyboardEvent) => {
    const dir = e.key === 'ArrowRight' || e.key === 'ArrowDown' ? 1 : e.key === 'ArrowLeft' || e.key === 'ArrowUp' ? -1 : 0;
    if (!dir) return;
    e.preventDefault();
    const i = enabled.findIndex((o) => o.value === value);
    const next = enabled[(i + dir + enabled.length) % enabled.length];
    if (next) {
      onChange(next.value);
      (e.currentTarget as HTMLElement).querySelector<HTMLElement>(`[data-value="${next.value}"]`)?.focus();
    }
  };
  return (
    <div class={['sh-seg', cls].filter(Boolean).join(' ')} role="radiogroup" aria-label={label} onKeyDown={onKey}>
      {options.map((o) => {
        const on = o.value === value;
        return (
          <button
            key={o.value}
            type="button"
            role="radio"
            aria-checked={on}
            tabIndex={on ? 0 : -1}
            disabled={o.disabled}
            data-value={o.value}
            class={['sh-seg__opt', on && 'is-on'].filter(Boolean).join(' ')}
            onClick={() => onChange(o.value)}
          >
            <Tick />
            {o.label}
          </button>
        );
      })}
    </div>
  );
}

export function Tick() {
  return (
    <svg class="sh-tick" viewBox="0 0 20 20" width="18" height="18" aria-hidden="true" focusable="false">
      <path d="M4 10.5l4 4 8-9" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round" />
    </svg>
  );
}
