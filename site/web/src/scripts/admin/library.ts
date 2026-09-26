// The photo library: uploads (drag-and-drop or picker, with progress), the
// thumbnail grid with inline descriptions, and delete with an in-page confirm.
import { $, announce, h, kb, pageName, slotAnchor } from './dom';
import { emit, on, putMedia, setMedia, setSlots, state } from './state';
import { ApiError, type Media } from './types';

export const MAX_BYTES = 15 * 1024 * 1024;
export const TYPES = ['image/jpeg', 'image/png', 'image/webp'];

export function thumb(m: { src: string; srcset: string; alt: string; blur?: string }, sizes: string, extra: Record<string, string> = {}) {
  return h('img', {
    src: m.src,
    srcset: m.srcset || null,
    sizes: m.srcset ? sizes : null,
    alt: '',
    loading: 'lazy',
    decoding: 'async',
    draggable: 'false',
    style: m.blur ? `background-image:url("${m.blur}");background-size:cover` : null,
    ...extra,
  });
}

function checkFile(f: File): string | null {
  if (!TYPES.includes(f.type)) return 'Not a photo we can use. Please choose a JPEG, PNG or WebP image.';
  if (f.size > MAX_BYTES) return `Too large (${kb(f.size)}). The limit is 15 MB. Try exporting it smaller from your phone or computer.`;
  return null;
}

/**
 * Upload files one by one into `list`, a <ul> that shows a progress row per
 * file. Resolves with the photos that made it into the library.
 */
export async function uploadFiles(files: File[], list: HTMLElement): Promise<Media[]> {
  const done: Media[] = [];
  const failed: string[] = [];
  let dups = 0;
  const rows = files.map((f) => {
    const bar = h('progress', { max: '100', value: '0', 'aria-label': `Uploading ${f.name}` });
    const msg = h('span', { class: 'ph-up__msg' }, 'Waiting…');
    const row = h('li', { class: 'ph-up' }, h('span', { class: 'ph-up__name' }, f.name), bar, msg);
    list.append(row);
    return { f, bar, msg, row };
  });
  list.hidden = false;
  if (files.length) announce(`Uploading ${files.length} ${files.length === 1 ? 'photo' : 'photos'}.`);

  for (const r of rows) {
    const bad = checkFile(r.f);
    if (bad) {
      r.row.classList.add('is-bad');
      r.bar.remove();
      r.msg.textContent = bad;
      failed.push(r.f.name);
      continue;
    }
    r.msg.textContent = 'Uploading…';
    try {
      const res = await state.api.upload(r.f, '', (p) => {
        r.bar.value = Math.round(p * 100);
        r.msg.textContent = p >= 1 ? 'Processing…' : `${Math.round(p * 100)}%`;
      });
      r.bar.value = 100;
      r.row.classList.add('is-ok');
      r.msg.textContent = res.duplicate ? 'Already in your library' : 'Added';
      if (res.duplicate) dups++;
      putMedia(res.media);
      done.push(res.media);
    } catch (e) {
      r.row.classList.add('is-bad');
      r.bar.remove();
      const err = e as ApiError;
      r.msg.textContent =
        err.status === 415 ? 'Not a photo we can use. Please choose a JPEG, PNG or WebP image.'
        : err.status === 413 ? 'Too large. The limit is 15 MB.'
        : err.status === 401 ? 'Signed out. Please sign in again.'
        : `Couldn't upload: ${err.message}`;
      failed.push(r.f.name);
    }
  }

  const parts = [];
  const added = done.length - dups;
  if (added) parts.push(`${added} ${added === 1 ? 'photo' : 'photos'} added to the library.`);
  if (dups) parts.push(`${dups} ${dups === 1 ? 'was' : 'were'} already in the library.`);
  if (failed.length) parts.push(`${failed.length} couldn't be added: ${failed.join(', ')}.`);
  announce(parts.join(' '));
  // Clear the successful rows after a while; keep failures until the next batch.
  setTimeout(() => {
    rows.forEach((r) => r.row.classList.contains('is-ok') && r.row.remove());
    if (!list.children.length) list.hidden = true;
  }, 6000);
  return done;
}

// ---- drop zone ---------------------------------------------------------------

