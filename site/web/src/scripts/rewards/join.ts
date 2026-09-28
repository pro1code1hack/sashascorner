// /rewards: programme rules, the join form, recovery, and the wallet buttons after.
import { api, lastCard, saveToken, CARD_ID_RE } from './api';
import { esc, ordinal, PROGRAM_FALLBACK, type CardState, type JoinResult, type Program, type PublicProgram } from './card';
import { walletHtml, wireWalletTracking } from './wallet';
import { currentSrc, track } from '../track';

const $ = <T extends Element = HTMLElement>(sel: string, root: ParentNode = document) => root.querySelector<T>(sel)!;
const $$ = <T extends Element = HTMLElement>(sel: string, root: ParentNode = document) => [...root.querySelectorAll<T>(sel)];

const params = new URLSearchParams(location.search);
const ref = params.get('ref');
const refId = ref && CARD_ID_RE.test(ref) ? ref : undefined;

track('rewards_view');

// ---- programme: the numbers on the page come from the API when it answers ----------
let program: Program = PROGRAM_FALLBACK;
void api<Program>('GET', '/api/loyalty/program').then((r) => {
  if (!r.ok || !r.data) return;
  program = r.data;
  const set = (k: string, v: string) => $$(`[data-p="${k}"]`).forEach((el) => (el.textContent = v));
  set('stamps', String(program.stamps_required));
  set('ninth', ordinal(program.stamps_required + 1));
  set('per-scan', String(program.max_stamps_per_scan));
  set('reward', program.reward_text.replace(/\.$/, ''));
  $$('[data-p-birthday]').forEach((el) => (el.hidden = !program.birthday_reward));
});

// ---- phase 3: other programmes ------------------------------------------------------------
// The join form offers every other active programme as an optional tick; a device that
// already holds a card is offered the ones its member has not joined, one tap each.
let clubs: PublicProgram[] = [];
const clubLine = (p: PublicProgram) =>
  p.description ?? (p.kind === 'POINTS' ? `${p.points_per_pound ?? ''} points for every pound` : `${p.stamps_required} stamps, then ${p.reward_text.toLowerCase()}`);
void api<{ programs: PublicProgram[] }>('GET', '/api/loyalty/programs').then((r) => {
  if (!r.ok || !r.data) return;
  clubs = r.data.programs.filter((p) => !p.is_default);
  $('[data-clubs]').hidden = clubs.length === 0;
  $('[data-clubs-list]').innerHTML = clubs
    .map(
      (p) =>
        `<label class="check"><input type="checkbox" name="also_join" value="${esc(p.slug)}" /><span><strong>${esc(p.name)}</strong><small>${esc(clubLine(p))}</small></span></label>`,
    )
    .join('');
});

// ---- a card already on this device ---------------------------------------------------
const mine = lastCard();
if (mine) {
  const note = $('[data-have-card]');
  $<HTMLAnchorElement>('[data-have-card-link]').href = `/c/${mine.id}`;
  note.hidden = false;
  void api<CardState>('GET', `/api/loyalty/card/${encodeURIComponent(mine.id)}`, { token: mine.token }).then((r) => {
    const joinable = r.ok && r.data ? (r.data.joinable ?? []) : [];
    $('[data-have-clubs]').hidden = joinable.length === 0;
    $('[data-have-clubs-list]').innerHTML = joinable
      .map(
        (p) =>
          `<li><span><strong>${esc(p.name)}</strong><small>${esc(clubLine(p))}</small></span><button class="btn btn--ghost" type="button" data-club="${esc(p.slug)}">Join</button></li>`,
      )
      .join('');
  });
  $('[data-have-clubs-list]').addEventListener('click', async (e) => {
    const b = (e.target as HTMLElement).closest<HTMLButtonElement>('[data-club]');
    if (!b) return;
    const msg = $('[data-have-clubs-msg]');
    b.disabled = true;
    msg.textContent = 'Adding the card…';
    const r = await api<JoinResult>('POST', `/api/loyalty/card/${encodeURIComponent(mine.id)}/programs`, {
      token: mine.token,
      body: { program: b.dataset.club },
    });
    b.disabled = false;
    if (r.ok && r.data) {
      saveToken(r.data.card_id, r.data.token);
      location.assign(r.data.web_card_url);
      return;
    }
    msg.textContent = r.status === 0 ? "Couldn't add it: no connection. Try again in a moment." : (r.error?.detail ?? "Couldn't add the card. Please try again.");
  });
}

