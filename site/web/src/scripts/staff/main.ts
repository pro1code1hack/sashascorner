// Staff scanner controller: views, flows and timers. CONTRACT §5 is the API;
// SPEC.md flows 2-6 are the behaviour.
//
// Views: pair -> pin -> scan (default) | convert | find, and card after a scan.
// One rule throughout: nothing is done "later". A call that fails shows why and
// leaves the card as the server last said it was; there is no offline queue.
//
// Phase 3 (CONTRACT "Phase 3"): a scan answers with the scanned card AND the member's
// other cards (`other_cards`); the card view shows a tab per card and acts on the
// selected one. A points card takes a spend instead of +1/+2/+3. A ready reward with
// several catalogue entries asks which one first, then lists only what it covers.

import { api, errorText, StaffApiError } from './api';
import { createScanner, cameraProblemText, type Scanner } from './scanner';
import { device, isMockMode, recents, session } from './store';
import type { Drink, DrinksResult, LoginResult, LookupMember, MeResult, PairResult, PosCustomer, RedeemResult, Reward, RewardOption, ScanResult, StampResult, UndoResult } from './types';
import { $, announce, fmt, h, pinPad, showMsg, type PinPad } from './ui';
import { stickerImg, stickerSrc } from '../rewards/stickers';

type View = 'boot' | 'pair' | 'pin' | 'scan' | 'convert' | 'card' | 'find';

interface UndoTarget {
  kind: 'event' | 'reward';
  id: number;
  until: number;
  cardId: string;
  text: string; // "+1 stamp for Olena"
}

const UNDO_FALLBACK_MS = 2 * 60_000;
const RECOVERY_SHOW_MS = 2 * 60_000;
const REJECT_SAME_CODE_MS = 3000;

const st = {
  view: 'boot' as View,
  card: null as ScanResult | null,
  /** Every card of the member on screen (phase 3), main card first. */
  cards: [] as ScanResult[],
  convertN: 0, // stickers chosen for a paper-card conversion; 0 = normal scanning
  undo: null as UndoTarget | null,
  /** Picker lists, keyed by what they are for ("all", "o<option id>", "p<programme>"). */
  drinks: new Map<string, DrinksResult>(),
  scanning: false, // a scan request is in flight
  lastReject: { text: '', at: 0 },
  expiryTimer: 0,
  tick: 0,
  recoveryUntil: 0,
};

let scanner: Scanner;
let pin: PinPad;
let mgrPin: PinPad;

// ---- views ---------------------------------------------------------------------

function show(view: View, opts: { focus?: boolean } = {}): void {
  st.view = view;
  document.body.dataset.view = view;
  document.querySelectorAll<HTMLElement>('.st-main > [data-view]').forEach((el) => (el.hidden = el.dataset.view !== view));
  const signed = view !== 'pair' && view !== 'pin' && view !== 'boot';
  document.body.dataset.signed = signed ? '1' : '0';
  $('[data-tabs]').hidden = !signed;
  $('[data-who]').hidden = !signed;
  const tab = view === 'card' ? (st.convertN ? 'convert' : 'scan') : view;
  document.querySelectorAll<HTMLElement>('[data-tab]').forEach((t) => {
    if (t.dataset.tab === tab) t.setAttribute('aria-current', 'page');
    else t.removeAttribute('aria-current');
  });

  // Camera: live on scan; paused (stream kept, so the next scan is instant) on the
  // card; off everywhere else.
  if (view === 'scan') void startCamera();
  else if (view === 'card') scanner.pause();
  else scanner.stop();

  if (view === 'scan') renderConvertBanner();
  if (view !== 'find') hideRecovery();
  if (opts.focus !== false) {
    // Not a card tab: the tab row uses roving tabindex, so its unselected tabs are -1 too.
    const target = document.querySelector<HTMLElement>(`.st-main > [data-view="${view}"] [tabindex="-1"]:not([role="tab"])`);
    target?.focus({ preventScroll: true });
  }
  window.scrollTo({ top: 0 });
}

// ---- session ------------------------------------------------------------------

function applySession(s: { user: { name: string; role: string }; expires_at: string }): void {
  $('[data-who-name]').textContent = s.user.name;
  const role = s.user.role.toLowerCase();
  const until = fmt.time(s.expires_at);
  $('[data-who-meta]').textContent = `${role === 'staff' ? '' : `${role[0].toUpperCase()}${role.slice(1)} · `}until ${until}`;
  window.clearTimeout(st.expiryTimer);
  const ms = Date.parse(s.expires_at) - Date.now();
  st.expiryTimer = window.setTimeout(() => endSession('Your 12-hour session has ended. Enter your PIN to carry on.'), Math.min(Math.max(ms, 0), 2 ** 31 - 1));
}

function endSession(note: string | null): void {
  session.clear();
  window.clearTimeout(st.expiryTimer);
  closeDialogs();
  st.card = null;
  st.cards = [];
  st.convertN = 0;
  pin.clear();
  showMsg($('[data-pin-note]'), note);
  showMsg($('[data-pin-err]'), null);
  show('pin');
}

function forgetDevice(note: string | null): void {
  device.clear();
  session.clear();
  window.clearTimeout(st.expiryTimer);
  closeDialogs();
  showMsg($('[data-pair-err]'), note);
  show('pair');
}

async function signIn(value: string): Promise<void> {
  const go = $<HTMLButtonElement>('[data-pin-go]');
  go.disabled = true;
  showMsg($('[data-pin-err]'), null);
  try {
    const r = await api.post<LoginResult>('/api/staff/login', { pin: value }, { auth: false });
    session.set({ token: r.session_token, expires_at: r.expires_at, user: r.user });
    applySession(r);
    pin.clear();
    showMsg($('[data-pin-note]'), null);
    announce(`Signed in as ${r.user.name}`);
    show('scan');
  } catch (e) {
    pin.clear();
    showMsg($('[data-pin-err]'), errorText(e));
    shake($('.st-pinview .st-dots'));
  } finally {
    go.disabled = pin.value().length < 4;
  }
}

async function signOut(): Promise<void> {
  try {
    await api.post('/api/staff/logout');
  } catch {
    /* signed out here either way */
  }
  endSession(null);
}

function shake(el: HTMLElement): void {
  el.classList.remove('is-shake');
  void el.offsetWidth;
  el.classList.add('is-shake');
}

// ---- camera + scanning -----------------------------------------------------------

async function startCamera(): Promise<void> {
  const msg = $('[data-camera-msg]');
  if (scanner.running) {
    scanner.resume();
    msg.hidden = true;
    return;
  }
  await scanner.start();
  if (scanner.running) {
    msg.hidden = true;
    $('[data-torch]').hidden = !scanner.torchAvailable;
  }
}

function cameraProblem(p: Parameters<typeof cameraProblemText>[0]): void {
  $('[data-camera-text]').textContent = cameraProblemText(p);
  $('[data-camera-msg]').hidden = false;
  $('[data-torch]').hidden = true;
}

function onCode(text: string): void {
  if (st.view !== 'scan' || st.scanning) return;
  if (text === st.lastReject.text && Date.now() - st.lastReject.at < REJECT_SAME_CODE_MS) return;
  void handlePayload(text);
}

