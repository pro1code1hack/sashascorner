// Transport for the photo admin. `realApi` talks to /api through the shared admin
// client (admin-core/api.ts); `?mock=1` swaps in an
// in-memory stand-in (mock.ts) so the page can be built and checked before the
// backend exists. Both return the normalised shapes in types.ts.
import { adminHeaders, ApiError as CoreError, friendlyMessage, request } from '../admin-core/api';
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
// Session cookie + `X-Admin: 1` via the shared admin client. A 401 is not
// redirected here: the photo page keeps the owner's unsaved drafts and offers
// to sign in again in another tab (main.ts, `adm:unauth`).

async function call(path: string, init: { method?: 'GET' | 'PUT' | 'PATCH' | 'DELETE'; body?: unknown } = {}): Promise<unknown> {
  try {
    return await request<unknown>(init.method ?? 'GET', path, init.body, { quiet401: true });
  } catch (e) {
    if (e instanceof CoreError && e.status === 401) window.dispatchEvent(new CustomEvent('adm:unauth'));
    if (e instanceof CoreError) throw new ApiError(e.status, e.message, { detail: e.detail });
    throw e;
  }
}

export const realApi: Api = {
  async listMedia() {
    return mediaList(await call('/api/admin/media'));
  },

  upload(file, alt, onProgress) {
    return new Promise((resolve, reject) => {
      const xhr = new XMLHttpRequest();
      xhr.open('POST', '/api/admin/media');
      xhr.withCredentials = true;
      for (const [k, v] of Object.entries(adminHeaders(false))) xhr.setRequestHeader(k, v);
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
          if (xhr.status === 401) window.dispatchEvent(new CustomEvent('adm:unauth'));
          reject(new ApiError(xhr.status, detailOf(body, friendlyMessage(xhr.status, null)), body));
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
    const body = await call(`/api/admin/media/${id}`, { method: 'PATCH', body: { alt } });
    return body && typeof body === 'object' ? normMedia(body) : null;
  },

  async deleteMedia(id, force) {
    await call(`/api/admin/media/${id}${force ? '?force=1' : ''}`, { method: 'DELETE' });
  },

  async slots() {
    return slotList(await call('/api/slots'));
  },

  async putSlot(key, items) {
    const body = await call(`/api/admin/slots/${encodeURIComponent(key)}`, {
      method: 'PUT',
      body: {
        items: items.map((i) => ({
          media_id: i.media_id,
          ...(i.alt.trim() ? { alt: i.alt.trim() } : {}),
          focal: { x: round(i.focal.x), y: round(i.focal.y) },
        })),
      },
    });
    const r = body as Raw | null;
    return r && Array.isArray(r.items) ? normSlot(key, r) : null;
  },
};

export const round = (v: number) => Math.round(v * 1000) / 1000;
