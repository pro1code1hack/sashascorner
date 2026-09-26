// Build-time view of the image slots: src/data/slots.json, written by
// `sashasite slots-export` in the same shape as GET /api/slots. Read through a glob
// (as lib/data.ts does) so a checkout without the export still builds -- every slot
// then renders its placeholder.
export interface SlotItem {
  media_id: number | null;
  src: string;
  srcset: string | string[];
  width: number;
  height: number;
  alt: string;
  focal: { x: number; y: number };
  blur: string | null;
}
export interface SlotDef {
  label: string;
  page: string;
  aspect: string;
  multiple: boolean;
  max: number;
  hint: string;
  items: SlotItem[];
}

const files = import.meta.glob<{ default: unknown }>('../data/slots.json', { eager: true });
const raw = (files['../data/slots.json']?.default as { slots?: Record<string, SlotDef> } | undefined) ?? null;
export const slots: Record<string, SlotDef> = raw?.slots ?? {};
export const hasExport = raw !== null;

export const slotDef = (key: string): SlotDef | null => slots[key] ?? null;
export const slotItem = (key: string, index = 0): SlotItem | null => slots[key]?.items?.[index] ?? null;
/** How many positions a page should loop over for this slot. */
export const slotCount = (key: string, fallback = 1): number => {
  const d = slots[key];
  return d ? (d.multiple ? d.max : 1) : fallback;
};

export const srcsetString = (s: SlotItem['srcset']) => (Array.isArray(s) ? s.join(', ') : s);
/** The widest variant, for links to the full image (lightbox, no-JS fallback). */
export const largest = (it: SlotItem): string => {
  let best = it.src;
  let bw = 0;
  for (const part of srcsetString(it.srcset).split(',')) {
    const [url, w] = part.trim().split(/\s+/);
    const n = parseInt(w ?? '', 10);
    if (url && n > bw) {
      bw = n;
      best = url;
    }
  }
  return best;
};
/** Stable fingerprint of what a slot position shows; the runtime script compares it. */
export const signature = (it: SlotItem | null) => (it ? `${it.src}|${it.focal.x},${it.focal.y}|${it.alt}` : '');
/** Public-facing half of a label ("About — the team" -> "The team"). */
export const shortLabel = (label: string) => {
  const s = label.includes(' — ') ? label.split(' — ').slice(1).join(' — ') : label;
  return s.charAt(0).toUpperCase() + s.slice(1);
};