function reject(title: string, detail: string, text: string): void {
  st.lastReject = { text, at: Date.now() };
  $('[data-reject-title]').textContent = title;
  $('[data-reject-text]').textContent = detail;
  $('[data-reject]').hidden = false;
  const cam = $('[data-camera]');
  cam.classList.add('is-reject');
  window.setTimeout(() => cam.classList.remove('is-reject'), 1500);
  announce(`${title}. ${detail}`, true);
}

async function handlePayload(raw: string): Promise<void> {
  const text = raw.trim();
  if (!text) return;
  $('[data-reject]').hidden = true;
  if (!/^SC1:/i.test(text)) {
    reject('Not a rewards card', "This QR code isn't a Sasha's Corner Rewards card. Ask the customer to open their card.", text);
    return;
  }
  st.scanning = true;
  scanner.pause();
  $('[data-checking]').hidden = false;
  try {
    const card = await api.post<ScanResult>('/api/staff/scan', { payload: text });
    ($('[data-manual]') as HTMLFormElement).reset();
    if (st.convertN) await migrate(card);
    else openCard(card);
  } catch (e) {
    if (e instanceof StaffApiError && e.code === 'bad_signature') reject('Card rejected', e.message, text);
    else if (e instanceof StaffApiError && e.code === 'unknown_card') reject('Card not found', e.message, text);
    else if (e instanceof StaffApiError && e.offline) reject('No connection', e.message, text);
    else if (!(e instanceof StaffApiError && e.status === 401)) reject("Couldn't check the card", errorText(e), text);
  } finally {
    st.scanning = false;
    $('[data-checking]').hidden = true;
    if (st.view === 'scan') scanner.resume();
  }
}

function renderConvertBanner(): void {
  $('[data-convert-banner]').hidden = !st.convertN;
  $('[data-convert-n]').textContent = fmt.plural(st.convertN, 'sticker');
  $('[data-camera-hint]').textContent = st.convertN ? "Scan the customer's new card" : "Hold the card's QR code inside the frame";
}

// ---- card ------------------------------------------------------------------------

/** The member's cards from one API answer: the card it is about, plus its siblings. */
function setMember(card: ScanResult): void {
  const all = [{ ...card, other_cards: [] }, ...(card.other_cards ?? [])];
  const main = all.filter((c) => (c.program_slug ?? 'stamp') === 'stamp');
  st.cards = [...main, ...all.filter((c) => (c.program_slug ?? 'stamp') !== 'stamp')];
  st.card = card;
}

const isPoints = (c: ScanResult) => c.program_kind === 'POINTS';
const unitWord = (c: ScanResult, n: number) => (isPoints(c) ? fmt.plural(n, 'point') : fmt.plural(n, 'stamp'));
/** "Free matcha ready" -> "free matcha"; the main card's reads "free drink". */
function rewardThing(c: ScanResult): string {
  const label = c.reward_ready_label || c.rewards.find((r) => r.kind === 'STAMP_CARD')?.label || 'Free drink ready';
  return /\sready$/i.test(label) ? label.replace(/\sready$/i, '').toLowerCase() : 'reward';
}

function openCard(card: ScanResult): void {
  setMember(card);
  showMsg($('[data-card-err]'), null);
  setActionMode('stamp');
  renderCard(card, 0);
  show('card');
  const main = document.querySelector<HTMLButtonElement>('[data-stamp="1"]');
  if (!card.voided && main && !card.rewards.length && !isPoints(card)) main.focus({ preventScroll: true });
  const others = st.cards.length > 1 ? ` ${fmt.plural(st.cards.length, 'card')}.` : '';
  announce(`${card.first_name}: ${card.stamps_current} of ${card.stamps_required} ${isPoints(card) ? 'points' : 'stamps'}${card.rewards.length ? `, ${fmt.plural(card.rewards.length, 'reward')} ready` : ''}.${others}`);
}

function selectCard(cardId: string): void {
  const next = st.cards.find((c) => c.card_id === cardId);
  if (!next || next.card_id === st.card?.card_id) return;
  st.card = next;
  showMsg($('[data-card-err]'), null);
  setActionMode('stamp');
  renderCard(next, 0);
  document.querySelector<HTMLButtonElement>(`[data-cardtab="${cardId}"]`)?.focus({ preventScroll: true });
  announce(`${next.program_name || 'Card'}: ${next.stamps_current} of ${next.stamps_required}.`);
}

function renderTabs(card: ScanResult): void {
  const bar = $('[data-cardtabs]');
  bar.hidden = st.cards.length < 2;
  if (bar.hidden) return bar.replaceChildren();
  bar.replaceChildren(
    ...st.cards.map((c) => {
      const on = c.card_id === card.card_id;
      const ready = c.rewards.length > 0;
      return h(
        'button',
        {
          type: 'button',
          role: 'tab',
          class: `st-cardtab${ready ? ' has-reward' : ''}`,
          'aria-selected': String(on),
          tabindex: on ? '0' : '-1',
          'data-cardtab': c.card_id,
          onclick: () => selectCard(c.card_id),
        },
        h('span', { class: 'st-cardtab-name' }, (c.program_slug ?? 'stamp') === 'stamp' ? 'Main card' : c.program_name || 'Card'),
        h('span', { class: 'st-cardtab-count num' }, `${c.stamps_current}/${c.stamps_required}`),
        ready ? h('span', { class: 'st-cardtab-dot' }, 'Reward') : null,
      );
    }),
  );
}

