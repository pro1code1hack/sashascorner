// Boots the admin shell (layouts/Admin.astro): checks the session, shows the
// sign-in form or the app, wires sign-out and the unread-messages badge.

import { api, ApiError, errorText, goToSignIn, isMock } from './api';
import { login, logout, markReady, me } from './session';
import { announce } from './ui';
import type { Me, Message } from './types';

type AuthState = 'pending' | 'in' | 'out';
const setAuth = (s: AuthState) => (document.body.dataset.auth = s);

/** Update the Messages badge in the sidebar and the tab bar. */
export function setUnread(n: number): void {
  document.querySelectorAll<HTMLElement>('[data-unread]').forEach((b) => {
    b.hidden = n <= 0;
    b.textContent = n > 99 ? '99+' : String(n);
  });
  document.querySelectorAll<HTMLElement>('[data-nav="messages"]').forEach((a) => {
    a.setAttribute('aria-label', n > 0 ? `Messages, ${n} new` : 'Messages');
  });
}

export async function refreshUnread(): Promise<void> {
  try {
    const list = await api.get<Message[]>('/api/admin/messages', { status: 'new' });
    setUnread(Array.isArray(list) ? list.length : 0);
  } catch {
    /* the badge is a nicety */
  }
}

function safeNext(): string | null {
  const n = new URLSearchParams(location.search).get('next');
  return n && /^\/admin(\/|$|\?)/.test(n) && !n.startsWith('//') ? n : null;
}

const isIndex = () => document.body.dataset.index === '1';

export async function bootShell(): Promise<void> {
  if (isMock()) document.querySelectorAll<HTMLElement>('[data-mockflag]').forEach((e) => (e.hidden = false));
  keepMockInLinks();

  document.querySelectorAll<HTMLButtonElement>('[data-signout]').forEach((b) =>
    b.addEventListener('click', async () => {
      b.disabled = true;
      b.textContent = 'Signing out…';
      await logout();
      location.assign('/admin?signedout=1');
    }),
  );

  let who: Me | null;
  try {
    who = await me();
  } catch (e) {
    showBootError(errorText(e));
    return;
  }

  if (!who) {
    if (!isIndex()) {
      goToSignIn();
      return;
    }
    setAuth('out');
    document.querySelector<HTMLAnchorElement>('[data-skip]')?.setAttribute('href', '#adm-password');
    initSignIn();
    return;
  }

  const next = safeNext();
  if (isIndex() && next) {
    location.replace(next);
    return;
  }
  setAuth('in');
  markReady(who);
  void refreshUnread();
}

function showBootError(text: string): void {
  const box = document.querySelector<HTMLElement>('.adm-loading');
  if (!box) return;
  box.replaceChildren();
  const p = document.createElement('p');
  p.textContent = text;
  const b = document.createElement('button');
  b.type = 'button';
  b.className = 'adm-btn adm-btn--secondary';
  b.style.marginTop = '12px';
  b.textContent = 'Try again';
  b.addEventListener('click', () => location.reload());
  box.append(p, b);
  box.setAttribute('role', 'alert');
}

function keepMockInLinks(): void {
  if (!isMock()) return;
  document.querySelectorAll<HTMLAnchorElement>('a[href^="/admin"]').forEach((a) => {
    const u = new URL(a.href, location.origin);
    u.searchParams.set('mock', '1');
    a.href = u.pathname + u.search;
  });
}

// ---- sign-in (only on /admin) -------------------------------------------------

function initSignIn(): void {
  const form = document.querySelector<HTMLFormElement>('[data-signin]');
  if (!form) return;
  const input = form.querySelector<HTMLInputElement>('input[name="password"]')!;
  const err = form.querySelector<HTMLElement>('[data-signin-error]')!;
  const btn = form.querySelector<HTMLButtonElement>('button[type="submit"]')!;
  const note = form.querySelector<HTMLElement>('[data-signin-note]');
  const params = new URLSearchParams(location.search);
  if (note && params.get('signedout') === '1') {
    note.textContent = 'You are signed out.';
    note.hidden = false;
  } else if (note && params.get('next')) {
    note.textContent = 'Sign in to carry on where you were.';
    note.hidden = false;
  }
  if (isMock()) {
    const hint = form.querySelector<HTMLElement>('[data-signin-mock]');
    if (hint) hint.hidden = false;
  }
  input.focus();

  const showErr = (text: string) => {
    err.textContent = text;
    err.hidden = false;
    input.setAttribute('aria-invalid', 'true');
    input.setAttribute('aria-describedby', err.id);
    announce(text, true);
  };

  form.addEventListener('submit', async (e) => {
    e.preventDefault();
    err.hidden = true;
    input.removeAttribute('aria-invalid');
    const password = input.value;
    if (!password) {
      showErr('Type the password first.');
      input.focus();
      return;
    }
    btn.disabled = true;
    btn.textContent = 'Opening…';
    try {
      await login(password);
      input.value = '';
      const next = safeNext();
      if (next) location.assign(next);
      else {
        const u = new URL(location.href);
        u.searchParams.delete('signedout');
        location.replace(u.pathname + u.search);
      }
      return;
    } catch (e2) {
      const status = e2 instanceof ApiError ? e2.status : -1;
      if (status === 401) showErr("That's not it. Check the password and try again.");
      else if (status === 429) showErr('Too many tries. Wait a minute, then try again.');
      else if (status === 503)
        showErr("Sign-in isn't set up on the server yet. Whoever runs the website needs to set the admin password.");
      else showErr(errorText(e2));
      input.select();
    } finally {
      btn.disabled = false;
      btn.textContent = 'Open';
    }
  });
}
