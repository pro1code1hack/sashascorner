// A labelled input with its error under it, wired with aria-describedby / aria-invalid.
import type { ComponentChildren, JSX } from 'preact';

interface Props extends Omit<JSX.InputHTMLAttributes<HTMLInputElement>, 'label'> {
  label: string;
  hint?: string;
  error?: string | null;
  /** Renders `children` instead of an <input> (a select, a textarea) inside the label. */
  children?: ComponentChildren;
}

let n = 0;
export function Field({ label, hint, error, children, id, class: cls, ...rest }: Props) {
  const fid = (id as string | undefined) ?? `sh-f${++n}`;
  const describe = [hint && `${fid}-hint`, error && `${fid}-err`].filter(Boolean).join(' ') || undefined;
  return (
    <div class={['sh-field', error && 'has-error', cls].filter(Boolean).join(' ')}>
      <label for={fid} class="sh-field__label">
        {label}
      </label>
      {hint && (
        <p id={`${fid}-hint`} class="sh-field__hint">
          {hint}
        </p>
      )}
      {children ?? (
        <input id={fid} class="sh-input" aria-describedby={describe} aria-invalid={error ? 'true' : undefined} {...rest} />
      )}
      {error && (
        <p id={`${fid}-err`} class="sh-field__err" role="alert">
          {error}
        </p>
      )}
    </div>
  );
}