function renderCard(card: ScanResult, justAdded: number): void {
  // Keep the tab list in step with the card just answered (a stamp changes its count).
  st.cards = st.cards.map((c) => (c.card_id === card.card_id ? { ...card, other_cards: [] } : c));
  renderTabs(card);
  const req = card.stamps_required;
  const points = isPoints(card);
  $('[data-pass]').classList.toggle('is-void', card.voided);
  $('[data-pass]').classList.toggle('is-points', points);
  const prog = $('[data-card-prog]');
  prog.hidden = !card.program_name || (card.program_slug ?? 'stamp') === 'stamp';
  prog.textContent = card.program_name ?? '';
  $('[data-card-name]').textContent = card.first_name || 'Deleted card';
  $('[data-card-since]').textContent = `Member since ${fmt.since(card.member_since)}`;
  $('[data-card-count]').textContent = `${card.stamps_current}/${req}`;
  const left = Math.max(0, req - card.stamps_current);
  $('[data-card-left]').textContent = card.voided
    ? points
      ? 'points'
      : 'stamps'
    : `${points ? 'points' : 'stamps'} · ${left} more to a ${rewardThing(card)}`;
  $('[data-card-void]').hidden = !card.voided;
  // A reward waiting: the reward sticker on the card, as on the customer's.
  const prize = !card.voided && card.rewards.some((r) => r.kind === 'STAMP_CARD');
  $('[data-pass]').classList.toggle('has-prize', prize);
  $('[data-card-prize]').hidden = !prize;

  // A points card shows its progress on eight slots (the target is far past what a
  // row of stickers can hold), the same way its wallet pass does.
  const slots = points || req > 20 ? 8 : req;
  const filled = points || req > 20 ? Math.min(8, Math.floor((card.stamps_current * 8) / Math.max(req, 1))) : card.stamps_current;
  const grid = $('[data-stamps]');
  grid.style.setProperty('--n', String(Math.min(Math.max(slots, 1), 10)));
  grid.replaceChildren();
  for (let i = 0; i < slots; i++) {
    const on = i < filled;
    const li = h('li', { class: `st-stamp${on ? ' is-on' : ''}${on && !points && i >= filled - justAdded ? ' is-new' : ''}` });
    // The same sticker per slot as the customer's card and wallet pass (../rewards/stickers).
    if (on) li.innerHTML = stickerImg(stickerSrc(i), 'st-stampart');
    else li.textContent = points ? '' : String(i + 1);
    grid.append(li);
  }

  const facts = $('[data-card-facts]');
  facts.replaceChildren();
  if (!points && card.stamps_last_10_min > 0) {
    facts.append(h('li', { class: card.stamps_last_10_min >= 3 ? 'is-warn' : '' }, `${fmt.plural(card.stamps_last_10_min, 'stamp')} on this card in the last 10 minutes${card.stamps_last_10_min >= 3 ? '. More needs a manager PIN.' : '.'}`));
  }
  if (card.last_event) {
    const e = card.last_event;
    const what = e.reason === 'UNDO' ? 'Undo' : e.reason === 'PAPER_MIGRATION' ? `Paper card +${e.delta}` : e.reason === 'MANUAL_FIX' ? `Correction ${e.delta > 0 ? '+' : ''}${e.delta}` : `${e.delta > 0 ? '+' : ''}${e.delta}`;
    const who = e.staff_name ? ` by ${e.staff_name}` : e.reason === 'PURCHASE' ? ' from a till receipt' : '';
    facts.append(h('li', {}, `Last: ${what}${who}, ${fmt.ago(e.at)}`));
  }
  if (card.lightspeed_linked) facts.append(h('li', {}, `Linked to the till: receipts with this customer attached add ${points ? 'points' : 'stamps'} by themselves.`));

  const rewards = card.voided ? [] : card.rewards;
  $('[data-rewards-wrap]').hidden = rewards.length === 0;
  const list = $('[data-rewards]');
  list.replaceChildren(
    ...rewards.map((r) =>
      h(
        'li',
        {},
        h(
          'button',
          { type: 'button', class: 'st-reward', onclick: () => openReward(r) },
          h('span', { class: 'st-reward-text' }, h('span', { class: 'st-reward-label' }, r.label), h('span', { class: 'st-reward-sub' }, rewardSub(r))),
          h('span', { class: 'st-reward-go' }, 'Redeem'),
        ),
      ),
    ),
  );
  document.querySelectorAll<HTMLButtonElement>('[data-stamp]').forEach((b) => {
    b.disabled = card.voided;
    b.hidden = Number(b.dataset.stamp) > (card.max_stamps_per_scan ?? 3);
  });
  renderPointsBox(card);
  renderMore(card);
}

function renderPointsBox(card: ScanResult): void {
  const points = isPoints(card) && !card.voided;
  const box = $<HTMLFormElement>('[data-points-actions]');
  box.dataset.card = card.card_id;
  if (!points) return;
  const input = $<HTMLInputElement>('[data-spend]');
  if (box.dataset.for !== card.card_id) {
    input.value = '';
    box.dataset.for = card.card_id;
  }
  updateSpendPreview();
}

/** "4.5" -> 450. Integer pence only: no float ever touches the money. */
function parsePence(raw: string): number | null {
  const m = /^\s*£?\s*(\d{1,4})(?:[.,](\d{1,2}))?\s*$/.exec(raw);
  if (!m) return null;
  const pence = Number(m[1]) * 100 + Number((m[2] ?? '0').padEnd(2, '0'));
  return pence > 0 ? pence : null;
}

function updateSpendPreview(): void {
  const card = st.card;
  if (!card || !isPoints(card)) return;
  const rate = card.points_per_pound ?? 0;
  const raw = $<HTMLInputElement>('[data-spend]').value;
  const pence = parsePence(raw);
  const pts = pence === null ? 0 : Math.floor((pence * rate) / 100);
  $<HTMLButtonElement>('[data-spend-go]').disabled = pence === null || pts < 1;
  $('[data-spend-note]').textContent =
    pence === null
      ? raw.trim()
        ? 'Type the amount in pounds and pence, like 4.50.'
        : `${rate} points for every pound. Enter the total they paid.`
      : `${fmt.gbp(pence)} earns ${unitWord(card, pts)}${pence > 5000 ? '. Over £50 needs a manager PIN.' : '.'}`;
}

function renderMore(card: ScanResult): void {
  const btns: HTMLElement[] = [];
  if (!card.voided) {
    for (const p of card.joinable ?? []) {
      btns.push(h('button', { type: 'button', class: 'btn btn--ghost st-wide', onclick: () => void addCard(p.slug, p.name) }, `Add a ${p.name} card`));
    }
    if (!card.lightspeed_linked) {
      btns.push(h('button', { type: 'button', class: 'btn btn--ghost st-wide', 'data-open-link': '1', onclick: () => void openLink() }, 'Link to their till customer'));
    }
  }
  $('[data-more]').hidden = btns.length === 0;
  $('[data-more-btns]').replaceChildren(...btns);
}

async function addCard(slug: string, name: string): Promise<void> {
  const card = st.card;
  if (!card) return;
  showMsg($('[data-card-err]'), null);
  try {
    const r = await api.post<UndoResult>('/api/staff/add-card', { card_id: card.card_id, program: slug });
    setMember(r.card);
    renderCard(r.card, 0);
    showDone(`${name} card added`, `${r.card.first_name} now has a ${name} card. It shows next to their other cards on their phone.`, null);
  } catch (e) {
    showMsg($('[data-card-err]'), errorText(e));
  }
}

async function spend(pence: number, managerPin?: string): Promise<void> {
  const card = st.card;
  if (!card) return;
  const go = $<HTMLButtonElement>('[data-spend-go]');
  go.disabled = true;
  showMsg($('[data-card-err]'), null);
  try {
    const body: Record<string, unknown> = { card_id: card.card_id, spend_pence: pence };
    if (managerPin) body.manager_pin = managerPin;
    const r = await api.post<StampResult>('/api/staff/spend', body);
    closeDialog('manager');
    setMember(r.card);
    renderCard(r.card, 0);
    $<HTMLInputElement>('[data-spend]').value = '';
    const added = r.card.last_event?.delta ?? 0;
    const title = `+${unitWord(r.card, added)} for ${r.card.first_name}`;
    const sub = r.reward_issued
      ? `That reaches ${r.card.stamps_required}: a ${rewardThing(r.card)} is ready.`
      : `${fmt.gbp(pence)} spent. ${r.card.stamps_current} of ${r.card.stamps_required}; ${r.card.stamps_required - r.card.stamps_current} more to a ${rewardThing(r.card)}.`;
    showDone(r.reward_issued ? r.card.rewards[0]?.label ?? 'Reward ready' : title, r.reward_issued ? `${title}. ${sub}` : sub, { kind: 'event', id: r.event_id, until: undoUntil(r.undo_until), cardId: r.card.card_id, text: title }, r.reward_issued);
  } catch (e) {
    if (e instanceof StaffApiError && e.code === 'manager_pin_required') {
      askManager(e.message, (p) => spend(pence, p));
    } else if (managerPin && e instanceof StaffApiError && /pin/i.test(e.code)) {
      mgrPin.clear();
      showMsg($('[data-mgr-err]'), e.message);
      shake($('[data-dlg="manager"] .st-dots'));
    } else {
      closeDialog('manager');
      showMsg($('[data-card-err]'), errorText(e));
    }
  } finally {
    updateSpendPreview();
  }
}

