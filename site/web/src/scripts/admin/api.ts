// Transport for the photo admin. `realApi` talks to /api; `?mock=1` swaps in an
// in-memory stand-in (mock.ts) so the page can be built and checked before the
// backend exists. Both return the normalised shapes in types.ts.
import { ApiError, type DraftItem, type Focal, type Media, type Slot, type SlotItem } from './types';

export interface UploadResult {
  media: Media;
  duplicate: boolean;
}

export interface Api {
  listMedia(): Promise<Media[]>;
  upload(file: File, alt: string, onProgress: (fraction: number) => void): Promise<UploadResult>;
  patchAlt(id: number, alt: string): Promise<Media | null>;
  deleteMedia(id: number, force: boolean): Promise<void>;
  slots(): Promise<Slot[]>;
  putSlot(key: string, items: DraftItem[]): Promise<Slot | null>;
}

// ---- credentials: sessionStorage only --------------------------------------

const KEY = 'sc-admin-auth';

export function basicHeader(password: string): string {
  // btoa() only takes Latin-1; encode UTF-8 first so any password works.
  const bytes = new TextEncoder().encode(`owner:${password}`);
  let bin = '';
  bytes.forEach((b) => (bin += String.fromCharCode(b)));
  return `Basic ${btoa(bin)}`;
}

export const auth = {
  get(): string | null {
    try {
      return sessionStorage.getItem(KEY);
    } catch {
      return memo;
    }
  },
  set(header: string | null) {
    memo = header;
    try {
      if (header) sessionStorage.setItem(KEY, header);
      else sessionStorage.removeItem(KEY);
    } catch {
      /* private mode: keep it in memory for this tab */
    }
  },
};
let memo: string | null = null;

// ---- normalisers -----------------------------------------------------------

type Raw = Record<string, unknown>;
const str = (v: unknown, d = '') => (typeof v === 'string' ? v : d);
const num = (v: unknown, d = 0) => (typeof v === 'number' && Number.isFinite(v) ? v : d);
const clamp01 = (v: number) => Math.min(1, Math.max(0, v));

function normFocal(v: unknown): Focal {
  const f = (v ?? {}) as Raw;
  return { x: clamp01(num(f.x, 0.5)), y: clamp01(num(f.y, 0.5)) };
}

function usageKeys(r: Raw): string[] {
  const src = r.usage ?? r.used_in ?? r.slots ?? [];
  if (!Array.isArray(src)) return [];
  const keys = src
    .map((u) => (typeof u === 'string' ? u : str((u as Raw)?.slot_key ?? (u as Raw)?.key)))
    .filter(Boolean);
  return [...new Set(keys)];
}

export function normMedia(v: unknown): Media {
  const r = (v ?? {}) as Raw;
  return {
    id: num(r.id ?? r.media_id),
    src: str(r.src),
    srcset: str(r.srcset),
    width: num(r.width),
    height: num(r.height),
    alt: str(r.alt),
    original_name: str(r.original_name),
    bytes: num(r.bytes),
    blur: str(r.blur),
    usage: usageKeys(r),
  };
}

function normItem(v: unknown): SlotItem {
  const r = (v ?? {}) as Raw;
  return {
    media_id: num(r.media_id ?? r.id),
    src: str(r.src),
    srcset: str(r.srcset),
    width: num(r.width),
    height: num(r.height),
    alt: str(r.alt),
    focal: normFocal(r.focal),
    blur: str(r.blur),
  };
}

export function normSlot(key: string, v: unknown): Slot {
  const r = (v ?? {}) as Raw;
  const multiple = r.multiple === true;
  return {
    key,
    label: str(r.label, key),
    page: str(r.page, '/'),
    aspect: str(r.aspect, '4/3'),
    multiple,
    max: Math.max(1, num(r.max, 1)),
    hint: str(r.hint),
    items: Array.isArray(r.items) ? r.items.map(normItem) : [],
  };
}

