// Slots: every place on the site that shows a photo, grouped by page. The owner
// picks or uploads photos, orders them, sets a focal point and saves per slot.
import { $, announce, h, PAGE_ORDER, pageName, pct, phoneAspect, slotAnchor } from './dom';
import { thumb, uploadFiles, reloadMedia } from './library';
import { dirtyKeys, emit, imageFor, isDirty, on, revert, setSlot, state } from './state';
import { ApiError, type DraftItem, type Focal, type Slot } from './types';

const articles = new Map<string, HTMLElement>();
const status = new Map<string, { text: string; state: 'ok' | 'bad' | 'busy' | '' }>();

const draft = (key: string) => state.drafts.get(key) ?? [];
const room = (s: Slot) => (s.multiple ? s.max : 1) - draft(s.key).length;
const aspectLabel = (a: string) => a.replace(/\s*\/\s*/, ':');

function change(key: string, items: DraftItem[], rerender = true) {
  state.drafts.set(key, items);
  status.set(key, { text: '', state: '' });
  if (rerender) renderSlot(key);
  emit('dirty');
}

// ---- page layout ------------------------------------------------------------------

export function renderSlots() {
  const root = $('[data-slots]');
  const byPage = new Map<string, Slot[]>();
  for (const s of state.slots.values()) {
    if (!byPage.has(s.page)) byPage.set(s.page, []);
    byPage.get(s.page)!.push(s);
  }
  const pages = [...byPage.keys()].sort((a, b) => {
    const ia = PAGE_ORDER.indexOf(a), ib = PAGE_ORDER.indexOf(b);
    return (ia < 0 ? 99 : ia) - (ib < 0 ? 99 : ib) || a.localeCompare(b);
  });
  articles.clear();
  root.replaceChildren(
    ...pages.map((page) => {
      const id = `page-${page.replace(/[^a-z0-9]+/gi, '-') || 'home'}`;
      return h(
        'section',
        { class: 'adm-page', 'aria-labelledby': id },
        h(
          'header',
          { class: 'adm-page__head' },
          h('h3', { id }, `${pageName(page)} page`),
          h('a', { href: page, target: '_blank', rel: 'noopener', class: 'adm-link' }, 'Open the live page', h('span', { class: 'sr-only' }, ' (opens in a new tab)'), ' ↗'),
        ),
        byPage.get(page)!.map((s) => {
          const art = h('article', { class: 'adm-slot', id: slotAnchor(s.key), tabindex: '-1', 'aria-labelledby': `${slotAnchor(s.key)}-t` });
          articles.set(s.key, art);
          renderSlot(s.key);
          return art;
        }),
      );
    }),
  );
  if (!pages.length) root.append(h('p', { class: 'adm-muted' }, 'No photo places are set up on the site yet.'));
}

// ---- one slot ---------------------------------------------------------------------

function renderSlot(key: string) {
  const s = state.slots.get(key);
  const art = articles.get(key);
  if (!s || !art) return;
  const focusKey = art.contains(document.activeElement) ? (document.activeElement as HTMLElement).dataset.f : undefined;

  const items = draft(key);
  const fill = s.multiple ? `${items.length} of ${s.max}` : items.length ? 'Has a photo' : 'Empty';
  const isEmpty = items.length === 0;

  art.classList.toggle('is-empty', isEmpty);
  art.replaceChildren(
    h(
      'header',
      { class: 'adm-slot__head' },
      h('div', {},
        h('h4', { id: `${slotAnchor(key)}-t` }, s.label),
        s.hint ? h('p', { class: 'adm-slot__hint' }, s.hint) : null,
      ),
      h('p', { class: 'adm-slot__meta' },
        h('span', { class: 'adm-shape', style: `aspect-ratio:${s.aspect}`, 'aria-hidden': 'true' }),
        h('span', {}, `Shape ${aspectLabel(s.aspect)}`),
        h('span', { class: `adm-fill${isEmpty ? ' is-empty' : ''}` }, fill),
      ),
    ),
    s.multiple ? strip(s) : single(s),
    h('ul', { class: 'adm-uploads', 'data-slot-uploads': '', hidden: true }),
    footer(s),
  );

  if (focusKey) art.querySelector<HTMLElement>(`[data-f="${focusKey}"]`)?.focus();
}