export function initDropzone() {
  const zone = $('[data-drop]');
  const input = $('[data-drop-input]') as HTMLInputElement;
  const list = $('[data-uploads]');
  $('[data-drop-pick]').addEventListener('click', () => input.click());

  input.addEventListener('change', () => {
    const files = [...(input.files ?? [])];
    input.value = '';
    if (files.length) void uploadFiles(files, list);
  });

  let depth = 0;
  zone.addEventListener('dragenter', (e) => {
    e.preventDefault();
    depth++;
    zone.classList.add('is-over');
  });
  zone.addEventListener('dragover', (e) => e.preventDefault());
  zone.addEventListener('dragleave', () => {
    if (--depth <= 0) zone.classList.remove('is-over'), (depth = 0);
  });
  zone.addEventListener('drop', (e) => {
    e.preventDefault();
    depth = 0;
    zone.classList.remove('is-over');
    const files = [...(e.dataTransfer?.files ?? [])];
    if (files.length) void uploadFiles(files, list);
  });
  // A file dropped anywhere else would navigate away and lose unsaved work.
  window.addEventListener('dragover', (e) => e.preventDefault());
  window.addEventListener('drop', (e) => e.preventDefault());
}

// ---- grid ----------------------------------------------------------------------

const cards = new Map<number, HTMLElement>();
const timers = new Map<number, number>();

function usedIn(m: Media) {
  if (!m.usage.length) return h('p', { class: 'ph-card__used is-free' }, 'Not used on the site yet');
  return h(
    'p',
    { class: 'ph-card__used' },
    'Used in ',
    m.usage.map((k, i) => {
      const s = state.slots.get(k);
      return [i ? ', ' : '', h('a', { href: `#${slotAnchor(k)}` }, s ? s.label : k)];
    }),
  );
}

async function saveAlt(m: Media, input: HTMLInputElement, status: HTMLElement) {
  const alt = input.value.trim();
  if (alt === m.alt) return;
  status.textContent = 'Saving…';
  status.dataset.state = 'busy';
  try {
    const next = await state.api.patchAlt(m.id, alt);
    m.alt = next?.alt ?? alt;
    if (next) m.usage = next.usage.length ? next.usage : m.usage;
    status.textContent = 'Saved';
    status.dataset.state = 'ok';
    input.closest('.ph-card')?.classList.toggle('needs-alt', !m.alt);
    emit('media');
  } catch (e) {
    status.textContent = `Couldn't save: ${(e as Error).message}`;
    status.dataset.state = 'bad';
    announce(`Couldn't save the description for ${m.original_name || 'this photo'}.`);
  }
}

function card(m: Media): HTMLElement {
  const id = `ph-alt-${m.id}`;
  const status = h('span', { class: 'ph-card__status', role: 'status' });
  const input = h('input', {
    id,
    class: 'adm-input ph-card__alt',
    type: 'text',
    value: m.alt,
    maxlength: '300',
    placeholder: 'e.g. Blue matcha latte on the counter',
    autocomplete: 'off',
  });
  const flush = () => {
    clearTimeout(timers.get(m.id));
    void saveAlt(m, input, status);
  };
  input.addEventListener('input', () => {
    status.textContent = '';
    clearTimeout(timers.get(m.id));
    timers.set(m.id, window.setTimeout(flush, 900));
  });
  input.addEventListener('blur', flush);
  input.addEventListener('keydown', (e) => e.key === 'Enter' && (e.preventDefault(), flush()));

  const meta = [m.original_name, m.width && m.height ? `${m.width} × ${m.height}` : '', kb(m.bytes)].filter(Boolean).join(' · ');
  const el = h(
    'li',
    { class: `ph-card${m.alt ? '' : ' needs-alt'}`, 'data-media': String(m.id) },
    h('div', { class: 'ph-card__img' }, thumb(m, '(max-width: 600px) 45vw, 220px')),
    h(
      'div',
      { class: 'ph-card__body' },
      h('label', { for: id, class: 'ph-card__label' }, 'Description', h('span', { class: 'ph-card__need' }, ' — needed')),
      input,
      status,
      h('div', { 'data-used': '' }, usedIn(m)),
      h('p', { class: 'ph-card__meta' }, meta),
      h('button', { type: 'button', class: 'ph-link ph-link--danger', onclick: () => confirmDelete(m.id) }, 'Delete photo'),
    ),
  );
  return el;
}