// ---- panels ----------------------------------------------------------------------------
const panels = {
  join: $<HTMLFormElement>('[data-panel="join"]'),
  done: $('[data-panel="done"]'),
  recover: $('[data-panel="recover"]'),
};
function show(which: keyof typeof panels, focus = true) {
  for (const [k, el] of Object.entries(panels)) el.hidden = k !== which;
  $('#join').dataset.panel = which;
  if (focus) {
    const el = panels[which];
    const target = which === 'join' ? $<HTMLInputElement>('input[name="first_name"]', el) : el;
    target.focus({ preventScroll: true });
    $('#join').scrollIntoView({ block: 'start', behavior: matchMedia('(prefers-reduced-motion: reduce)').matches ? 'auto' : 'smooth' });
  }
}

function fieldError(form: HTMLElement, name: string, msg: string) {
  const el = $(`[data-err="${name}"]`, form);
  el.textContent = msg;
  const input = form.querySelector<HTMLInputElement>(`[aria-describedby~="err-${name}"]`);
  input?.setAttribute('aria-invalid', msg ? 'true' : 'false');
}
function formError(form: HTMLElement, msg: string | null) {
  const el = $('[data-error]', form);
  el.textContent = msg ?? '';
  el.hidden = !msg;
}
const EMAIL_RE = /^[^\s@]+@[^\s@]+\.[^\s@]+$/;
const phoneDigits = (s: string) => s.replace(/[^\d]/g, '');
const looksLikePhone = (s: string) => /^\+?[\d\s()-]{7,20}$/.test(s.trim()) && phoneDigits(s).length >= 10 && phoneDigits(s).length <= 15;

function commonError(status: number, detail?: string): string {
  if (status === 0) return "Couldn't reach the café. Check your connection and try again.";
  if (status === 429) return 'Too many tries from this connection. Please wait a few minutes and try again.';
  return detail || 'Something went wrong on our side. Please try again, or ask at the till.';
}

// ---- join --------------------------------------------------------------------------------
const form = panels.join;
const contact = $<HTMLInputElement>('input[name="contact"]', form);
const contactLabel = $('[data-contact-label]', form);
function setVia(via: 'email' | 'phone') {
  const email = via === 'email';
  contactLabel.textContent = email ? 'Email address' : 'Mobile number';
  contact.type = email ? 'email' : 'tel';
  contact.inputMode = email ? 'email' : 'tel';
  contact.autocomplete = email ? 'email' : 'tel';
  contact.maxLength = email ? 254 : 20;
  contact.value = '';
  fieldError(form, 'contact', '');
}
form.addEventListener('change', (e) => {
  const t = e.target as HTMLInputElement;
  if (t.name === 'via') {
    setVia(t.value as 'email' | 'phone');
    contact.focus();
  }
});