function addButtons(s: Slot) {
  const left = s.multiple ? room(s) : 1;
  if (left <= 0) return h('p', { class: 'adm-muted adm-slot__full' }, `Full: this place shows up to ${s.max}. Remove one to add another.`);
  const input = h('input', {
    type: 'file',
    accept: 'image/jpeg,image/png,image/webp',
    multiple: s.multiple && left > 1,
    class: 'sr-only',
    tabindex: '-1',
    'aria-hidden': 'true',
  }) as HTMLInputElement;
  input.addEventListener('change', () => {
    const files = [...(input.files ?? [])];
    input.value = '';
    if (files.length) void uploadInto(s.key, files);
  });
  const filled = draft(s.key).length > 0;
  const verb = s.multiple ? (filled ? `Add up to ${left} more` : `Add up to ${left}`) : filled ? 'Replace' : 'Add a photo';
  return h(
    'div',
    { class: 'adm-slot__add' },
    h('span', { class: 'adm-slot__addlabel' }, `${verb}:`),
    h('button', { type: 'button', class: 'btn btn--ghost adm-btn', 'data-f': 'pick', onclick: () => openPicker(s.key) }, 'Choose from library'),
    h('button', { type: 'button', class: 'btn btn--ghost adm-btn', 'data-f': 'upload', onclick: () => input.click() }, 'Upload new'),
    input,
  );
}

function emptyFrame(s: Slot) {
  return h(
    'div',
    { class: 'adm-frame adm-frame--empty', style: `aspect-ratio:${s.aspect}` },
    h('p', {}, h('strong', {}, 'No photo yet'), s.hint ? h('span', {}, s.hint) : null),
  );
}

function single(s: Slot) {
  const items = draft(s.key);
  if (!items.length) return h('div', { class: 'adm-slot__body' }, emptyFrame(s), addButtons(s));
  return h(
    'div',
    { class: 'adm-slot__body' },
    editor(s, 0),
    h('div', { class: 'adm-slot__row' },
      addButtons(s),
      h('button', {
        type: 'button', class: 'adm-link adm-link--danger', 'data-f': 'remove-0',
        onclick: () => { change(s.key, []); announce('Photo taken out of this place. Save to confirm.'); },
      }, 'Take this photo out'),
    ),
  );
}

// ---- ordered strip for galleries ------------------------------------------------------

function move(key: string, from: number, to: number, focus: string) {
  const items = [...draft(key)];
  if (to < 0 || to >= items.length || from === to) return;
  const [it] = items.splice(from, 1);
  items.splice(to, 0, it);
  const sel = state.selected.get(key) ?? 0;
  state.selected.set(key, sel === from ? to : sel);
  change(key, items, false);
  renderSlot(key);
  const art = articles.get(key);
  const opposite = focus === 'left' ? 'right' : focus === 'right' ? 'left' : 'grip';
  const target = [focus, opposite, 'grip']
    .map((f) => art?.querySelector<HTMLButtonElement>(`[data-f="${f}-${to}"]`))
    .find((b) => b && !b.disabled);
  target?.focus();
  announce(`Moved to position ${to + 1} of ${items.length}. Save to confirm.`);
}

