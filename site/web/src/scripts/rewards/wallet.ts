// "Add to Apple Wallet" / "Add to Google Wallet" / "Keep it in the browser", ordered for
// the device in hand. Plain text buttons in the site's own style: the official wallet
// badges carry licence rules and are to be dropped in later.
import { qrSvg } from './qr';
import { platform } from './api';
import { track } from '../track';

export interface WalletLinks {
  apple: string | null;
  google: string | null;
  /** Relative web-card link with its token (`/c/<id>#t=...`). Omit on the card itself. */
  webCard?: string | null;
  /** Link for the "open it on your phone" QR on desktops. */
  phoneLink?: string | null;
}

const appleBtn = (href: string, primary: boolean) =>
  `<a class="btn ${primary ? '' : 'btn--ghost '}rw-wbtn" href="${href}" data-wallet="apple">Add to Apple Wallet</a>`;
const googleBtn = (href: string, primary: boolean) =>
  `<a class="btn ${primary ? '' : 'btn--ghost '}rw-wbtn" href="${href}" data-wallet="google" rel="noopener">Add to Google Wallet</a>`;
const webBtn = (href: string) => `<a class="btn btn--ghost rw-wbtn" href="${href}" data-wallet="web">Keep it in the browser</a>`;

export function walletHtml(w: WalletLinks): string {
  const p = platform();
  const out: string[] = [];
  const extra: string[] = [];
  if (p === 'ios') {
    if (w.apple) out.push(appleBtn(w.apple, true));
    if (w.webCard) out.push(webBtn(w.webCard));
    if (w.google) extra.push(`<a href="${w.google}" data-wallet="google" rel="noopener">Add to Google Wallet instead</a>`);
  } else if (p === 'android') {
    if (w.google) out.push(googleBtn(w.google, true));
    if (w.webCard) out.push(webBtn(w.webCard));
    if (w.apple) extra.push(`<a href="${w.apple}" data-wallet="apple">Add to Apple Wallet instead</a>`);
  } else {
    if (w.apple) out.push(appleBtn(w.apple, true));
    if (w.google) out.push(googleBtn(w.google, !w.apple));
    if (w.webCard) out.push(webBtn(w.webCard));
  }
  const none =
    !w.apple && !w.google
      ? `<p class="rw-wnote">Apple and Google Wallet aren't switched on yet. The card works in your browser in the meantime, and it will be the same card when they are.</p>`
      : '';
  const phone =
    p === 'desktop' && w.phoneLink
      ? `<div class="rw-phone">
          ${qrSvg(new URL(w.phoneLink, location.href).href, { label: 'QR code that opens your card on a phone' })}
          <p><strong>On a computer?</strong> Scan this with your phone's camera to open your card there, then add it to your wallet.</p>
        </div>`
      : '';
  return `${none}<div class="rw-wbtns">${out.join('')}</div>${extra.length ? `<p class="rw-wextra">${extra.join(' · ')}</p>` : ''}${phone}`;
}

/** Tracks wallet taps inside `root` (delegated; call once per root). */
export function wireWalletTracking(root: HTMLElement, where: string): void {
  root.addEventListener('click', (e) => {
    const a = (e.target as HTMLElement).closest<HTMLAnchorElement>('[data-wallet]');
    const kind = a?.dataset.wallet;
    if (kind === 'apple') track('wallet_add_apple', { page: where });
    else if (kind === 'google') track('wallet_add_google', { page: where });
  });
}
