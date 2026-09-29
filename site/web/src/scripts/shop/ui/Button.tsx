import type { ComponentChildren, JSX } from 'preact';

export type ButtonTone = 'caramel' | 'ink' | 'ghost' | 'paper' | 'link';

interface Props extends Omit<JSX.ButtonHTMLAttributes<HTMLButtonElement>, 'size'> {
  tone?: ButtonTone;
  /** Renders as a link when set (same look). Same-origin /order links stay in-app. */
  href?: string;
  size?: 'md' | 'sm';
  block?: boolean;
  busy?: boolean;
  children: ComponentChildren;
}

/** Brand button: pill, 48px tall, caramel for the one primary action on a screen. */
export function Button({ tone = 'ink', href, size = 'md', block, busy, children, class: cls, className, disabled, type, ...rest }: Props) {
  const classes = ['sh-btn', `sh-btn--${tone}`, size === 'sm' && 'sh-btn--sm', block && 'sh-btn--block', busy && 'is-busy', cls, className]
    .filter(Boolean)
    .join(' ');
  if (href && !disabled) {
    return (
      <a class={classes} href={href} {...(rest as unknown as JSX.AnchorHTMLAttributes<HTMLAnchorElement>)}>
        {children}
      </a>
    );
  }
  return (
    <button class={classes} type={type ?? 'button'} disabled={disabled || busy} aria-busy={busy ? 'true' : undefined} {...rest}>
      {children}
    </button>
  );
}