function strip(s: Slot) {
  const items = draft(s.key);
  let sel = state.selected.get(s.key) ?? 0;
  if (sel >= items.length) sel = Math.max(0, items.length - 1);
  state.selected.set(s.key, sel);

  const list = h('ol', { class: 'adm-strip', 'aria-label': `${s.label}, in the order shown on the site` });
  items.forEach((it, i) => {
    const m = imageFor(s.key, it.media_id);
    const name = m?.alt || m?.original_name || `Photo ${i + 1}`;
    list.append(
      h(
        'li',
        { class: `adm-strip__item${i === sel ? ' is-selected' : ''}`, 'data-idx': String(i) },
        h(
          'button',
          {
            type: 'button', class: 'adm-strip__pick', 'data-f': `sel-${i}`, 'aria-pressed': i === sel ? 'true' : 'false',
            'aria-label': `Photo ${i + 1}: ${name}. Edit focal point and description`,
            style: `aspect-ratio:${s.aspect}`,
            onclick: () => { state.selected.set(s.key, i); renderSlot(s.key); },
          },
          m ? thumb(m, '120px', { 'data-focal-img': String(i), style: `object-position:${pct(it.focal.x)} ${pct(it.focal.y)}` }) : null,
          h('span', { class: 'adm-strip__n num', 'aria-hidden': 'true' }, String(i + 1)),
        ),
        h(
          'div',
          { class: 'adm-strip__tools' },
          h('button', { type: 'button', class: 'adm-icon', 'data-f': `left-${i}`, disabled: i === 0, 'aria-label': `Move photo ${i + 1} left`, onclick: () => move(s.key, i, i - 1, 'left') }, '←'),
          h('button', {
            type: 'button', class: 'adm-icon adm-grip', 'data-f': `grip-${i}`,
            'aria-label': `Drag to reorder photo ${i + 1}, or use the arrow keys`,
            onpointerdown: (e: Event) => startDrag(s.key, i, e as PointerEvent),
            onkeydown: (e: Event) => {
              const k = (e as KeyboardEvent).key;
              if (k === 'ArrowLeft' || k === 'ArrowUp') (e.preventDefault(), move(s.key, i, i - 1, 'grip'));
              if (k === 'ArrowRight' || k === 'ArrowDown') (e.preventDefault(), move(s.key, i, i + 1, 'grip'));
            },
          }, h('span', { 'aria-hidden': 'true' }, '⠿')),
          h('button', { type: 'button', class: 'adm-icon', 'data-f': `right-${i}`, disabled: i === items.length - 1, 'aria-label': `Move photo ${i + 1} right`, onclick: () => move(s.key, i, i + 1, 'right') }, '→'),
          h('button', {
            type: 'button', class: 'adm-icon adm-icon--x', 'data-f': `remove-${i}`, 'aria-label': `Take photo ${i + 1} out of this place`,
            onclick: () => {
              const next = draft(s.key).filter((_, j) => j !== i);
              if ((state.selected.get(s.key) ?? 0) >= next.length) state.selected.set(s.key, Math.max(0, next.length - 1));
              change(s.key, next);
              const art = articles.get(s.key);
              (art?.querySelector<HTMLElement>(`[data-f="remove-${Math.min(i, next.length - 1)}"]`) ?? art?.querySelector<HTMLElement>('[data-f="pick"]'))?.focus();
              announce(`Photo taken out. ${next.length} left. Save to confirm.`);
            },
          }, '×'),
        ),
      ),
    );
  });
  // Empty positions, so the owner sees how many the layout holds.
  for (let i = items.length; i < s.max; i++) {
    list.append(h('li', { class: 'adm-strip__item is-hole', 'aria-hidden': 'true' }, h('span', { class: 'adm-strip__hole', style: `aspect-ratio:${s.aspect}` }, String(i + 1))));
  }

  return h(
    'div',
    { class: 'adm-slot__body' },
    items.length ? null : emptyFrame(s),
    list,
    addButtons(s),
    items.length ? h('div', { class: 'adm-slot__sel' }, h('p', { class: 'adm-muted' }, `Editing photo ${sel + 1} of ${items.length}`), editor(s, sel)) : null,
  );
}