export function renderLibrary() {
  const grid = $('[data-library]');
  const count = $('[data-library-count]');
  const empty = $('[data-library-empty]');
  const n = state.mediaOrder.length;
  count.textContent = n ? `${n} ${n === 1 ? 'photo' : 'photos'}` : '';
  empty.hidden = n > 0;

  for (const [id, el] of cards) {
    if (!state.media.has(id)) {
      el.remove();
      cards.delete(id);
    }
  }
  let prev: Element | null = null;
  for (const id of state.mediaOrder) {
    const m = state.media.get(id)!;
    let el = cards.get(id);
    if (!el) {
      el = card(m);
      cards.set(id, el);
    } else {
      el.querySelector('[data-used]')!.replaceChildren(usedIn(m));
      el.classList.toggle('needs-alt', !m.alt);
      const input = el.querySelector<HTMLInputElement>('.ph-card__alt')!;
      if (document.activeElement !== input && !timers.get(id)) input.value = m.alt;
    }
    const want: Element | null = prev ? prev.nextElementSibling : grid.firstElementChild;
    if (want !== el) grid.insertBefore(el, want);
    prev = el;
  }
}

export async function reloadMedia() {
  setMedia(await state.api.listMedia());
}

// ---- delete, with an in-page confirmation ------------------------------------------

export function confirmDelete(id: number) {
  const m = state.media.get(id);
  if (!m) return;
  const dlg = $('[data-delete-dialog]') as HTMLDialogElement;
  const body = $('[data-delete-body]', dlg);
  const actions = $('[data-delete-actions]', dlg);
  const opener = document.activeElement as HTMLElement | null;
  const close = () => {
    dlg.close();
    const next = cards.get(id) ?? $('[data-library]');
    (document.contains(opener) ? opener : next)?.focus?.();
  };

  const run = async (force: boolean) => {
    actions.querySelectorAll('button').forEach((b) => ((b as HTMLButtonElement).disabled = true));
    $('[data-delete-msg]', dlg).textContent = 'Deleting…';
    try {
      await state.api.deleteMedia(id, force);
      state.media.delete(id);
      state.mediaOrder = state.mediaOrder.filter((x) => x !== id);
      // Drop it from any unsaved drafts too, so a later save can't re-add it.
      for (const [k, d] of state.drafts) state.drafts.set(k, d.filter((i) => i.media_id !== id));
      dlg.close();
      emit('media');
      if (force) setSlots(await state.api.slots());
      announce(`Photo deleted${force ? ' and removed from the site' : ''}.`);
      $('[data-library]').focus();
    } catch (e) {
      const err = e as ApiError;
      if (err.status === 409) return inUse(err);
      $('[data-delete-msg]', dlg).textContent = `Couldn't delete: ${err.message}`;
      actions.querySelectorAll('button').forEach((b) => ((b as HTMLButtonElement).disabled = false));
    }
  };

  const inUse = (err: ApiError) => {
    const fromServer = JSON.stringify(err.body ?? '').match(/[a-z0-9_]+(\.[a-z0-9_]+)+/g) ?? [];
    const keys = [...new Set([...m.usage, ...fromServer.filter((k) => state.slots.has(k))])];
    $('[data-delete-title]', dlg).textContent = 'This photo is on the website';
    body.replaceChildren(
      h('p', {}, 'It is shown in these places. Deleting it will leave them empty until you choose another photo:'),
      h('ul', { class: 'ph-dlg__list' }, keys.map((k) => {
        const s = state.slots.get(k);
        return h('li', {}, s ? `${s.label} (${pageName(s.page)} page)` : k);
      })),
    );
    $('[data-delete-msg]', dlg).textContent = '';
    actions.replaceChildren(
      h('button', { type: 'button', class: 'adm-btn adm-btn--danger', onclick: () => run(true) }, 'Remove from those places and delete'),
      h('button', { type: 'button', class: 'adm-btn adm-btn--secondary', onclick: close }, 'Keep it'),
    );
    (actions.firstElementChild as HTMLElement).focus();
  };

  $('[data-delete-title]', dlg).textContent = 'Delete this photo?';
  body.replaceChildren(
    h('div', { class: 'ph-dlg__thumb' }, thumb(m, '120px')),
    h('p', {}, `“${m.alt || m.original_name || 'Untitled photo'}” will be removed from your library. This can't be undone, but you can always upload it again.`),
  );
  $('[data-delete-msg]', dlg).textContent = '';
  actions.replaceChildren(
    h('button', { type: 'button', class: 'adm-btn adm-btn--danger', onclick: () => run(false) }, 'Delete'),
    h('button', { type: 'button', class: 'adm-btn adm-btn--secondary', onclick: close }, 'Keep it'),
  );
  dlg.showModal();
  (actions.lastElementChild as HTMLElement).focus();
}

export function initLibrary() {
  initDropzone();
  on('media', renderLibrary);
  on('slots', renderLibrary);
}
