// Session state for the admin: who is signed in, and a promise pages await before
// loading anything.

import { api, ApiError } from './api';
import type { Me } from './types';

let resolveReady: (me: Me) => void;
const readyP = new Promise<Me>((r) => (resolveReady = r));

/** Resolves once the owner is signed in (never resolves on the sign-in screen). */
export function ready(): Promise<Me> {
  return readyP;
}

export function markReady(me: Me): void {
  resolveReady(me);
}

/** null when signed out. */
export async function me(): Promise<Me | null> {
  try {
    return await api.get<Me>('/api/admin/me', undefined, { quiet401: true });
  } catch (e) {
    if (e instanceof ApiError && e.status === 401) return null;
    throw e;
  }
}

export async function login(password: string): Promise<void> {
  await api.post('/api/admin/login', { password }, { quiet401: true });
}

export async function logout(): Promise<void> {
  try {
    await api.post('/api/admin/logout', {}, { quiet401: true });
  } catch {
    /* signed out either way */
  }
}

/** POST /api/admin/password. Other devices are signed out; this one gets a fresh session. */
export async function changePassword(current: string, next: string): Promise<void> {
  await api.post('/api/admin/password', { current, new: next }, { quiet401: true });
}
