// In-memory stand-in for the media API, enabled with `/admin/photos?mock=1`.
// (The admin shell signs in; this only stands in for the photo endpoints.)
// Uploads fake their progress; files named *fail* are refused with 415.
import type { Api } from './api';
import { ApiError, type Media, type Slot } from './types';

const wait = (ms: number) => new Promise((r) => setTimeout(r, ms));

const REGISTRY: Omit<Slot, 'items'>[] = [
  { key: 'home.hero', label: 'Home — opening photo', page: '/', aspect: '16/9', multiple: false, max: 1, hint: 'The room or the counter, warm light, no people close up' },
  { key: 'home.signatures', label: 'Home — signature drinks', page: '/', aspect: '1/1', multiple: true, max: 4, hint: 'Matcha, Raff, rose latte, bubble tea — one drink per photo' },
  { key: 'about.room.hero', label: 'About — large room photo', page: '/about', aspect: '4/5', multiple: false, max: 1, hint: 'Wide shot of the room from the door, daylight' },
  { key: 'about.gallery', label: 'About — gallery strip', page: '/about', aspect: '4/3', multiple: true, max: 6, hint: 'Details: the tree, the cat prints, the chess set, the window' },
  { key: 'order.hero', label: 'Order — takeaway photo', page: '/order', aspect: '3/2', multiple: false, max: 1, hint: 'A drink in a takeaway cup, ideally by the window' },
  { key: 'visit.storefront', label: 'Visit — shop front', page: '/visit', aspect: '3/2', multiple: false, max: 1, hint: 'Commercial Street frontage so people recognise it' },
  { key: 'menu.header', label: 'Menu — header photo', page: '/menu', aspect: '21/9', multiple: false, max: 1, hint: 'A row of drinks on the counter' },
];

const SEED = ['interior', 'storefront', 'blue-and-green-matcha', 'winter-lattes', 'cat-print'];

let nextId = 1;
const media = new Map<number, Media & { at: number }>();
const assigned = new Map<string, { media_id: number; alt?: string; focal: { x: number; y: number } }[]>();

for (const name of SEED) {
  const id = nextId++;
  media.set(id, {
    id,
    src: `/img/cafe/${name}.webp`,
    srcset: `/img/cafe/${name}.webp 1050w`,
    width: 1050,
    height: 1400,
    alt: name === 'cat-print' ? '' : name.replace(/-/g, ' ').replace(/^./, (c) => c.toUpperCase()),
    original_name: `${name}.jpg`,
    bytes: 240_000 + id * 13_000,
    blur: '',
    usage: [],
    at: id,
  });
}
assigned.set('about.room.hero', [{ media_id: 1, focal: { x: 0.5, y: 0.4 } }]);
assigned.set('visit.storefront', [{ media_id: 2, focal: { x: 0.5, y: 0.5 } }]);
assigned.set('about.gallery', [
  { media_id: 5, focal: { x: 0.5, y: 0.3 } },
  { media_id: 3, alt: 'Blue and green matcha on the counter', focal: { x: 0.5, y: 0.5 } },
]);

function usageOf(id: number) {
  return [...assigned].filter(([, items]) => items.some((i) => i.media_id === id)).map(([k]) => k);
}

function slotsNow(): Slot[] {
  return REGISTRY.map((s) => ({
    ...s,
    items: (assigned.get(s.key) ?? []).flatMap((i) => {
      const m = media.get(i.media_id);
      return m ? [{ media_id: m.id, src: m.src, srcset: m.srcset, width: m.width, height: m.height, alt: i.alt ?? m.alt, focal: i.focal, blur: '' }] : [];
    }),
  }));
}

export function mockLogin(password: string) {
  if (password === 'down') throw new ApiError(503, 'Admin password not configured.');
  if (password !== 'mock') throw new ApiError(401, 'Unauthorised');
}

export const mockApi: Api = {
  async listMedia() {
    await wait(200);
    return [...media.values()].sort((a, b) => b.at - a.at).map((m) => ({ ...m, usage: usageOf(m.id) }));
  },

  async upload(file, alt, onProgress) {
    for (let p = 0; p <= 1; p += 0.1) {
      onProgress(p);
      await wait(120);
    }
    if (/fail/i.test(file.name)) throw new ApiError(415, 'Only JPEG, PNG or WebP images are accepted.');
    const dup = [...media.values()].find((m) => m.original_name === file.name && m.bytes === file.size);
    if (dup) return { media: { ...dup, usage: usageOf(dup.id) }, duplicate: true };
    const url = URL.createObjectURL(file);
    const dims = await new Promise<[number, number]>((r) => {
      const img = new Image();
      img.onload = () => r([img.naturalWidth, img.naturalHeight]);
      img.onerror = () => r([1200, 900]);
      img.src = url;
    });
    const id = nextId++;
    const m = { id, src: url, srcset: '', width: dims[0], height: dims[1], alt, original_name: file.name, bytes: file.size, blur: '', usage: [], at: id + 100 };
    media.set(id, m);
    return { media: m, duplicate: false };
  },

  async patchAlt(id, alt) {
    await wait(250);
    const m = media.get(id);
    if (!m) throw new ApiError(404, 'Not found');
    m.alt = alt;
    return { ...m, usage: usageOf(id) };
  },

  async deleteMedia(id, force) {
    await wait(300);
    const used = usageOf(id);
    if (used.length && !force) throw new ApiError(409, 'This photo is in use.', { detail: { message: 'in use', usage: used } });
    for (const [k, items] of assigned) assigned.set(k, items.filter((i) => i.media_id !== id));
    media.delete(id);
  },

  async slots() {
    await wait(200);
    return slotsNow();
  },

  async putSlot(key, items) {
    await wait(450);
    const reg = REGISTRY.find((s) => s.key === key);
    if (!reg) throw new ApiError(422, 'Unknown slot.');
    if (items.length > reg.max) throw new ApiError(422, `This place holds at most ${reg.max}.`);
    if (items.some((i) => i.alt === 'fail')) throw new ApiError(500, 'Simulated failure.');
    assigned.set(key, items.map((i) => ({ media_id: i.media_id, alt: i.alt.trim() || undefined, focal: i.focal })));
    return slotsNow().find((s) => s.key === key) ?? null;
  },
};