function startDrag(key: string, from: number, e: PointerEvent) {
  if (e.button !== 0) return;
  const grip = e.currentTarget as HTMLElement;
  const li = grip.closest('li') as HTMLElement;
  const list = li.parentElement as HTMLElement;
  const x0 = e.clientX, y0 = e.clientY;
  let dragging = false;
  // Listen on window, not with pointer capture: moving the <li> in the DOM
  // would release a capture held by the grip and strand the drag.
  e.preventDefault();

  const onMove = (ev: PointerEvent) => {
    if (!dragging) {
      if (Math.hypot(ev.clientX - x0, ev.clientY - y0) < 5) return;
      dragging = true;
      li.classList.add('is-dragging');
      list.classList.add('is-sorting');
    }
    const others = [...list.children].filter((c) => c !== li && !c.classList.contains('is-hole')) as HTMLElement[];
    for (const o of others) {
      const r = o.getBoundingClientRect();
      if (ev.clientX >= r.left && ev.clientX <= r.right && ev.clientY >= r.top && ev.clientY <= r.bottom) {
        const after = ev.clientX > r.left + r.width / 2;
        list.insertBefore(li, after ? o.nextSibling : o);
        break;
      }
    }
  };
  const onUp = () => {
    window.removeEventListener('pointermove', onMove);
    window.removeEventListener('pointerup', onUp);
    window.removeEventListener('pointercancel', onUp);
    li.classList.remove('is-dragging');
    list.classList.remove('is-sorting');
    if (!dragging) return;
    const to = [...list.children].filter((c) => !c.classList.contains('is-hole')).indexOf(li);
    if (to !== from && to >= 0) move(key, from, to, 'grip');
    else renderSlot(key);
  };
  window.addEventListener('pointermove', onMove);
  window.addEventListener('pointerup', onUp);
  window.addEventListener('pointercancel', onUp);
}

// ---- focal point + description editor ------------------------------------------------