function validate(): Record<string, unknown> | null {
  const f = new FormData(form);
  let ok = true;
  const first = String(f.get('first_name') ?? '').trim();
  fieldError(form, 'first_name', first ? '' : 'Please add your first name.');
  if (!first) ok = false;

  const via = f.get('via') === 'phone' ? 'phone' : 'email';
  const c = String(f.get('contact') ?? '').trim();
  let cErr = '';
  if (!c) cErr = via === 'email' ? 'Please add your email address.' : 'Please add your mobile number.';
  else if (via === 'email' && !EMAIL_RE.test(c)) cErr = "That email address doesn't look right.";
  else if (via === 'phone' && !looksLikePhone(c)) cErr = "That number doesn't look right. A UK mobile starts 07.";
  fieldError(form, 'contact', cErr);
  if (cErr) ok = false;

  const day = Number(f.get('birthday_day') || 0);
  const month = Number(f.get('birthday_month') || 0);
  let bErr = '';
  if ((day && !month) || (!day && month)) bErr = 'Please pick both the day and the month, or leave both empty.';
  else if (day && month && day > new Date(Date.UTC(2024, month, 0)).getUTCDate()) bErr = "That month doesn't have that many days.";
  $('[data-err="birthday"]', form).textContent = bErr;
  if (bErr) ok = false;

  const terms = f.get('terms') === 'on';
  fieldError(form, 'terms', terms ? '' : 'Please agree to the card rules to get a card.');
  if (!terms) ok = false;

  if (!ok) return null;
  return {
    first_name: first,
    ...(via === 'email' ? { email: c } : { phone: c }),
    ...(day && month ? { birthday_day: day, birthday_month: month } : {}),
    terms: true,
    marketing_opt_in: f.get('marketing_opt_in') === 'on',
    ...(currentSrc() ? { src: currentSrc() } : {}),
    ...(refId ? { ref: refId } : {}),
    ...(f.getAll('also_join').length ? { also_join: f.getAll('also_join').map(String) } : {}),
    website: String(f.get('website') ?? ''),
  };
}

form.addEventListener('submit', async (e) => {
  e.preventDefault();
  formError(form, null);
  const body = validate();
  if (!body) {
    form.querySelector<HTMLElement>('[aria-invalid="true"], .ferr:not(:empty)')?.closest('label, fieldset')?.querySelector<HTMLElement>('input, select')?.focus();
    return;
  }
  const btn = $<HTMLButtonElement>('[data-submit]', form);
  btn.disabled = true;
  btn.classList.add('is-busy');
  try {
    const r = await api<JoinResult>('POST', '/api/loyalty/join', { body });
    if (r.ok && r.data) {
      track('join_submit');
      done(r.data, String(body.first_name), 'join');
      return;
    }
    if (r.status === 409 && r.error?.error === 'already_member') {
      openRecover(String(body.email ?? body.phone), 'You already have a card with that ' + (body.email ? 'email address' : 'number') + '. We can send you a code to get it back on this phone.');
      return;
    }
    if (r.status === 422) {
      formError(form, r.error?.detail || 'Please check the form and try again.');
      return;
    }
    formError(form, commonError(r.status, r.error?.detail));
  } finally {
    btn.disabled = false;
    btn.classList.remove('is-busy');
  }
});

// ---- after join / recovery: the wallet buttons -----------------------------------------------
const walletRoot = $('[data-wallet-root]');
wireWalletTracking(walletRoot, 'rewards');

function done(j: JoinResult, firstName: string | null, how: 'join' | 'recover') {
  saveToken(j.card_id, j.token);
  $('[data-done-title]').textContent =
    how === 'recover' ? 'Found it. Here is your card.' : firstName ? `Your card is ready, ${firstName}.` : 'Your card is ready.';
  $('[data-done-lede]').textContent =
    how === 'recover'
      ? 'Same card, same stamps. Save it again on this phone.'
      : "Save it somewhere you'll find it at the till. The first stamp comes with your next drink.";
  walletRoot.innerHTML = walletHtml({
    apple: j.apple_pass_url,
    google: j.google_save_url,
    webCard: j.web_card_url,
    phoneLink: j.web_card_url,
  });
  $('[data-have-card]').hidden = true;
  $('[data-have-clubs]').hidden = true;
  const extra = j.extra_cards ?? [];
  for (const x of extra) saveToken(x.card_id, x.token);
  // The main card stays this device's "last card": save it again after the extras.
  if (extra.length) saveToken(j.card_id, j.token);
  const extraEl = $('[data-done-extra]');
  extraEl.hidden = extra.length === 0;
  extraEl.innerHTML = extra
    .map((x) => {
      const name = clubs.find((p) => p.slug === x.program_slug)?.name ?? x.program_slug;
      return `<li>Your ${esc(name)} card: <a href="${esc(x.web_card_url)}">open it</a> to save it too.</li>`;
    })
    .join('');
  show('done');
}

