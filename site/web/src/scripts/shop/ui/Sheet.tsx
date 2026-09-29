// A modal sheet: bottom sheet on phones, centred dialog on wider screens. Native
// <dialog> gives the focus trap, Escape, the backdrop and focus return for free; the
// first control inside gets focus when it opens so the Tab order starts in the sheet.
import type { ComponentChildren } from 'preact';
import { useEffect, useRef } from 'preact/hooks';

interface Props {
  open: boolean;
  onClose: () => void;
  title: string;
  children: ComponentChildren;
  /** Buttons row at the bottom. */
  actions?: ComponentChildren;
  /** Wider body (allergen text). */
  wide?: boolean;
}

let seq = 0;

export function Sheet({ open, onClose, title, children, actions, wide }: Props) {
  const ref = useRef<HTMLDialogElement>(null);
  const id = useRef(`sh-sheet-${++seq}`);
  useEffect(() => {
    const d = ref.current;
    if (!d) return;
    if (open && !d.open) {
      d.showModal();
      // The primary action first (it is what most people came to press); the close
      // button is still a Tab away.
      const first = d.querySelector<HTMLElement>(
        '.sh-sheet__actions button, .sh-sheet__actions a, .sh-sheet__body input, .sh-sheet__body button, .sh-sheet__body a',
      );
      (first ?? d.querySelector<HTMLElement>('.sh-sheet__close'))?.focus();
    } else if (!open && d.open) d.close();
  }, [open]);
  return (
    <dialog
      ref={ref}
      class={['sh-sheet', wide && 'sh-sheet--wide'].filter(Boolean).join(' ')}
      aria-labelledby={id.current}
      onClose={onClose}
      onClick={(e) => {
        // A click on the backdrop (outside the panel) closes the sheet.
        if (e.target === ref.current) onClose();
      }}
    >
      <div class="sh-sheet__panel">
        <div class="sh-sheet__head">
          <h2 id={id.current} class="sh-sheet__title">
            {title}
          </h2>
          <button type="button" class="sh-iconbtn sh-sheet__close" aria-label="Close" onClick={onClose}>
            <svg viewBox="0 0 20 20" width="20" height="20" aria-hidden="true" focusable="false">
              <path d="M5 5l10 10M15 5L5 15" stroke="currentColor" stroke-width="2" stroke-linecap="round" />
            </svg>
          </button>
        </div>
        <div class="sh-sheet__body">{children}</div>
        {actions && <div class="sh-sheet__actions">{actions}</div>}
      </div>
    </dialog>
  );
}
