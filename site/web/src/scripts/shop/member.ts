// The signed-in member, for the top bar and the status page (Agent K). The card token
// itself lives in the rewards slot (api.ts `session`); this is the name that goes with
// it, kept under 'sc.shop.member.v1' so the bar says "Sasha" on the first paint and
// refreshed from GET /api/shop/me whenever a token is on the device.
import { signal, type Signal } from '@preact/signals';
import { session, shopApi } from './api';
import { config } from './store';
import type { MeK } from './types';

const KEY = 'sc.shop.member.v1';

export interface MemberSummary {
  card_id: string;
  first_name: string;
}

function readCached(): MemberSummary | null {
  try {
    const raw = localStorage.getItem(KEY);
    if (!raw) return null;
    const m = JSON.parse(raw) as Partial<MemberSummary>;
    return typeof m.card_id === 'string' && typeof m.first_name === 'string' ? { card_id: m.card_id, first_name: m.first_name } : null;
  } catch {
    return null;
  }
}
function writeCached(m: MemberSummary | null) {
  try {
    if (m) localStorage.setItem(KEY, JSON.stringify(m));
    else localStorage.removeItem(KEY);
  } catch {
    /* storage off: the name is fetched again next visit */
  }
}

/** Who is signed in on this device, or null. Only meaningful while `session.token()` is set. */
export const member: Signal<MemberSummary | null> = signal<MemberSummary | null>(session.token() ? readCached() : null);
/** True when a Rewards card token is on this device (not yet proven with the server). */
export const signedIn: Signal<boolean> = signal<boolean>(!!session.token());
/** The full /me answer from the last load, for screens that want more than the name. */
export const me: Signal<MeK | null> = signal<MeK | null>(null);

export function setMember(m: MeK | null): void {
  me.value = m;
  const summary = m ? { card_id: m.card_id, first_name: m.member?.first_name ?? m.first_name } : null;
  member.value = summary;
  writeCached(summary);
  syncHeader(summary);
}

/** The site header's "My account" link (components/Header.astro) follows a sign-in or sign-out without a reload. */
function syncHeader(m: MemberSummary | null): void {
  const a = document.querySelector<HTMLAnchorElement>('[data-hdr-account]');
  if (!a) return;
  const name = m?.first_name.trim();
  a.textContent = name || 'My account';
  a.setAttribute('aria-label', name ? `My account, ${name}` : 'My account');
}

/** Forgets the card on this device and everything that came with it. */
export function signOut(): void {
  session.clear();
  signedIn.value = false;
  setMember(null);
}

/** Records a fresh sign-in or join; the name arrives with the next `loadMember()`. */
export function signIn(cardId: string, token: string): void {
  session.set(cardId, token);
  signedIn.value = true;
  void loadMember();
}

let inflight: Promise<MeK | null> | null = null;
/**
 * Loads /me when a token is here. A 401 means the token is dead (expired, or the card
 * was deleted): it is forgotten so the app stops pretending. Other failures keep what
 * was cached.
 */
export function loadMember(): Promise<MeK | null> {
  if (!session.token()) {
    if (member.value || me.value) setMember(null);
    signedIn.value = false;
    return Promise.resolve(null);
  }
  if (inflight) return inflight;
  inflight = shopApi
    .me()
    .then((r) => {
      if (r.ok && r.data) {
        signedIn.value = true;
        setMember(r.data);
        return r.data;
      }
      if (r.status === 401) signOut();
      return null;
    })
    .finally(() => (inflight = null));
  return inflight;
}

/** The wall: an account is required and this device has no card. */
export function needsSignIn(): boolean {
  return config.value?.require_account === true && !signedIn.value;
}
