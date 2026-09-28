// /c/<card_id>: the web card. Reads the id from the path and the token from #t=,
// keeps the token in localStorage and cuts it from the address bar, then polls the
// card every 20 s while the page is visible so a stamp shows up while you stand at
// the till.
//
// Phase 3: the member's other cards (a matcha club, a points card) show as tabs above
// the card -- each tab is that card's own link, token included -- and programmes they
// have not joined are offered in "More cards", one tap, no form.
import { api, CARD_ID_RE, forgetToken, isMock, loadToken, platform, saveToken } from './api';
import { cardHtml, esc, type CardState, type JoinResult } from './card';
import { walletHtml, wireWalletTracking } from './wallet';

const POLL_MS = 20_000;
const $ = <T extends Element = HTMLElement>(sel: string) => document.querySelector<T>(sel)!;

type State = 'loading' | 'card' | 'notoken' | 'notfound' | 'deleted' | 'error';
const TITLES: Record<State, string> = {
  loading: 'Your card',
  card: 'Your card',
  notoken: "This card isn't saved on this device.",
  notfound: "We can't find this card.",
  deleted: 'Your card is deleted.',
  error: "Your card didn't load.",
};
function setState(s: State) {
  document.querySelectorAll<HTMLElement>('[data-state]').forEach((el) => (el.hidden = el.dataset.state !== s));
  const h1 = $('[data-title]');
  const changed = h1.textContent !== TITLES[s];
  h1.textContent = TITLES[s];
  if (changed && s !== 'card') h1.focus();
}

// A home-screen icon must open this card, not the site's manifest start_url ("/").
// Without a manifest link, Safari and Chrome bookmark the page being viewed.
document.querySelector('link[rel="manifest"]')?.remove();

// ---- id and token ------------------------------------------------------------------------
const id = decodeURIComponent(location.pathname.replace(/^\/c\/?/, '').replace(/\.html$/, '').split('/')[0] ?? '');
const hashToken = new URLSearchParams(location.hash.slice(1)).get('t');
if (hashToken && CARD_ID_RE.test(id)) {
  saveToken(id, hashToken);
  history.replaceState(history.state, '', location.pathname + location.search);
  void navigator.storage?.persist?.().catch(() => undefined);
}
const token = CARD_ID_RE.test(id) ? (hashToken ?? loadToken(id)) : null;

// ---- rendering ---------------------------------------------------------------------------
const cardEl = $('[data-card]');
const announce = $('[data-announce]');
const walletRoot = $('[data-wallet-root]');
wireWalletTracking(walletRoot, 'card');
let current: CardState | null = null;

function render(s: CardState) {
  const prev = current;
  current = s;
  if (prev && prev.updated_at === s.updated_at && prev.marketing_opt_in === s.marketing_opt_in) return;

  const ready = s.rewards.some((r) => r.kind === 'STAMP_CARD');
  const wasReady = prev?.rewards.some((r) => r.kind === 'STAMP_CARD') ?? false;
  const fresh = prev && !ready && s.stamps_current > prev.stamps_current ? s.stamps_current - prev.stamps_current : 0;
  cardEl.innerHTML = cardHtml(s, { qr: s.qr_payload, qrLabel: 'Your card code, for scanning at the till', fresh });

  if (prev) {
    if (ready && !wasReady) announce.textContent = 'Your free drink is ready.';
    else if (fresh) announce.textContent = `Stamped. You've got ${s.stamps_current} of ${s.stamps_required}.`;
  }
  if (!prev || prev.wallets.apple_pass_url !== s.wallets.apple_pass_url || prev.wallets.google_save_url !== s.wallets.google_save_url) {
    walletRoot.innerHTML = walletHtml({
      apple: s.wallets.apple_pass_url,
      google: s.wallets.google_save_url,
      phoneLink: token ? `/c/${s.card_id}#t=${encodeURIComponent(token)}` : null,
    });
  }
  const box = $<HTMLInputElement>('[data-marketing]');
  if (!box.disabled) box.checked = s.marketing_opt_in;
  renderCards(s);
  setState('card');
}

function renderCards(s: CardState) {
  const others = s.other_cards ?? [];
  const nav = $('[data-cards]');
  nav.hidden = others.length === 0;
  if (others.length) {
    const tab = (name: string, count: string, ready: boolean, href: string, current: boolean) =>
      `<a href="${esc(href)}"${current ? ' aria-current="page"' : ''}><span>${esc(name)}</span><span class="num">${esc(count)}</span>${ready ? '<span class="ccards__dot">Reward</span>' : ''}</a>`;
    const here = tab(
      s.program_name,
      `${s.stamps_current}/${s.stamps_required}`,
      s.rewards.some((r) => r.kind === 'STAMP_CARD'),
      location.pathname,
      true,
    );
    const rest = others.map((o) => tab(o.program_name, `${o.stamps_current}/${o.stamps_required}`, o.reward_ready, o.web_card_url, false));
    // The main card first, as on the scanner.
    nav.innerHTML = (s.program_slug ?? 'stamp') === 'stamp' ? here + rest.join('') : [...rest, here].join('');
    // Each tab is the other card's own link (token in the fragment), so following it
    // is a normal page load that saves that card's token here like any other.
  }
  const joinable = s.joinable ?? [];
  $('[data-join-section]').hidden = joinable.length === 0;
  $('[data-join-list]').innerHTML = joinable
    .map(
      (p) =>
        `<li><span><strong>${esc(p.name)}</strong><small>${esc(p.description ?? (p.kind === 'POINTS' ? `${p.points_per_pound ?? ''} points a pound` : `${p.stamps_required} stamps, then ${p.reward_text.toLowerCase()}`))}</small></span><button class="btn btn--ghost" type="button" data-join="${esc(p.slug)}">Join</button></li>`,
    )
    .join('');
}