/** What the reward is, as a noun: "Jamie's free drink" reads; "Free drink ready" doesn't. */
function rewardName(r: Reward): string {
  if (r.kind === 'STAMP_CARD') return st.card ? rewardThing(st.card) : 'free drink';
  if (r.kind === 'BIRTHDAY') return 'birthday drink';
  if (r.kind === 'REFERRAL') return 'referral reward';
  return r.label.toLowerCase();
}

function rewardSub(r: Reward): string {
  const opts = st.card?.reward_options ?? [];
  const main = opts.length > 1 ? `${opts.map((o) => o.name).join(' or ')}` : opts[0]?.covers ? `Covers ${opts[0].covers}` : 'Any drink, on us';
  const kind = r.kind === 'BIRTHDAY' ? 'Birthday reward, separate from stamps' : r.kind === 'REFERRAL' ? 'Referral reward' : main;
  return r.expires_at ? `${kind} · until ${fmt.day(r.expires_at)}` : kind;
}

function setActionMode(mode: 'stamp' | 'done'): void {
  const points = !!st.card && isPoints(st.card);
  $('[data-stamp-actions]').hidden = mode !== 'stamp' || !!st.card?.voided || points;
  $('[data-points-actions]').hidden = mode !== 'stamp' || !!st.card?.voided || !points;
  $('[data-done]').hidden = mode !== 'done';
  $('[data-back]').hidden = mode === 'done';
}

function showDone(title: string, sub: string, undo: UndoTarget | null, isReward = false): void {
  $('[data-done-title]').textContent = title;
  $('[data-done-sub]').textContent = sub;
  $('[data-done]').classList.toggle('is-reward', isReward);
  setUndo(undo);
  setActionMode('done');
  $<HTMLButtonElement>('[data-next]').focus({ preventScroll: true });
  $('[data-done]').scrollIntoView({ block: 'nearest', behavior: 'smooth' });
  announce(`${title}. ${sub}`);
}

function undoUntil(iso: string | undefined): number {
  const t = iso ? Date.parse(iso) : NaN;
  return Number.isFinite(t) ? t : Date.now() + UNDO_FALLBACK_MS;
}

async function stamp(delta: number, managerPin?: string): Promise<void> {
  const card = st.card;
  if (!card) return;
  const btns = document.querySelectorAll<HTMLButtonElement>('[data-stamp]');
  btns.forEach((b) => (b.disabled = true));
  showMsg($('[data-card-err]'), null);
  try {
    const body: Record<string, unknown> = { card_id: card.card_id, delta };
    if (managerPin) body.manager_pin = managerPin;
    const r = await api.post<StampResult>('/api/staff/stamp', body);
    closeDialog('manager');
    setMember(r.card);
    renderCard(r.card, r.reward_issued ? 0 : delta);
    const title = `+${fmt.plural(delta, 'stamp')} for ${r.card.first_name}${st.cards.length > 1 && r.card.program_name ? ` (${r.card.program_name})` : ''}`;
    const sub = r.reward_issued
      ? `That completes the card: a ${rewardThing(r.card)} is ready. They can have it now or next time.`
      : `${r.card.stamps_current} of ${r.card.stamps_required}. ${r.card.stamps_required - r.card.stamps_current} more to a ${rewardThing(r.card)}.`;
    showDone(r.reward_issued ? (r.card.rewards.find((x) => x.kind === 'STAMP_CARD')?.label ?? 'Free drink ready') : title, r.reward_issued ? `${title}. ${sub}` : sub, { kind: 'event', id: r.event_id, until: undoUntil(r.undo_until), cardId: r.card.card_id, text: title }, r.reward_issued);
  } catch (e) {
    if (e instanceof StaffApiError && e.code === 'manager_pin_required') {
      askManager(e.message, (p) => stamp(delta, p));
    } else if (managerPin && e instanceof StaffApiError && /pin/i.test(e.code)) {
      mgrPin.clear();
      showMsg($('[data-mgr-err]'), e.message);
      shake($('[data-dlg="manager"] .st-dots'));
    } else {
      closeDialog('manager');
      showMsg($('[data-card-err]'), errorText(e));
    }
  } finally {
    btns.forEach((b) => (b.disabled = !!st.card?.voided));
  }
}

async function migrate(card: ScanResult): Promise<void> {
  const n = st.convertN;
  try {
    const r = await api.post<StampResult>('/api/staff/migrate', { card_id: card.card_id, paper_stamps: n });
    st.convertN = 0;
    setMember(r.card);
    showMsg($('[data-card-err]'), null);
    renderCard(r.card, r.reward_issued ? 0 : n);
    show('card');
    const title = `${fmt.plural(n, 'paper stamp')} added for ${r.card.first_name}`;
    showDone(
      title,
      `${r.reward_issued ? 'That completes the card: a free drink is ready. ' : `Now ${r.card.stamps_current} of ${r.card.stamps_required}. `}Keep the paper card so it isn't used again.`,
      { kind: 'event', id: r.event_id, until: undoUntil(r.undo_until), cardId: r.card.card_id, text: title },
      r.reward_issued,
    );
    resetStickers();
  } catch (e) {
    if (e instanceof StaffApiError && e.code === 'already_migrated') {
      st.convertN = 0;
      resetStickers();
      openCard(card);
      showMsg($('[data-card-err]'), `${e.message} No stamps were added.`);
      $('[data-card-err]').scrollIntoView({ block: 'nearest' });
    } else if (e instanceof StaffApiError && e.status === 401) {
      /* session ended; the PIN screen is up */
    } else {
      reject("Couldn't convert the paper card", errorText(e), '');
    }
  }
}

// ---- undo ------------------------------------------------------------------------

function setUndo(u: UndoTarget | null): void {
  st.undo = u && u.until > Date.now() ? u : null;
  renderUndo();
}

function renderUndo(): void {
  const u = st.undo;
  const live = !!u && u.until > Date.now();
  if (u && !live) st.undo = null;
  const left = live ? fmt.clock(u!.until - Date.now()) : '';
  $('[data-undo]').hidden = !live;
  $('[data-done-clock]').textContent = left;
  $('[data-undo-strip]').hidden = !live;
  if (live) {
    $('[data-undo-strip-text]').textContent = u!.text;
    $('[data-undo-clock]').textContent = left;
  }
}

async function undo(): Promise<void> {
  const u = st.undo;
  if (!u) return;
  const btns = document.querySelectorAll<HTMLButtonElement>('[data-undo], [data-undo-strip-btn]');
  btns.forEach((b) => (b.disabled = true));
  try {
    const r = await api.post<UndoResult>('/api/staff/undo', u.kind === 'event' ? { event_id: u.id } : { reward_id: u.id });
    setUndo(null);
    if (st.view === 'card' && st.cards.some((c) => c.card_id === r.card.card_id)) {
      setMember(r.card);
      renderCard(r.card, 0);
      setActionMode('stamp');
      showMsg($('[data-card-err]'), null);
      announce(`Undone. ${r.card.first_name} has ${r.card.stamps_current} of ${r.card.stamps_required} stamps.`);
      const note = h('li', { class: 'is-warn' }, `Undone: ${u.text}. The card is back to ${r.card.stamps_current}/${r.card.stamps_required}.`);
      $('[data-card-facts]').prepend(note);
    } else {
      $('[data-undo-strip]').hidden = false;
      $('[data-undo-strip-text]').textContent = `Undone: ${u.text}.`;
      $('[data-undo-strip-btn]').hidden = true;
      window.setTimeout(() => {
        $('[data-undo-strip-btn]').hidden = false;
        renderUndo();
      }, 4000);
      announce(`Undone: ${u.text}.`);
    }
  } catch (e) {
    if (e instanceof StaffApiError && e.code === 'undo_expired') setUndo(null);
    const msg = errorText(e);
    if (st.view === 'card') showMsg($('[data-card-err]'), msg);
    else reject("Couldn't undo", msg, '');
  } finally {
    btns.forEach((b) => (b.disabled = false));
  }
}