function editor(s: Slot, idx: number) {
  const it = draft(s.key)[idx];
  const m = imageFor(s.key, it.media_id);
  if (!m) return h('p', { class: 'adm-muted' }, 'This photo is no longer in the library.');
  const r = m.width && m.height ? m.width / m.height : 4 / 3;
  const ratio = `${m.width || 4}/${m.height || 3}`;
  const pos = `${pct(it.focal.x)} ${pct(it.focal.y)}`;
  const phone = phoneAspect(s.aspect);
  const altId = `${slotAnchor(s.key)}-alt-${idx}`;
  const helpId = `${slotAnchor(s.key)}-fhelp`;

  const cross = h('span', { class: 'adm-cross', style: `left:${pct(it.focal.x)};top:${pct(it.focal.y)}`, 'aria-hidden': 'true' });
  const pad = h(
    'div',
    {
      class: 'adm-focal__pad', style: `aspect-ratio:${ratio};width:min(100%, ${Math.round(420 * r)}px)`, tabindex: '0', role: 'application',
      'data-f': `focal-${idx}`, 'aria-roledescription': 'focal point picker',
      'aria-label': focalLabel(it.focal), 'aria-describedby': helpId,
    },
    thumb(m, '(max-width: 700px) 90vw, 420px', { class: 'adm-focal__img' }),
    cross,
  );

  const frames = h(
    'div',
    { class: 'adm-focal__frames' },
    h('figure', { class: 'adm-crop' },
      h('div', { class: 'adm-crop__box', style: `aspect-ratio:${s.aspect}` }, thumb(m, '320px', { 'data-crop': '', style: `object-position:${pos}` })),
      h('figcaption', {}, `On the page (${aspectLabel(s.aspect)})`),
    ),
    h('figure', { class: 'adm-crop adm-crop--phone' },
      h('div', { class: 'adm-crop__box', style: `aspect-ratio:${phone}` }, thumb(m, '160px', { 'data-crop': '', style: `object-position:${pos}` })),
      h('figcaption', {}, `Narrow phone crop (${aspectLabel(phone)})`),
    ),
  );

  const set = (f: Focal, say = false) => {
    const next = [...draft(s.key)];
    next[idx] = { ...next[idx], focal: { x: clamp(f.x), y: clamp(f.y) } };
    const p = next[idx].focal;
    cross.style.left = pct(p.x);
    cross.style.top = pct(p.y);
    pad.setAttribute('aria-label', focalLabel(p));
    const op = `${pct(p.x)} ${pct(p.y)}`;
    articles.get(s.key)?.querySelectorAll<HTMLElement>(`[data-crop], [data-focal-img="${idx}"]`).forEach((img) => (img.style.objectPosition = op));
    change(s.key, next, false);
    if (say) announce(focalLabel(p));
  };
  const fromPointer = (e: PointerEvent) => {
    const r = pad.getBoundingClientRect();
    set({ x: (e.clientX - r.left) / r.width, y: (e.clientY - r.top) / r.height });
  };
  pad.addEventListener('pointerdown', (e) => {
    if (e.button !== 0) return;
    e.preventDefault();
    pad.focus();
    pad.setPointerCapture(e.pointerId);
    pad.classList.add('is-active');
    fromPointer(e);
    const mv = (ev: PointerEvent) => fromPointer(ev);
    const up = () => {
      pad.classList.remove('is-active');
      pad.removeEventListener('pointermove', mv);
      pad.removeEventListener('pointerup', up);
      pad.removeEventListener('pointercancel', up);
      announce(`${focalLabel(draft(s.key)[idx].focal)} Save to confirm.`);
    };
    pad.addEventListener('pointermove', mv);
    pad.addEventListener('pointerup', up);
    pad.addEventListener('pointercancel', up);
  });
  pad.addEventListener('keydown', (e) => {
    const step = e.shiftKey ? 0.1 : 0.02;
    const f = draft(s.key)[idx].focal;
    const d: Record<string, [number, number]> = { ArrowLeft: [-step, 0], ArrowRight: [step, 0], ArrowUp: [0, -step], ArrowDown: [0, step] };
    if (d[e.key]) {
      e.preventDefault();
      set({ x: f.x + d[e.key][0], y: f.y + d[e.key][1] }, true);
    } else if (e.key === 'Home' || e.key === 'c') {
      e.preventDefault();
      set({ x: 0.5, y: 0.5 }, true);
    }
  });

  const alt = h('input', {
    id: altId, class: 'input', type: 'text', maxlength: '300', autocomplete: 'off', 'data-f': `alt-${idx}`,
    value: it.alt,
    placeholder: m.alt ? `Uses the photo's description: “${m.alt}”` : 'Describe the photo as it appears here',
  }) as HTMLInputElement;
  alt.addEventListener('input', () => {
    const next = [...draft(s.key)];
    next[idx] = { ...next[idx], alt: alt.value };
    change(s.key, next, false);
  });

  return h(
    'div',
    { class: 'adm-editor' },
    h(
      'div',
      { class: 'adm-focal' },
      h('div', { class: 'adm-focal__main' },
        h('p', { class: 'adm-focal__title' }, 'Tap the most important part of the photo'),
        pad,
        h('p', { class: 'adm-muted adm-focal__help', id: helpId },
          'The crop keeps this spot in view. With a keyboard: arrow keys move it, Shift moves further, C centres it.'),
        h('button', { type: 'button', class: 'adm-link', 'data-f': `centre-${idx}`, onclick: () => set({ x: 0.5, y: 0.5 }, true) }, 'Centre it'),
      ),
      frames,
    ),
    h('div', { class: 'field adm-editor__alt' },
      h('label', { for: altId }, h('span', {}, 'Description for this place (optional)')),
      alt,
      h('p', { class: 'adm-muted' }, !m.alt && !it.alt
        ? 'This photo has no description yet. Add one here, or in the library below, for people using screen readers.'
        : 'Leave empty to use the description from the library.'),
    ),
  );
}

const clamp = (v: number) => Math.min(1, Math.max(0, v));
function focalLabel(f: Focal) {
  const across = f.x < 0.34 ? 'left' : f.x > 0.66 ? 'right' : 'centre';
  const down = f.y < 0.34 ? 'top' : f.y > 0.66 ? 'bottom' : 'middle';
  return `Focal point ${Math.round(f.x * 100)}% across, ${Math.round(f.y * 100)}% down (${down} ${across}).`;
}

// ---- save --------------------------------------------------------------------------

