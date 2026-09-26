// Shared admin state: the library, the slot registry as last saved, and the
// owner's unsaved drafts. Views subscribe to coarse change events.
import type { Api } from './api';
import type { DraftItem, Media, Slot } from './types';

type Topic = 'media' | 'slots' | 'dirty' | 'saved';

export const state = {
  api: null as unknown as Api,
  media: new Map<number, Media>(),
  /** Library order, newest first as the server sends it. */
  mediaOrder: [] as number[],
  slots: new Map<string, Slot>(),
  drafts: new Map<string, DraftItem[]>(),
  saved: new Map<string, string>(),
  /** Which item of a multiple slot is open in the focal editor. */
  selected: new Map<string, number>(),
};

const subs = new Map<Topic, Set<() => void>>();
export function on(topic: Topic, fn: () => void) {
  if (!subs.has(topic)) subs.set(topic, new Set());
  subs.get(topic)!.add(fn);
}
export function emit(topic: Topic) {
  subs.get(topic)?.forEach((fn) => fn());
}

export function setMedia(list: Media[]) {
  state.media = new Map(list.map((m) => [m.id, m]));
  state.mediaOrder = list.map((m) => m.id);
  emit('media');
}

export function putMedia(m: Media, front = true) {
  const known = state.media.has(m.id);
  state.media.set(m.id, m);
  if (!known) state.mediaOrder = front ? [m.id, ...state.mediaOrder] : [...state.mediaOrder, m.id];
  emit('media');
}

const snap = (items: DraftItem[]) => JSON.stringify(items.map((i) => [i.media_id, i.alt.trim(), round(i.focal.x), round(i.focal.y)]));
const round = (v: number) => Math.round(v * 1000) / 1000;

/** Turn a slot as served into an editable draft. The API gives `alt` as
 *  `alt_override ?? media.alt`; an override is whatever differs from the photo. */
export function draftOf(slot: Slot): DraftItem[] {
  return slot.items.map((i) => {
    const m = state.media.get(i.media_id);
    return { media_id: i.media_id, alt: m && i.alt !== m.alt ? i.alt : '', focal: { ...i.focal } };
  });
}

/** Replace the saved copy of one slot (after load or save). Keeps the draft
 *  unless `resetDraft`. */
export function setSlot(slot: Slot, resetDraft = true) {
  state.slots.set(slot.key, slot);
  const d = draftOf(slot);
  state.saved.set(slot.key, snap(d));
  if (resetDraft || !state.drafts.has(slot.key)) state.drafts.set(slot.key, d);
}

export function setSlots(list: Slot[]) {
  const keep = new Map([...state.drafts].filter(([k]) => isDirty(k)));
  state.slots.clear();
  for (const s of list) setSlot(s, !keep.has(s.key));
  emit('slots');
  emit('dirty');
}

export function isDirty(key: string) {
  const d = state.drafts.get(key);
  return !!d && snap(d) !== state.saved.get(key);
}

export function dirtyKeys() {
  return [...state.slots.keys()].filter(isDirty);
}

export function revert(key: string) {
  const s = state.slots.get(key);
  if (s) state.drafts.set(key, draftOf(s));
  emit('dirty');
}

/** Thumbnail data for a draft item: from the library, else from the slot as served. */
export function imageFor(key: string, mediaId: number) {
  const m = state.media.get(mediaId);
  if (m) return m;
  const it = state.slots.get(key)?.items.find((i) => i.media_id === mediaId);
  return it ? { ...it, id: it.media_id, original_name: '', bytes: 0, usage: [] as string[] } : null;
}