// ---- manager PIN -----------------------------------------------------------------

let mgrAction: ((pin: string) => Promise<void>) | null = null;

function askManager(detail: string, action: (pin: string) => Promise<void>): void {
  mgrAction = action;
  mgrPin.clear();
  $('[data-mgr-detail]').textContent = detail;
  showMsg($('[data-mgr-err]'), null);
  const dlg = $<HTMLDialogElement>('[data-dlg="manager"]');
  if (!dlg.open) dlg.showModal();
}

async function submitManager(value: string): Promise<void> {
  if (!mgrAction) return;
  const go = $<HTMLButtonElement>('[data-mgr-go]');
  go.disabled = true;
  showMsg($('[data-mgr-err]'), null);
  await mgrAction(value);
  go.disabled = mgrPin.value().length < 4;
}

// ---- redeem ----------------------------------------------------------------------

let redeeming: Reward | null = null;
let redeemOption: RewardOption | null = null;

/** A ready reward: which catalogue entry first (if there is a choice), then the item. */
function openReward(r: Reward): void {
  const opts = st.card?.reward_options ?? [];
  if (opts.length <= 1) return void openDrinkPicker(r, opts[0] ?? null);
  redeeming = r;
  $('[data-opt-for]').textContent = `${r.label} for ${st.card?.first_name ?? 'this customer'}. What would they like?`;
  $('[data-opt-list]').replaceChildren(
    ...opts.map((o) =>
      h(
        'li',
        {},
        h(
          'button',
          {
            type: 'button',
            class: 'st-reward',
            'data-option': o.id,
            onclick: () => {
              closeDialog('options');
              void openDrinkPicker(r, o);
            },
          },
          h(
            'span',
            { class: 'st-reward-text' },
            h('span', { class: 'st-reward-label' }, o.name),
            h('span', { class: 'st-reward-sub' }, [o.description, o.max_price_pence !== null ? `up to ${fmt.gbp(o.max_price_pence)}` : null].filter(Boolean).join(' · ') || `Covers ${o.covers}`),
          ),
          h('span', { class: 'st-reward-go' }, 'Choose'),
        ),
      ),
    ),
  );
  $<HTMLDialogElement>('[data-dlg="options"]').showModal();
}

function pickerKey(r: Reward, o: RewardOption | null): string {
  return o ? `o${o.id}` : `p${st.card?.program_slug ?? 'stamp'}`;
}

async function openDrinkPicker(r: Reward, option: RewardOption | null = null): Promise<void> {
  redeeming = r;
  redeemOption = option;
  const dlg = $<HTMLDialogElement>('[data-dlg="drinks"]');
  const drinkish = !option || /drink|matcha|coffee|tea/i.test(option.name + option.covers);
  $('#st-drk-h').textContent = option ? option.name : 'Which drink?';
  $('[data-drk-for]').textContent = `${r.label} for ${st.card?.first_name ?? 'this customer'}. Pick the ${drinkish ? 'drink' : 'item'} so stock stays right.`;
  $<HTMLInputElement>('[data-drk-q]').placeholder = drinkish ? 'Search drinks' : 'Search the menu';
  $('[data-drk-skip]').textContent = drinkish ? "Skip, don't log a drink" : "Skip, don't log it";
  showMsg($('[data-drk-err]'), null);
  const q = $<HTMLInputElement>('[data-drk-q]');
  q.value = '';
  dlg.showModal();
  const key = pickerKey(r, option);
  if (!st.drinks.has(key)) {
    $('[data-drk-list]').replaceChildren(h('p', { class: 'st-note' }, 'Loading…'));
    try {
      const qs = option ? `?option_id=${option.id}` : (st.card?.program_slug ?? 'stamp') === 'stamp' ? '' : `?reward_id=${r.id}`;
      st.drinks.set(key, await api.get<DrinksResult>(`/api/staff/drinks${qs}`));
    } catch (e) {
      $('[data-drk-list]').replaceChildren();
      showMsg($('[data-drk-err]'), `${errorText(e)} You can still skip logging it.`);
      return;
    }
  }
  renderDrinks();
  // On a phone the keyboard would cover the list: focus search only where there's room.
  if (window.matchMedia('(min-width: 700px) and (min-height: 600px)').matches) q.focus();
}

function renderDrinks(): void {
  const res = redeeming ? st.drinks.get(pickerKey(redeeming, redeemOption)) : undefined;
  const all = res?.items ?? [];
  const cap = res?.max_price_pence ?? null;
  const q = $<HTMLInputElement>('[data-drk-q]').value.trim().toLowerCase();
  const list = $('[data-drk-list]');
  const btn = (d: Drink) =>
    h(
      'button',
      { type: 'button', class: `st-drink${cap !== null && d.price_pence !== null && d.price_pence > cap ? ' is-over' : ''}`, 'data-drink': d.menu_item_id, onclick: () => redeem(d) },
      h('span', {}, d.name),
      h('span', { class: 'num' }, d.price_pence === null ? '' : fmt.gbp(d.price_pence)),
    );
  const nodes: Node[] = [];
  if (cap !== null) nodes.push(h('p', { class: 'st-note' }, `Covers up to ${fmt.gbp(cap)}. Struck-through prices are over it.`));
  if (q) {
    const words = q.split(/\s+/);
    const hits = all.filter((d) => words.every((w) => `${d.name} ${d.category ?? ''}`.toLowerCase().includes(w)));
    if (!hits.length) nodes.push(h('p', { class: 'st-note' }, `Nothing matches "${q}". Try fewer letters, or skip.`));
    hits.slice(0, 60).forEach((d) => nodes.push(btn(d)));
  } else {
    const byId = new Map(all.map((d) => [d.menu_item_id, d]));
    const rec = recents
      .get()
      .map((id) => byId.get(id))
      .filter((d): d is Drink => !!d);
    if (rec.length) {
      nodes.push(h('h3', {}, 'Recent'));
      rec.forEach((d) => nodes.push(btn(d)));
    }
    // The API sorts by name and category is often empty on the live menu: group here,
    // named categories A-Z, uncategorised last; no headings at all if none are named.
    const groups = new Map<string, Drink[]>();
    for (const d of all) {
      const c = d.category?.trim() || '';
      groups.set(c, [...(groups.get(c) ?? []), d]);
    }
    const named = [...groups.keys()].filter(Boolean).sort((a, b) => a.localeCompare(b, 'en-GB'));
    for (const c of [...named, ...(groups.has('') ? [''] : [])]) {
      if (named.length) nodes.push(h('h3', {}, c || 'Other'));
      groups.get(c)!.forEach((d) => nodes.push(btn(d)));
    }
  }
  list.replaceChildren(...nodes);
  list.scrollTop = 0;
}