function footer(s: Slot) {
  const dirty = isDirty(s.key);
  const st = status.get(s.key) ?? { text: '', state: '' };
  return h(
    'footer',
    { class: 'adm-slot__foot' },
    h('button', { type: 'button', class: 'btn btn--caramel adm-btn', 'data-save': '', 'data-f': 'save', disabled: !dirty, onclick: () => void save(s.key) }, 'Save'),
    h('button', { type: 'button', class: 'adm-link', 'data-undo': '', 'data-f': 'undo', hidden: !dirty, onclick: () => { revert(s.key); status.delete(s.key); renderSlot(s.key); announce('Changes undone.'); } }, 'Undo changes'),
    h('p', { class: 'adm-status', role: 'status', 'data-status': '', 'data-state': dirty && !st.text ? 'dirty' : st.state }, dirty && !st.text ? 'Not saved yet' : st.text),
  );
}

/** Refresh the save controls without rebuilding the slot (keeps focus while typing). */
function refreshFooter(key: string) {
  const art = articles.get(key);
  const s = state.slots.get(key);
  if (!art || !s) return;
  const dirty = isDirty(key);
  const st = status.get(key) ?? { text: '', state: '' };
  const btn = art.querySelector<HTMLButtonElement>('[data-save]');
  if (btn && st.state !== 'busy') btn.disabled = !dirty;
  const undo = art.querySelector<HTMLElement>('[data-undo]');
  if (undo) undo.hidden = !dirty;
  const p = art.querySelector<HTMLElement>('[data-status]');
  if (p) {
    const text = dirty && !st.text ? 'Not saved yet' : st.text;
    if (p.textContent !== text) p.textContent = text;
    p.dataset.state = dirty && !st.text ? 'dirty' : st.state;
  }
  art.classList.toggle('is-dirty', dirty);
}

export async function save(key: string): Promise<boolean> {
  const s = state.slots.get(key);
  if (!s || !isDirty(key)) return true;
  const items = draft(key);
  status.set(key, { text: 'Saving…', state: 'busy' });
  const btn = articles.get(key)?.querySelector<HTMLButtonElement>('[data-save]');
  if (btn) btn.disabled = true;
  refreshFooter(key);
  try {
    const back = await state.api.putSlot(key, items);
    // Rebuild the saved copy from what we sent, enriched with the library's
    // image data; prefer the server's items when it returns them.
    const local: Slot = {
      ...s,
      items: items.map((d) => {
        const m = imageFor(key, d.media_id);
        return {
          media_id: d.media_id, src: m?.src ?? '', srcset: m?.srcset ?? '', width: m?.width ?? 0, height: m?.height ?? 0,
          alt: d.alt.trim() || m?.alt || '', focal: d.focal, blur: m?.blur ?? '',
        };
      }),
    };
    setSlot(back && back.items.length === items.length ? { ...s, items: back.items } : local, true);
    const t = new Intl.DateTimeFormat('en-GB', { hour: '2-digit', minute: '2-digit' }).format(new Date());
    status.set(key, { text: `Saved at ${t}. The site shows it within a minute.`, state: 'ok' });
    announce(`${s.label}: saved.`);
    renderSlot(key);
    emit('dirty');
    emit('saved');
    void reloadMedia().catch(() => {});
    return true;
  } catch (e) {
    const err = e as ApiError;
    const why =
      err.status === 401 ? 'you were signed out. Sign in again; your changes are still here.'
      : err.status === 422 ? err.message
      : err.status === 0 ? 'the server could not be reached. Check the connection and try again.'
      : err.message;
    status.set(key, { text: `Not saved: ${why}`, state: 'bad' });
    announce(`${s.label}: not saved. ${why}`);
    refreshFooter(key);
    if (err.status === 401) window.dispatchEvent(new CustomEvent('adm:unauth'));
    return false;
  }
}

// ---- adding photos -----------------------------------------------------------------

function place(key: string, ids: number[]) {
  const s = state.slots.get(key)!;
  const cur = draft(key);
  const fresh = ids.map((id) => ({ media_id: id, alt: '', focal: { x: 0.5, y: 0.5 } }));
  if (s.multiple) {
    const next = [...cur, ...fresh.filter((f) => !cur.some((c) => c.media_id === f.media_id))].slice(0, s.max);
    state.selected.set(key, Math.max(0, next.length - 1));
    change(key, next);
  } else {
    change(key, fresh.slice(0, 1));
  }
  const n = ids.length;
  announce(`${n === 1 ? 'Photo' : `${n} photos`} placed in ${s.label}. Set the focal point, then save.`);
  articles.get(key)?.querySelector<HTMLElement>('[data-f^="focal-"]')?.focus();
}