$('[data-join-list]').addEventListener('click', async (e) => {
  const b = (e.target as HTMLElement).closest<HTMLButtonElement>('[data-join]');
  if (!b || !token) return;
  const msg = $('[data-join-msg]');
  b.disabled = true;
  msg.textContent = 'Adding the card…';
  const r = await api<JoinResult>('POST', `/api/loyalty/card/${encodeURIComponent(id)}/programs`, {
    token,
    body: { program: b.dataset.join },
  });
  b.disabled = false;
  if (r.ok && r.data) {
    saveToken(r.data.card_id, r.data.token);
    msg.textContent = 'Done. Opening your new card…';
    location.assign(r.data.web_card_url);
    return;
  }
  msg.textContent =
    r.status === 0 ? "Couldn't add it: no connection. Try again in a moment." : (r.error?.detail ?? "Couldn't add the card. Please try again.");
});

// ---- loading and polling -------------------------------------------------------------------
let timer: number | undefined;
let inFlight = false;

async function load(first = false) {
  if (!token || inFlight) return;
  inFlight = true;
  try {
    const r = await api<CardState>('GET', `/api/loyalty/card/${encodeURIComponent(id)}`, { token });
    if (r.ok && r.data) return render(r.data);
    if (r.status === 404 || r.status === 410) {
      stop();
      return setState('notfound');
    }
    if (r.status === 401 || r.status === 403) {
      stop();
      forgetToken(id);
      return setState('notoken');
    }
    // A failed poll keeps the card on screen; only the first load shows the error.
    if (first || !current) {
      $('[data-error-text]').textContent =
        r.status === 0 ? "We couldn't reach the café just now. Check your connection." : 'Something went wrong on our side.';
      setState('error');
    }
  } finally {
    inFlight = false;
  }
}
function start() {
  stop();
  timer = window.setInterval(() => document.visibilityState === 'visible' && void load(), POLL_MS);
}
function stop() {
  if (timer !== undefined) window.clearInterval(timer);
  timer = undefined;
}
document.addEventListener('visibilitychange', () => {
  if (document.visibilityState === 'visible' && timer !== undefined) void load();
});
$('[data-retry]').addEventListener('click', () => {
  setState('loading');
  void load(true);
});

if (!CARD_ID_RE.test(id)) setState('notfound');
else if (!token) setState('notoken');
else {
  void load(true);
  start();
}

// ---- add to home screen --------------------------------------------------------------------
const standalone =
  matchMedia('(display-mode: standalone)').matches || (navigator as Navigator & { standalone?: boolean }).standalone === true;
const p = platform();
if (!standalone && p !== 'desktop') {
  $('[data-home]').hidden = false;
  $(p === 'ios' ? '[data-home-ios]' : '[data-home-android]').hidden = false;
}

// ---- marketing preference -------------------------------------------------------------------
const box = $<HTMLInputElement>('[data-marketing]');
const boxMsg = $('[data-marketing-msg]');
box.addEventListener('change', async () => {
  if (!token) return;
  const want = box.checked;
  box.disabled = true;
  boxMsg.textContent = 'Saving…';
  const r = await api<CardState>('PATCH', `/api/loyalty/card/${encodeURIComponent(id)}/preferences`, {
    token,
    body: { marketing_opt_in: want },
  });
  box.disabled = false;
  if (r.ok && r.data) {
    boxMsg.textContent = want ? "Done. We'll send news and offers, two a month at most." : "Done. We won't send you news or offers.";
    render(r.data);
  } else {
    box.checked = !want;
    boxMsg.textContent =
      r.status === 0 ? "Couldn't save: no connection. Try again in a moment." : "Couldn't save that. Please try again.";
  }
});

// ---- delete, with an inline confirm ------------------------------------------------------------
const delOpen = $<HTMLButtonElement>('[data-del-open]');
const delBox = $('[data-del-confirm]');
const delMsg = $('[data-del-msg]');
const toggleDel = (open: boolean) => {
  delBox.hidden = !open;
  delOpen.setAttribute('aria-expanded', String(open));
  delMsg.textContent = '';
  (open ? $<HTMLButtonElement>('[data-del-no]') : delOpen).focus();
};
delOpen.addEventListener('click', () => toggleDel(delBox.hidden === true));
$('[data-del-no]').addEventListener('click', () => toggleDel(false));
$<HTMLButtonElement>('[data-del-yes]').addEventListener('click', async (e) => {
  if (!token) return;
  const btn = e.currentTarget as HTMLButtonElement;
  btn.disabled = true;
  delMsg.textContent = '';
  const r = await api<null>('DELETE', `/api/loyalty/card/${encodeURIComponent(id)}`, { token });
  btn.disabled = false;
  if (r.ok || r.status === 404) {
    stop();
    forgetToken(id);
    cardEl.innerHTML = '';
    setState('deleted');
  } else {
    delMsg.textContent =
      r.status === 0 ? "Couldn't delete: no connection. Try again in a moment." : "Couldn't delete the card. Please try again, or ask at the till.";
  }
});

if (isMock()) console.info('[rewards] mock mode');