async function redeem(d: Drink | null): Promise<void> {
  const r = redeeming;
  if (!r) return;
  const dlg = $<HTMLDialogElement>('[data-dlg="drinks"]');
  dlg.querySelectorAll<HTMLButtonElement>('button').forEach((b) => (b.disabled = true));
  showMsg($('[data-drk-err]'), null);
  try {
    const body: Record<string, unknown> = { reward_id: r.id };
    if (d) body.menu_item_id = d.menu_item_id;
    if (redeemOption) body.option_id = redeemOption.id;
    const what = `${st.card?.first_name ?? 'Their'}'s ${rewardName(r)}`;
    const res = await api.post<RedeemResult>('/api/staff/redeem', body);
    if (d) recents.push(d.menu_item_id);
    closeDialog('drinks');
    const opt = redeemOption;
    redeeming = null;
    redeemOption = null;
    setMember(res.card);
    renderCard(res.card, 0);
    showDone(
      d ? `${d.name}, on us` : 'Reward redeemed',
      d ? `${what} is used${opt ? ` as ${opt.name.toLowerCase()}` : ''}, logged as a free ${d.name}.` : `${what} is used. Nothing logged.`,
      { kind: 'reward', id: res.reward_id, until: undoUntil(res.undo_until), cardId: res.card.card_id, text: `Redeemed ${what}` },
      true,
    );
  } catch (e) {
    if (e instanceof StaffApiError && (e.code === 'over_price_cap' || e.code === 'not_covered')) {
      showMsg($('[data-drk-err]'), e.message);
      if (d) document.querySelector(`[data-drink="${d.menu_item_id}"]`)?.classList.add('is-over');
      $('[data-drk-err]').scrollIntoView({ block: 'nearest' });
    } else if (!(e instanceof StaffApiError && e.status === 401)) {
      showMsg($('[data-drk-err]'), errorText(e));
    }
  } finally {
    dlg.querySelectorAll<HTMLButtonElement>('button').forEach((b) => (b.disabled = false));
  }
}

// ---- link to the till (phase 3) ---------------------------------------------------------

async function openLink(): Promise<void> {
  const card = st.card;
  if (!card) return;
  const dlg = $<HTMLDialogElement>('[data-dlg="link"]');
  $('[data-link-for]').textContent = `Pick ${card.first_name}'s customer from a recent receipt, or type their customer number from Lightspeed. Their receipts then add ${isPoints(card) ? 'points' : 'stamps'} by themselves.`;
  showMsg($('[data-link-err]'), null);
  $<HTMLInputElement>('[data-link-q]').value = '';
  dlg.showModal();
  const list = $('[data-link-list]');
  list.replaceChildren(h('p', { class: 'st-note' }, 'Loading…'));
  try {
    const r = await api.get<{ customers: PosCustomer[] }>('/api/staff/pos-customers');
    list.replaceChildren(
      ...(r.customers.length
        ? r.customers.map((c) =>
            h(
              'button',
              { type: 'button', class: 'st-drink', 'data-customer': c.customer_id, onclick: () => void link({ customer_id: c.customer_id }) },
              h('span', { class: 'st-cust' }, h('strong', {}, c.label ?? 'Customer'), h('small', { class: 'num' }, c.customer_id)),
              h('span', { class: 'num st-cust-when' }, `${fmt.ago(c.last_at)}${c.receipts > 1 ? ` · ${c.receipts} receipts` : ''}`),
            ),
          )
        : [h('p', { class: 'st-note' }, 'No unlinked till customers in the last 3 days. Receipts arrive with the nightly sync.')]),
    );
  } catch (e) {
    list.replaceChildren();
    showMsg($('[data-link-err]'), errorText(e));
  }
}

async function link(body: { customer_id?: string; receipt_id?: string }): Promise<void> {
  const card = st.card;
  if (!card) return;
  showMsg($('[data-link-err]'), null);
  try {
    const r = await api.post<UndoResult>('/api/staff/link-pos', { card_id: card.card_id, ...body });
    closeDialog('link');
    setMember(r.card);
    renderCard(r.card, 0);
    showDone(`${r.card.first_name} is linked to the till`, 'Receipts with this customer attached now add stamps by themselves. No need to scan when they do.', null);
  } catch (e) {
    showMsg($('[data-link-err]'), errorText(e));
  }
}

function closeDialog(name: 'manager' | 'drinks' | 'options' | 'link'): void {
  const dlg = document.querySelector<HTMLDialogElement>(`[data-dlg="${name}"]`);
  if (dlg?.open) dlg.close();
}
function closeDialogs(): void {
  closeDialog('manager');
  closeDialog('drinks');
  closeDialog('options');
  closeDialog('link');
}

// ---- convert -----------------------------------------------------------------------

function resetStickers(): void {
  document.querySelectorAll('[data-stickers] [data-n]').forEach((b) => b.setAttribute('aria-checked', 'false'));
  $<HTMLButtonElement>('[data-convert-go]').disabled = true;
}

// ---- find member -------------------------------------------------------------------

let findSeq = 0;
let findTimer = 0;

async function runFind(): Promise<void> {
  const q = $<HTMLInputElement>('[data-find-q]').value.trim();
  const status = $('[data-find-status]');
  const list = $('[data-find-results]');
  const seq = ++findSeq;
  if (q.length < 2) {
    list.replaceChildren();
    status.textContent = q ? 'Type at least 2 characters.' : '';
    return;
  }
  status.textContent = 'Searching…';
  try {
    const r = await api.get<{ members: LookupMember[] }>(`/api/staff/lookup?q=${encodeURIComponent(q)}`);
    if (seq !== findSeq) return;
    status.textContent = r.members.length ? `${fmt.plural(r.members.length, 'member')} found` : 'No member found. Check the spelling, or try their phone number or email.';
    list.replaceChildren(
      ...r.members.map((m) =>
        h('li', {}, h('button', { type: 'button', class: 'st-result', onclick: () => showRecovery(m) }, h('strong', {}, m.first_name), h('span', {}, m.contact_masked))),
      ),
    );
  } catch (e) {
    if (seq === findSeq) status.textContent = errorText(e);
  }
}

async function showRecovery(m: LookupMember): Promise<void> {
  const status = $('[data-find-status]');
  status.textContent = 'Making the code…';
  try {
    const [{ url }, { qrSvg }] = await Promise.all([api.post<{ url: string }>('/api/staff/recovery-link', { card_id: m.card_id }), import('./qr-encode')]);
    $('[data-qr]').innerHTML = await qrSvg(url, `QR code that opens ${m.first_name}'s card`);
    $('[data-recovery-name]').textContent = `${m.first_name} (${m.contact_masked})`;
    status.textContent = '';
    $('[data-find-search]').hidden = true;
    $('[data-find-lede]').hidden = true;
    $('[data-recovery]').hidden = false;
    st.recoveryUntil = Date.now() + RECOVERY_SHOW_MS;
    $<HTMLButtonElement>('[data-recovery-done]').focus({ preventScroll: true });
    announce(`Showing a QR code for ${m.first_name} to scan.`);
  } catch (e) {
    status.textContent = errorText(e);
  }
}

function hideRecovery(): void {
  st.recoveryUntil = 0;
  $('[data-qr]').replaceChildren();
  $('[data-recovery]').hidden = true;
  $('[data-find-search]').hidden = false;
  $('[data-find-lede]').hidden = false;
}

// ---- timers + network ----------------------------------------------------------------

function everySecond(): void {
  renderUndo();
  if (st.recoveryUntil) {
    const left = st.recoveryUntil - Date.now();
    if (left <= 0) hideRecovery();
    else $('[data-recovery-clock]').textContent = fmt.clock(left);
  }
}