async function uploadInto(key: string, files: File[]) {
  const s = state.slots.get(key)!;
  const art = articles.get(key)!;
  const list = art.querySelector<HTMLElement>('[data-slot-uploads]')!;
  const done = await uploadFiles(files.slice(0, Math.max(1, room(s) || 1)), list);
  if (done.length) place(key, done.map((m) => m.id));
}

// ---- picker dialog -------------------------------------------------------------------

export function openPicker(key: string) {
  const s = state.slots.get(key);
  if (!s) return;
  const dlg = $('[data-picker]') as HTMLDialogElement;
  const grid = $('[data-picker-grid]', dlg);
  const ok = $('[data-picker-ok]', dlg) as HTMLButtonElement;
  const note = $('[data-picker-note]', dlg);
  const opener = document.activeElement as HTMLElement | null;
  const left = s.multiple ? room(s) : 1;
  const inSlot = new Set(draft(key).map((i) => i.media_id));
  const chosen: number[] = [];

  $('[data-picker-title]', dlg).textContent = `Choose ${s.multiple ? 'photos' : 'a photo'} for ${s.label}`;
  const update = () => {
    note.textContent = s.multiple ? `${chosen.length} of up to ${left} chosen` : '';
    ok.hidden = !s.multiple;
    (ok.parentElement as HTMLElement).hidden = !s.multiple;
    ok.disabled = chosen.length === 0;
    ok.textContent = chosen.length > 1 ? `Add ${chosen.length} photos` : 'Add photo';
    grid.querySelectorAll<HTMLButtonElement>('button[data-id]').forEach((b) => {
      const id = Number(b.dataset.id);
      const on = chosen.includes(id);
      b.setAttribute('aria-pressed', on ? 'true' : 'false');
      b.disabled = inSlot.has(id) || (!on && s.multiple && chosen.length >= left);
    });
  };

  const ids = state.mediaOrder;
  grid.replaceChildren(
    ...(ids.length
      ? ids.map((id) => {
          const m = state.media.get(id)!;
          const here = inSlot.has(id);
          return h('li', {},
            h('button', {
              type: 'button', class: 'adm-pick', 'data-id': String(id), 'aria-pressed': 'false',
              onclick: () => {
                if (!s.multiple) {
                  dlg.close();
                  place(key, [id]);
                  return;
                }
                const at = chosen.indexOf(id);
                if (at >= 0) chosen.splice(at, 1);
                else chosen.push(id);
                update();
              },
            },
            h('span', { class: 'adm-pick__img' }, thumb(m, '160px')),
            h('span', { class: 'adm-pick__cap' }, here ? 'Already here' : m.alt || m.original_name || `Photo ${id}`),
            s.multiple ? h('span', { class: 'adm-pick__tick', 'aria-hidden': 'true' }, '✓') : null,
            ));
        })
      : [h('li', { class: 'adm-pick__none' }, 'Your library is empty. Close this and use “Upload new”, or add photos in the library below.')]),
  );
  ok.onclick = () => {
    dlg.close();
    place(key, [...chosen]);
  };
  dlg.onclose = () => {
    if (!dlg.returnValue && document.contains(opener)) opener?.focus();
    dlg.returnValue = '';
  };
  update();
  dlg.showModal();
  (grid.querySelector<HTMLButtonElement>('button:not([disabled])') ?? ($('[data-picker-close]', dlg) as HTMLElement)).focus();
}

// ---- what's missing + unsaved bar ----------------------------------------------------------

const SHOW = 8;
let showAll = false;

