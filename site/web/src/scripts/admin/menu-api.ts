// Website menu admin: transport and shapes (site/ADMIN.md, "Website menu").
// Everything the server sends is normalised here, so the screen reads one shape
// whatever small naming differences the backend settles on. `?mock=1` routes to
// an in-memory stand-in built from the static menu.json (menu-mock.ts).
import { api, isMock } from '../admin-core/api';
import { mockMenuApi } from './menu-mock';

export type Source = 'ops' | 'board';

export interface Size {
  code: string;
  label: string;
  price_pence: number | null;
}

export interface WebMeta {
  description: string | null;
  signature: boolean;
  hidden: boolean;
  position: number | null;
  /** Show the back-office note on the website instead of the description. */
  use_ops_note: boolean;
}

export interface Item {
  key: string;
  name: string;
  sizes: Size[];
  seasonal: string | null;
  /** False when the item's season is closed: the website hides it until then. */
  in_season: boolean;
  /** The back-office (or board) note, shown on the website when there is no description. */
  note: string | null;
  web: WebMeta;
}

export interface Category {
  name: string;
  slug: string;
  kind: 'DRINKS' | 'FOOD' | null;
  blurb: string;
  hidden: boolean;
  position: number | null;
  items: Item[];
}

export interface PriceMismatch {
  name: string;
  size: string;
  board: number | null;
  ops: number | null;
}

export interface Drift {
  price_mismatches: PriceMismatch[];
  board_only: string[];
  ops_only: string[];
  /** Set when the comparison itself could not run. */
  error: string | null;
}

export interface MenuAdmin {
  source: Source;
  warnings: string[];
  categories: Category[];
  unassigned: Item[];
  drift: Drift;
}

export interface OrderBody {
  categories: string[];
  items: Record<string, string[]>;
}

export interface MenuApi {
  load(): Promise<MenuAdmin>;
  putCategory(slug: string, body: { blurb?: string | null; hidden?: boolean }): Promise<void>;
  putItem(key: string, body: { description?: string | null; signature?: boolean; hidden?: boolean; use_ops_note?: boolean }): Promise<void>;
  order(body: OrderBody): Promise<void>;
}

// ---- normalisers ------------------------------------------------------------

type Raw = Record<string, unknown>;
const str = (v: unknown, d = ''): string => (typeof v === 'string' ? v : d);
const strOrNull = (v: unknown): string | null => (typeof v === 'string' && v.trim() ? v : null);
const numOrNull = (v: unknown): number | null => (typeof v === 'number' && Number.isFinite(v) ? v : null);

function normSize(v: unknown): Size {
  const r = (v ?? {}) as Raw;
  const code = str(r.code, 'One');
  return { code, label: str(r.label, code), price_pence: numOrNull(r.price_pence) };
}

function normItem(v: unknown): Item {
  const r = (v ?? {}) as Raw;
  const w = (r.web ?? {}) as Raw;
  const seasonal = r.seasonal;
  const name = str(r.name, str(r.key));
  return {
    key: str(r.key, name),
    name,
    sizes: Array.isArray(r.sizes) ? r.sizes.map(normSize) : [],
    seasonal:
      typeof seasonal === 'string' ? seasonal : seasonal && typeof seasonal === 'object' ? strOrNull((seasonal as Raw).name) : null,
    in_season: !(seasonal && typeof seasonal === 'object' && (seasonal as Raw).in_season === false),
    note: strOrNull(r.ops_note ?? r.note ?? r.back_office_note),
    web: {
      description: strOrNull(w.description),
      signature: w.signature === true,
      hidden: w.hidden === true,
      position: numOrNull(w.position),
      use_ops_note: w.use_ops_note === true,
    },
  };
}

function normCategory(v: unknown): Category {
  const r = (v ?? {}) as Raw;
  const kind = str(r.kind).toUpperCase();
  return {
    name: str(r.name, str(r.slug)),
    slug: str(r.slug),
    kind: kind === 'DRINKS' || kind === 'FOOD' ? kind : null,
    blurb: str(r.blurb),
    hidden: r.hidden === true,
    position: numOrNull(r.position),
    items: Array.isArray(r.items) ? r.items.map(normItem) : [],
  };
}

/** A drift entry may be a tuple (board name, ops name, size, board pence, ops pence) or an object. */
function normMismatch(v: unknown): PriceMismatch {
  if (Array.isArray(v)) {
    return { name: str(v[0], str(v[1])), size: str(v[2]), board: numOrNull(v[3]), ops: numOrNull(v[4]) };
  }
  const r = (v ?? {}) as Raw;
  return {
    name: str(r.board_name ?? r.name ?? r.item ?? r.ops_name),
    size: str(r.size ?? r.size_code ?? r.code),
    board: numOrNull(r.board_pence ?? r.board ?? r.board_price_pence),
    ops: numOrNull(r.ops_pence ?? r.ops ?? r.ops_price_pence),
  };
}

function nameOf(v: unknown): string {
  if (typeof v === 'string') return v.replace(/\s+\[[^\]]*\]$/, '').trim();
  const r = (v ?? {}) as Raw;
  return str(r.name ?? r.item ?? r.key);
}

export function normMenu(v: unknown): MenuAdmin {
  const r = (v ?? {}) as Raw;
  const d = (r.drift ?? {}) as Raw;
  const list = (x: unknown) => (Array.isArray(x) ? x : []);
  return {
    source: r.source === 'ops' ? 'ops' : 'board',
    warnings: list(r.warnings).filter((w): w is string => typeof w === 'string' && !!w.trim()),
    categories: list(r.categories).map(normCategory),
    unassigned: list(r.unassigned).map(normItem),
    drift: {
      price_mismatches: list(d.price_mismatches).map(normMismatch).filter((m) => m.name),
      board_only: list(d.board_only).map(nameOf).filter(Boolean),
      ops_only: list(d.ops_only).map(nameOf).filter(Boolean),
      error: strOrNull(d.error),
    },
  };
}

// ---- real transport ------------------------------------------------------------

const enc = encodeURIComponent;

const realMenuApi: MenuApi = {
  async load() {
    return normMenu(await api.get<unknown>('/api/admin/menu'));
  },
  async putCategory(slug, body) {
    await api.put(`/api/admin/menu/categories/${enc(slug)}`, body);
  },
  async putItem(key, body) {
    await api.put(`/api/admin/menu/items/${enc(key)}`, body);
  },
  async order(body) {
    await api.post('/api/admin/menu/order', body);
  },
};

export const menuApi: MenuApi = isMock() ? mockMenuApi : realMenuApi;