// ---- recovery -------------------------------------------------------------------------------
const rec = panels.recover;
const stepContact = $<HTMLFormElement>('[data-step="contact"]', rec);
const stepCode = $<HTMLFormElement>('[data-step="code"]', rec);
const stepStaff = $('[data-step="staff"]', rec);
const recContact = $<HTMLInputElement>('input[name="contact"]', stepContact);
let pendingContact = '';

function recoverStep(which: 'contact' | 'code' | 'staff') {
  stepContact.hidden = which !== 'contact';
  stepCode.hidden = which !== 'code';
  stepStaff.hidden = which !== 'staff';
}

function openRecover(prefill?: string, note?: string) {
  recoverStep('contact');
  formError(stepContact, null);
  fieldError(stepContact, 'rcontact', '');
  if (prefill) recContact.value = prefill;
  const n = $('[data-recover-note]', rec);
  n.textContent = note ?? '';
  n.hidden = !note;
  show('recover');
  if (!note) recContact.focus({ preventScroll: true });
}

$$('[data-open-recover]').forEach((b) =>
  b.addEventListener('click', (e) => {
    e.preventDefault();
    openRecover();
  }),
);
$$('[data-open-join]').forEach((b) => b.addEventListener('click', () => show('join')));
$$('[data-restart]', rec).forEach((b) =>
  b.addEventListener('click', () => {
    recoverStep('contact');
    recContact.focus();
  }),
);
if (location.hash === '#recover') openRecover();

stepContact.addEventListener('submit', async (e) => {
  e.preventDefault();
  formError(stepContact, null);
  const c = recContact.value.trim();
  if (!EMAIL_RE.test(c) && !looksLikePhone(c)) {
    fieldError(stepContact, 'rcontact', 'Enter the email address or mobile number you joined with.');
    recContact.focus();
    return;
  }
  fieldError(stepContact, 'rcontact', '');
  const btn = $<HTMLButtonElement>('button[type="submit"]', stepContact);
  btn.disabled = true;
  try {
    const r = await api<{ delivery: 'email' | 'sms' | 'ask_staff' }>('POST', '/api/loyalty/recover', { body: { contact: c } });
    if (!r.ok || !r.data) {
      formError(stepContact, commonError(r.status, r.error?.detail));
      return;
    }
    pendingContact = c;
    if (r.data.delivery === 'ask_staff') {
      recoverStep('staff');
      stepStaff.focus?.();
      return;
    }
    $('[data-code-sent]', stepCode).textContent =
      r.data.delivery === 'sms'
        ? 'If that number has a card, we have just texted it a code. It lasts 10 minutes.'
        : 'If that email address has a card, we have just emailed it a code. It lasts 10 minutes; check your junk folder too.';
    formError(stepCode, null);
    fieldError(stepCode, 'code', '');
    $<HTMLInputElement>('input[name="code"]', stepCode).value = '';
    recoverStep('code');
    $<HTMLInputElement>('input[name="code"]', stepCode).focus();
  } finally {
    btn.disabled = false;
  }
});

stepCode.addEventListener('submit', async (e) => {
  e.preventDefault();
  formError(stepCode, null);
  const input = $<HTMLInputElement>('input[name="code"]', stepCode);
  const code = input.value.replace(/\s/g, '');
  if (!/^\d{4,8}$/.test(code)) {
    fieldError(stepCode, 'code', 'Enter the code from the message: numbers only.');
    input.focus();
    return;
  }
  fieldError(stepCode, 'code', '');
  const btn = $<HTMLButtonElement>('button[type="submit"]', stepCode);
  btn.disabled = true;
  try {
    const r = await api<JoinResult>('POST', '/api/loyalty/recover/verify', { body: { contact: pendingContact, code } });
    if (r.ok && r.data) {
      done(r.data, null, 'recover');
      return;
    }
    if (r.status === 400) {
      fieldError(stepCode, 'code', "That code didn't work. It may have expired: codes last 10 minutes.");
      input.focus();
      return;
    }
    formError(stepCode, commonError(r.status, r.error?.detail));
  } finally {
    btn.disabled = false;
  }
});