export function renderMissing() {
  const box = $('[data-missing]');
  const all = [...state.slots.values()];
  const empty = all.filter((s) => s.items.length === 0);
  const partial = all.filter((s) => s.multiple && s.items.length > 0 && s.items.length < s.max);
  const noAlt = state.mediaOrder.filter((id) => !state.media.get(id)?.alt).length;
  const title = $('[data-missing-title]');
  const done = all.length - empty.length;
  $('[data-missing-count]').textContent = all.length ? `${done} of ${all.length} places have photos` : '';

  if (!empty.length && !partial.length && !noAlt) {
    title.textContent = 'Nothing missing';
    box.replaceChildren(h('p', { class: 'adm-muted' }, 'Every place on the site has a photo, and every photo has a description.'));
    return;
  }
  title.textContent = "What's missing";
  const link = (s: Slot, extra: string) =>
    h('li', {},
      h('a', { href: `#${slotAnchor(s.key)}`, onclick: (e: Event) => { e.preventDefault(); goTo(s.key); } }, s.label),
      h('span', { class: 'adm-missing__page' }, ` · ${pageName(s.page)} page${extra}`),
      s.hint ? h('span', { class: 'adm-missing__hint' }, s.hint) : null,
    );
  box.replaceChildren(h('div', {},
    empty.length ? h('ol', { class: 'adm-missing__list', id: 'adm-missing-list' }, empty.map((s, i) => {
      const li = link(s, s.multiple ? `, up to ${s.max}` : '');
      if (i >= SHOW && !showAll) li.hidden = true;
      return li;
    })) : null,
    empty.length > SHOW && !showAll ? h('button', {
      type: 'button', class: 'adm-link', 'aria-controls': 'adm-missing-list',
      onclick: () => {
        showAll = true;
        renderMissing();
        document.querySelectorAll<HTMLElement>('#adm-missing-list a')[SHOW]?.focus();
      },
    }, `Show all ${empty.length} empty places`) : null,
    partial.length ? h('div', {}, h('p', { class: 'adm-missing__sub' }, 'Could take more'),
      h('ul', { class: 'adm-missing__list is-soft' }, partial.map((s) => link(s, `, ${s.items.length} of ${s.max}`)))) : null,
    noAlt ? h('p', { class: 'adm-missing__alt' }, h('a', { href: '#library' }, `${noAlt} ${noAlt === 1 ? 'photo has' : 'photos have'} no description`), ' — descriptions are read aloud to people who can’t see the photos.') : null,
  ));
}

function goTo(key: string) {
  const art = articles.get(key);
  if (!art) return;
  art.scrollIntoView({ behavior: matchMedia('(prefers-reduced-motion: reduce)').matches ? 'auto' : 'smooth', block: 'start' });
  art.focus({ preventScroll: true });
  history.replaceState(null, '', `#${slotAnchor(key)}`);
}

function renderBar() {
  const bar = $('[data-unsaved]');
  const keys = dirtyKeys();
  bar.hidden = keys.length === 0;
  $('[data-unsaved-text]', bar).textContent =
    keys.length === 1 ? `Unsaved changes in ${state.slots.get(keys[0])?.label ?? 'one place'}` : `Unsaved changes in ${keys.length} places`;
  document.body.classList.toggle('adm-has-bar', keys.length > 0);
}

export async function saveAll() {
  const keys = dirtyKeys();
  let ok = 0;
  for (const k of keys) if (await save(k)) ok++;
  announce(ok === keys.length ? 'All changes saved.' : `${keys.length - ok} could not be saved. See the messages beside each one.`);
  if (ok < keys.length) goTo(dirtyKeys()[0]);
}

export function initSlots() {
  on('slots', () => {
    renderSlots();
    renderMissing();
  });
  on('media', () => {
    renderMissing();
  });
  on('dirty', () => {
    for (const k of state.slots.keys()) refreshFooter(k);
    renderBar();
  });
  on('saved', renderMissing);
  $('[data-save-all]').addEventListener('click', () => void saveAll());
  $('[data-picker-close]').addEventListener('click', () => ($('[data-picker]') as HTMLDialogElement).close());
  window.addEventListener('beforeunload', (e) => {
    if (dirtyKeys().length) {
      e.preventDefault();
      e.returnValue = '';
    }
  });
}
