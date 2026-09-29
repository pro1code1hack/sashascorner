// A sentence the customer must not miss: a change to their basket, an error, the shop
// being closed. Colour only marks the crossed threshold (coffee rule for a problem).
import type { ComponentChildren } from 'preact';

interface Props {
  tone?: 'info' | 'warn' | 'error';
  children: ComponentChildren;
  onDismiss?: () => void;
  class?: string;
}

export function Notice({ tone = 'info', children, onDismiss, class: cls }: Props) {
  return (
    <div class={['sh-notice', `sh-notice--${tone}`, cls].filter(Boolean).join(' ')} role={tone === 'error' ? 'alert' : 'status'}>
      <div class="sh-notice__text">{children}</div>
      {onDismiss && (
        <button type="button" class="sh-iconbtn" aria-label="Dismiss" onClick={onDismiss}>
          <svg viewBox="0 0 20 20" width="18" height="18" aria-hidden="true" focusable="false">
            <path d="M5 5l10 10M15 5L5 15" stroke="currentColor" stroke-width="2" stroke-linecap="round" />
          </svg>
        </button>
      )}
    </div>
  );
}