function setOffline(off: boolean): void {
  $('[data-offline]').hidden = !off;
}

// ---- boot ----------------------------------------------------------------------------

function wire(): void {
  scanner = createScanner($<HTMLVideoElement>('[data-video]'), onCode, cameraProblem);

  // pairing
  $<HTMLFormElement>('[data-pair-form]').addEventListener('submit', async (e) => {
    e.preventDefault();
    const f = e.currentTarget as HTMLFormElement;
    const code = (f.elements.namedItem('code') as HTMLInputElement).value.replace(/\D/g, '');
    const name = (f.elements.namedItem('name') as HTMLInputElement).value.trim();
    const err = $('[data-pair-err]');
    if (code.length !== 6) return showMsg(err, 'The pairing code is 6 digits.');
    if (!name) return showMsg(err, 'Give this device a name, such as "Till tablet" or "Anna\'s phone".');
    const btn = $<HTMLButtonElement>('[data-pair-submit]');
    btn.disabled = true;
    showMsg(err, null);
    try {
      const r = await api.post<PairResult>('/api/staff/device/pair', { pairing_code: code, device_name: name }, { auth: false });
      device.set(r.device_token, name);
      $('[data-device-name]').textContent = name;
      f.reset();
      showMsg($('[data-pin-note]'), `Paired as "${name}". Now sign in with your PIN.`);
      show('pin');
    } catch (e2) {
      showMsg(err, errorText(e2));
    } finally {
      btn.disabled = false;
    }
  });

  // PIN
  pin = pinPad($('[data-keypad="pin"]'), $('[data-dots="pin"]'), (n) => ($<HTMLButtonElement>('[data-pin-go]').disabled = n < 4), (v) => void signIn(v));
  $('[data-pin-go]').addEventListener('click', () => void signIn(pin.value()));
  const repair = $<HTMLButtonElement>('[data-repair]');
  repair.addEventListener('click', () => {
    if (repair.dataset.armed) {
      delete repair.dataset.armed;
      repair.textContent = 'Pair again';
      forgetDevice(null);
    } else {
      repair.dataset.armed = '1';
      repair.textContent = 'Tap again to unpair this device';
      window.setTimeout(() => {
        delete repair.dataset.armed;
        repair.textContent = 'Pair again';
      }, 4000);
    }
  });

  // manager PIN
  mgrPin = pinPad($('[data-keypad="manager"]'), $('[data-dots="manager"]'), (n) => ($<HTMLButtonElement>('[data-mgr-go]').disabled = n < 4), (v) => void submitManager(v));
  $('[data-mgr-go]').addEventListener('click', () => void submitManager(mgrPin.value()));
  $('[data-mgr-cancel]').addEventListener('click', () => closeDialog('manager'));
  $('[data-dlg="manager"]').addEventListener('close', () => {
    mgrAction = null;
    mgrPin.clear();
  });

  // physical keyboard on the PIN pads
  document.addEventListener('keydown', (e) => {
    const mgr = $<HTMLDialogElement>('[data-dlg="manager"]');
    if (mgr.open) {
      mgrPin.key(e);
      return;
    }
    if (st.view === 'pin' && !(e.target instanceof HTMLInputElement)) pin.key(e);
  });

  // nav
  document.querySelectorAll<HTMLAnchorElement>('[data-tab]').forEach((t) =>
    t.addEventListener('click', (e) => {
      e.preventDefault();
      const v = t.dataset.tab as View;
      if (v === 'scan') st.convertN = 0;
      show(v);
    }),
  );
  $('[data-signout]').addEventListener('click', () => void signOut());

  // scan
  $('[data-camera-start]').addEventListener('click', () => void startCamera());
  const torch = $<HTMLButtonElement>('[data-torch]');
  torch.addEventListener('click', async () => {
    const on = await scanner.setTorch(torch.getAttribute('aria-pressed') !== 'true');
    torch.setAttribute('aria-pressed', String(on));
    $('[data-torch-label]').textContent = on ? 'Torch on' : 'Torch';
  });
  $<HTMLFormElement>('[data-manual]').addEventListener('submit', (e) => {
    e.preventDefault();
    const v = (e.currentTarget as HTMLFormElement).elements.namedItem('payload') as HTMLInputElement;
    void handlePayload(v.value);
  });
  $('[data-convert-cancel]').addEventListener('click', () => {
    st.convertN = 0;
    resetStickers();
    renderConvertBanner();
    show('scan', { focus: false });
  });
  $('[data-undo-strip-btn]').addEventListener('click', () => void undo());

  // card
  document.querySelectorAll<HTMLButtonElement>('[data-stamp]').forEach((b) => b.addEventListener('click', () => void stamp(Number(b.dataset.stamp))));
  $('[data-undo]').addEventListener('click', () => void undo());
  const back = () => {
    st.card = null;
    st.cards = [];
    show('scan');
  };
  $('[data-next]').addEventListener('click', back);
  $('[data-back]').addEventListener('click', back);

  // points
  $('[data-spend]').addEventListener('input', updateSpendPreview);
  $<HTMLFormElement>('[data-points-actions]').addEventListener('submit', (e) => {
    e.preventDefault();
    const pence = parsePence($<HTMLInputElement>('[data-spend]').value);
    if (pence !== null) void spend(pence);
  });

  // card tabs: arrow keys move between a member's cards
  $('[data-cardtabs]').addEventListener('keydown', (e) => {
    if (e.key !== 'ArrowRight' && e.key !== 'ArrowLeft') return;
    const i = st.cards.findIndex((c) => c.card_id === st.card?.card_id);
    const next = st.cards[(i + (e.key === 'ArrowRight' ? 1 : -1) + st.cards.length) % st.cards.length];
    if (next) selectCard(next.card_id);
    e.preventDefault();
  });

  // link to the till
  $('[data-link-cancel]').addEventListener('click', () => closeDialog('link'));
  $<HTMLFormElement>('[data-link-form]').addEventListener('submit', (e) => {
    e.preventDefault();
    const v = $<HTMLInputElement>('[data-link-q]').value.trim();
    if (!v) return showMsg($('[data-link-err]'), 'Type the customer number, or pick a customer below.');
    void link({ customer_id: v });
  });

  // redeem
  $('[data-opt-cancel]').addEventListener('click', () => closeDialog('options'));
  $('[data-drk-q]').addEventListener('input', renderDrinks);
  $('[data-drk-skip]').addEventListener('click', () => void redeem(null));
  $('[data-drk-cancel]').addEventListener('click', () => closeDialog('drinks'));

  // convert
  $('[data-stickers]').addEventListener('click', (e) => {
    const b = (e.target as HTMLElement).closest<HTMLButtonElement>('[data-n]');
    if (!b) return;
    document.querySelectorAll('[data-stickers] [data-n]').forEach((x) => x.setAttribute('aria-checked', String(x === b)));
    $<HTMLButtonElement>('[data-convert-go]').disabled = false;
  });
  $('[data-convert-go]').addEventListener('click', () => {
    const b = document.querySelector<HTMLElement>('[data-stickers] [aria-checked="true"]');
    st.convertN = Number(b?.dataset.n ?? 0);
    if (st.convertN) show('scan');
  });

  // find
  $('[data-find-q]').addEventListener('input', () => {
    window.clearTimeout(findTimer);
    findTimer = window.setTimeout(() => void runFind(), 300);
  });
  $('[data-recovery-done]').addEventListener('click', () => {
    hideRecovery();
    $<HTMLInputElement>('[data-find-q]').focus();
  });

  // session/device events from api.ts
  window.addEventListener('staff:session-ended', (e) => endSession((e as CustomEvent<string>).detail || 'Your session has ended. Enter your PIN again.'));
  window.addEventListener('staff:device-revoked', (e) => forgetDevice(`${(e as CustomEvent<string>).detail} Ask a manager for a new pairing code.`));

  // network
  window.addEventListener('online', () => setOffline(false));
  window.addEventListener('offline', () => setOffline(true));
  setOffline(navigator.onLine === false);

  // A till tablet sleeps and wakes: re-check the session clock, and let the camera go
  // while hidden so the light doesn't stay on in a drawer.
  document.addEventListener('visibilitychange', () => {
    if (document.hidden) {
      scanner.stop();
      return;
    }
    if (st.view !== 'pair' && st.view !== 'pin' && !session.get()) {
      endSession('Your 12-hour session has ended. Enter your PIN to carry on.');
      return;
    }
    if (st.view === 'scan') void startCamera();
  });

  st.tick = window.setInterval(everySecond, 1000);
}