function mediaList(v: unknown): Media[] {
  const r = v as Raw;
  const arr = Array.isArray(v) ? v : (r?.items ?? r?.media ?? []);
  return Array.isArray(arr) ? arr.map(normMedia) : [];
}

function slotList(v: unknown): Slot[] {
  const map = ((v as Raw)?.slots ?? {}) as Raw;
  return Object.entries(map).map(([k, s]) => normSlot(k, s));
}

/** A readable sentence from a FastAPI error body. */
export function detailOf(body: unknown, fallback: string): string {
  const d = (body as Raw | null)?.detail;
  if (typeof d === 'string') return d;
  if (d && typeof d === 'object' && !Array.isArray(d) && typeof (d as Raw).message === 'string') return (d as Raw).message as string;
  if (Array.isArray(d) && d[0] && typeof (d[0] as Raw).msg === 'string') return (d[0] as Raw).msg as string;
  return fallback;
}

// ---- real transport --------------------------------------------------------

async function call(path: string, init: RequestInit = {}, admin = true): Promise<unknown> {
  const headers = new Headers(init.headers);
  const a = auth.get();
  if (admin && a) headers.set('Authorization', a);
  let r: Response;
  try {
    // credentials: 'omit' keeps the browser from answering the server's
    // `WWW-Authenticate: Basic` 401 with its own native password prompt; the
    // Authorization header we set by hand is still sent.
    r = await fetch(path, { ...init, headers, cache: 'no-store', credentials: 'omit' });
  } catch {
    throw new ApiError(0, 'Could not reach the server. Check the connection and try again.');
  }
  const body = r.status === 204 ? null : await r.json().catch(() => null);
  if (!r.ok) throw new ApiError(r.status, detailOf(body, `The server said ${r.status}.`), body);
  return body;
}

export const realApi: Api = {
  async listMedia() {
    return mediaList(await call('/api/admin/media'));
  },

  upload(file, alt, onProgress) {
    return new Promise((resolve, reject) => {
      const xhr = new XMLHttpRequest();
      xhr.open('POST', '/api/admin/media');
      const a = auth.get();
      if (a) xhr.setRequestHeader('Authorization', a);
      xhr.upload.addEventListener('progress', (e) => {
        if (e.lengthComputable) onProgress(e.loaded / e.total);
      });
      xhr.addEventListener('load', () => {
        let body: unknown = null;
        try {
          body = JSON.parse(xhr.responseText);
        } catch {
          /* not JSON */
        }
        if (xhr.status >= 200 && xhr.status < 300) {
          onProgress(1);
          resolve({ media: normMedia(body), duplicate: xhr.status === 200 });
        } else {
          reject(new ApiError(xhr.status, detailOf(body, `The server said ${xhr.status}.`), body));
        }
      });
      xhr.addEventListener('error', () => reject(new ApiError(0, 'The upload was interrupted. Check the connection and try again.')));
      const fd = new FormData();
      fd.append('file', file);
      fd.append('alt', alt);
      xhr.send(fd);
    });
  },

  async patchAlt(id, alt) {
    const body = await call(`/api/admin/media/${id}`, {
      method: 'PATCH',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ alt }),
    });
    return body && typeof body === 'object' ? normMedia(body) : null;
  },

  async deleteMedia(id, force) {
    await call(`/api/admin/media/${id}${force ? '?force=1' : ''}`, { method: 'DELETE' });
  },

  async slots() {
    return slotList(await call('/api/slots', {}, false));
  },

  async putSlot(key, items) {
    const body = await call(`/api/admin/slots/${encodeURIComponent(key)}`, {
      method: 'PUT',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        items: items.map((i) => ({
          media_id: i.media_id,
          ...(i.alt.trim() ? { alt: i.alt.trim() } : {}),
          focal: { x: round(i.focal.x), y: round(i.focal.y) },
        })),
      }),
    });
    const r = body as Raw | null;
    return r && Array.isArray(r.items) ? normSlot(key, r) : null;
  },
};

export const round = (v: number) => Math.round(v * 1000) / 1000;
