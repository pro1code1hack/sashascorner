// Photo admin entry: sign-in, first load, and re-sign-in when a session lapses.
import { auth, basicHeader, realApi } from './api';
import { $, announce } from './dom';
import { initLibrary, reloadMedia } from './library';
import { mockApi, mockLogin } from './mock';
import { initSlots } from './slots';
import { setSlots, state } from './state';
import { ApiError } from './types';

const MOCK = new URLSearchParams(location.search).has('mock');
state.api = MOCK ? mockApi : realApi;

const gate = $('[data-signin]');
const app = $('[data-app]');
const form = $('[data-signin-form]') as HTMLFormElement;
const pw = $('[data-signin-pw]') as HTMLInputElement;
const err = $('[data-signin-err]');
const intro = $('[data-signin-intro]');
let loaded = false;

if (MOCK) $('[data-mock-note]').hidden = false;

function explain(e: unknown): string {
  const s = (e as ApiError).status;
  if (s === 401) return "That password didn't work. Check it and try again.";
  if (s === 503) return 'The photo admin isn’t switched on at the server yet (no admin password is configured). Ask whoever set up the website.';
  if (s === 0) return 'Could not reach the website’s server. Check your connection and try again.';
  return `Something went wrong (${(e as Error).message}). Try again in a moment.`;
}

let lastStatus = 0;
async function enter(header: string, password?: string): Promise<boolean> {
  auth.set(header);
  try {
    if (MOCK) mockLogin(password ?? 'mock');
    if (!loaded) {
      // Library first: the slot drafts compare against each photo's own description.
      await reloadMedia();
      setSlots(await state.api.slots());
      loaded = true;
    } else {
      await state.api.listMedia();
    }
  } catch (e) {
    lastStatus = (e as ApiError).status;
    auth.set(null);
    err.textContent = explain(e);
    err.hidden = false;
    pw.setAttribute('aria-invalid', 'true');
    return false;
  }
  err.hidden = true;
  pw.removeAttribute('aria-invalid');
  pw.value = '';
  gate.hidden = true;
  app.hidden = false;
  return true;
}

form.addEventListener('submit', async (e) => {
  e.preventDefault();
  const p = pw.value;
  if (!p) {
    err.textContent = 'Please type the password.';
    err.hidden = false;
    pw.focus();
    return;
  }
  const btn = form.querySelector<HTMLButtonElement>('button[type=submit]')!;
  btn.disabled = true;
  btn.textContent = 'Checking…';
  const ok = await enter(basicHeader(p), p);
  btn.disabled = false;
  btn.textContent = 'Sign in';
  if (ok) {
    announce('Signed in.');
    $('[data-app-title]').focus();
  } else {
    pw.focus();
  }
});

$('[data-signout]').addEventListener('click', () => {
  auth.set(null);
  location.reload();
});

// A 401 mid-session (password changed on the server): ask again, keep drafts.
window.addEventListener('adm:unauth', () => {
  auth.set(null);
  intro.textContent = 'You were signed out. Sign in again — your unsaved changes are kept.';
  app.hidden = true;
  gate.hidden = false;
  pw.focus();
});

initLibrary();
initSlots();

const saved = auth.get();
if (saved && !MOCK) {
  gate.hidden = true;
  void enter(saved).then((ok) => {
    if (ok) return;
    gate.hidden = false;
    if (lastStatus === 401) {
      err.hidden = true; // a stale session is not the owner's mistake
      pw.removeAttribute('aria-invalid');
    }
  });
}