export function boot(): void {
  wire();
  const mock = isMockMode();
  $('[data-mockflag]').hidden = !mock;
  $('[data-device-name]').textContent = device.name() ?? 'not named';

  if (mock) {
    const demo = new URLSearchParams(location.search).get('demo');
    if (demo) {
      void runDemo(demo);
      return;
    }
  }

  registerServiceWorker();
  if (!device.token()) return show('pair');
  const s = session.get();
  if (!s) return show('pin');
  applySession(s);
  show('scan');
  // Check the stored session is still good (revoked, or a server restart).
  api
    .get<MeResult>('/api/staff/me')
    .then((me) => {
      session.set({ ...s, user: me.user, expires_at: me.expires_at });
      applySession(me);
      if (me.device?.name) $('[data-device-name]').textContent = me.device.name;
      setOffline(false);
    })
    .catch((e) => {
      if (e instanceof StaffApiError && e.offline) setOffline(true);
    });
}

function registerServiceWorker(): void {
  if (!import.meta.env.PROD || !('serviceWorker' in navigator)) return;
  // Scope /staff (not /staff/) so it covers the page itself: Caddy sends
  // Service-Worker-Allowed: /staff for the worker script (CONTRACT §8).
  navigator.serviceWorker.register('/staff/sw.js', { scope: '/staff' }).catch(() => {
    /* no offline shell; the scanner works the same online */
  });
}

// ---- mock demo states (dev only, ?mock=1&demo=…) -------------------------------------

const MOCK = (n: number, sig = 'a1b2c3d4') => `SC1:0c5e1d3a-5f6b-4b6e-9a57-1b1f3e0c000${n}:${sig}`;

async function runDemo(demo: string): Promise<void> {
  try {
    sessionStorage.removeItem('sc-staff-mock-state');
  } catch {
    /* ignore */
  }
  session.clear();
  device.clear();
  if (demo === 'pair') return show('pair');
  const r = await api.post<PairResult>('/api/staff/device/pair', { pairing_code: '123456', device_name: 'Till tablet' }, { auth: false });
  device.set(r.device_token, 'Till tablet');
  $('[data-device-name]').textContent = 'Till tablet';
  if (demo === 'pin') {
    show('pin');
    ['1', '2'].forEach((k) => document.querySelector<HTMLButtonElement>(`[data-keypad="pin"] [data-key="${k}"]`)?.click());
    return;
  }
  if (demo === 'pinwrong') {
    show('pin');
    return signIn('5555');
  }
  const login = await api.post<LoginResult>('/api/staff/login', { pin: '1234' }, { auth: false });
  session.set({ token: login.session_token, expires_at: login.expires_at, user: login.user });
  applySession(login);
  const scanThen = (n: number) => handlePayloadDirect(MOCK(n));
  switch (demo) {
    case 'scan':
      return show('scan');
    case 'offline':
      show('scan');
      return setOffline(true);
    case 'reject':
      show('scan');
      return handlePayload(MOCK(1, 'ffffffff'));
    case 'unknown':
      show('scan');
      return handlePayload('SC1:0c5e1d3a-5f6b-4b6e-9a57-1b1f3e0c9999:a1b2c3d4');
    case 'card':
      return scanThen(1);
    case 'void':
      return scanThen(4);
    case 'reward':
      return scanThen(2);
    case 'stamped':
      await scanThen(1);
      return stamp(1);
    case 'issued':
      await scanThen(1);
      return stamp(3);
    case 'manager':
      await scanThen(1);
      await stamp(3);
      openCard(st.card!);
      return stamp(1);
    case 'drinks':
      await scanThen(2);
      return openDrinkPicker(st.card!.rewards[0], st.card!.reward_options?.[0] ?? null);
    case 'cap': {
      await scanThen(2);
      await openDrinkPicker(st.card!.rewards[0], st.card!.reward_options?.[0] ?? null);
      const shake = [...st.drinks.values()].flatMap((d) => d.items).find((d) => d.name === 'Strawberry milkshake')!;
      return redeem(shake);
    }
    case 'redeemed': {
      await scanThen(2);
      await openDrinkPicker(st.card!.rewards[0], st.card!.reward_options?.[0] ?? null);
      return redeem([...st.drinks.values()][0].items[0]);
    }
    // ---- phase 3 ----
    case 'cards': // a member with two cards: the main card and the matcha club
      return scanThen(1);
    case 'matcha':
      await scanThen(5);
      return;
    case 'points': // a points card, spend typed
      await scanThen(6);
      $<HTMLInputElement>('[data-spend]').value = '4.50';
      return updateSpendPreview();
    case 'spent': {
      await scanThen(6);
      return spend(650);
    }
    case 'options': // a ready reward with a choice of what it is
      await scanThen(2);
      return openReward(st.card!.rewards.find((r) => r.kind === 'STAMP_CARD')!);
    case 'cake': {
      await scanThen(2);
      const opt = st.card!.reward_options!.find((o) => o.name === 'Slice of cake')!;
      return openDrinkPicker(st.card!.rewards[0], opt);
    }
    case 'join': // a member with one card and clubs to add
      return scanThen(3);
    case 'link':
      await scanThen(3);
      return openLink();
    case 'convert':
      show('convert');
      document.querySelector<HTMLButtonElement>('[data-stickers] [data-n="5"]')?.click();
      return;
    case 'converting':
      st.convertN = 5;
      return show('scan');
    case 'migrated':
      st.convertN = 5;
      show('scan');
      return handlePayload(MOCK(3));
    case 'already':
      st.convertN = 3;
      show('scan');
      return handlePayload(MOCK(2));
    case 'find': {
      show('find');
      $<HTMLInputElement>('[data-find-q]').value = 'ol';
      return runFind();
    }
    case 'recovery':
      show('find');
      return showRecovery({ card_id: '0c5e1d3a-5f6b-4b6e-9a57-1b1f3e0c0001', first_name: 'Olena', contact_masked: '07••• •••312' });
    case 'strip':
      await scanThen(1);
      await stamp(1);
      st.card = null;
      return show('scan');
    default:
      return show('scan');
  }
}

async function handlePayloadDirect(payload: string): Promise<void> {
  const card = await api.post<ScanResult>('/api/staff/scan', { payload });
  openCard(card);
}
