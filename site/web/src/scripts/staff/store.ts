// What this device remembers: its pairing token, the signed-in session, and the
// drinks picked most recently for rewards. localStorage only; every access is
// guarded because a locked-down browser can throw on it.

import type { StaffUser } from './types';

const K = {
  device: 'sc-staff-device',
  deviceName: 'sc-staff-device-name',
  session: 'sc-staff-session',
  recents: 'sc-staff-recent-drinks',
} as const;

function read(key: string): string | null {
  try {
    return localStorage.getItem(key);
  } catch {
    return null;
  }
}
function write(key: string, value: string | null): void {
  try {
    if (value === null) localStorage.removeItem(key);
    else localStorage.setItem(key, value);
  } catch {
    /* private mode: the device simply forgets */
  }
}

// In mock mode the device keeps a separate set of keys, so trying the mock on a
// real till never signs the real session out.
const ns = (key: string) => (isMockMode() ? `${key}-mock` : key);

export function isMockMode(): boolean {
  try {
    const q = new URLSearchParams(location.search).get('mock');
    if (q === '1') sessionStorage.setItem('sc-staff-mock', '1');
    if (q === '0') sessionStorage.removeItem('sc-staff-mock');
    return sessionStorage.getItem('sc-staff-mock') === '1';
  } catch {
    return new URLSearchParams(location.search).get('mock') === '1';
  }
}

export const device = {
  token: (): string | null => read(ns(K.device)),
  name: (): string | null => read(ns(K.deviceName)),
  set(token: string, name: string): void {
    write(ns(K.device), token);
    write(ns(K.deviceName), name);
  },
  clear(): void {
    write(ns(K.device), null);
    write(ns(K.deviceName), null);
  },
};

export interface Session {
  token: string;
  expires_at: string;
  user: StaffUser;
}

export const session = {
  get(): Session | null {
    const raw = read(ns(K.session));
    if (!raw) return null;
    try {
      const s = JSON.parse(raw) as Session;
      if (!s.token || !s.expires_at || Date.parse(s.expires_at) <= Date.now()) {
        write(ns(K.session), null);
        return null;
      }
      return s;
    } catch {
      return null;
    }
  },
  set(s: Session): void {
    write(ns(K.session), JSON.stringify(s));
  },
  clear(): void {
    write(ns(K.session), null);
  },
};

const MAX_RECENTS = 8;

export const recents = {
  /** menu_item_ids, most recent first. */
  get(): number[] {
    try {
      const v = JSON.parse(read(ns(K.recents)) ?? '[]');
      return Array.isArray(v) ? v.filter((n): n is number => Number.isInteger(n)).slice(0, MAX_RECENTS) : [];
    } catch {
      return [];
    }
  },
  push(id: number): void {
    const next = [id, ...recents.get().filter((x) => x !== id)].slice(0, MAX_RECENTS);
    write(ns(K.recents), JSON.stringify(next));
  },
};
