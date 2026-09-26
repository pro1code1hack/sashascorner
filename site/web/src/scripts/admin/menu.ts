// /admin/menu: what the website's menu page shows. Names, sizes and prices come
// from the back office (or the boards file) and are read-only here; this screen
// edits only the website's own presentation: order, category blurbs, item
// descriptions, the Signature star and what is hidden.
//
// Saving: everything autosaves. Switches and the star save at once; text saves a
// moment after you stop typing, and at once when you leave the field. Each row
// says Saving… / Saved / Not saved + Retry, the header sums it up, and the page
// warns before you leave with anything unsaved.
import { errorText } from '../admin-core/api';
import { ready } from '../admin-core/session';
import { announce, h, toast } from '../admin-core/ui';
import { gbp } from '../../lib/format';
import { menuApi, type Category, type Item, type MenuAdmin, type Size } from './menu-api';

const $ = <T extends HTMLElement = HTMLElement>(sel: string) => document.querySelector<T>(sel)!;

let data: MenuAdmin;
let editable = false;

// ---- saving -----------------------------------------------------------------------

type Status = 'idle' | 'editing' | 'saving' | 'saved' | 'failed';

interface Saver {
  status: Status;
  pending: Record<string, unknown> | null;
  inFlight: boolean;
  send: (body: Record<string, unknown>) => Promise<void>;
  view: HTMLElement | null;
  label: string;
  timer: number;
  onSaved?: (body: Record<string, unknown>) => void;
}

const savers = new Map<string, Saver>();

function saver(id: string, label: string, send: Saver['send'], onSaved?: Saver['onSaved']): Saver {
  let s = savers.get(id);
  if (!s) {
    s = { status: 'idle', pending: null, inFlight: false, send, view: null, label, timer: 0, onSaved };
    savers.set(id, s);
  }
  return s;
}

function paint(s: Saver) {
  const v = s.view;
  if (v) {
    v.dataset.state = s.status;
    if (s.status === 'failed') {
      v.replaceChildren(
        h('span', {}, 'Not saved'),
        h('button', { type: 'button', class: 'mn-retry', onclick: () => flush(s) }, 'Retry'),
      );
    } else {
      v.textContent = { idle: '', editing: 'Unsaved', saving: 'Saving…', saved: 'Saved' }[s.status];
    }
  }
  paintSummary();
}

function paintSummary() {
  const all = [...savers.values()];
  const failed = all.filter((s) => s.status === 'failed').length;
  const busy = all.some((s) => s.status === 'saving' || s.status === 'editing');
  const el = document.querySelector<HTMLElement>('[data-saved]');
  if (!el) return;
  el.dataset.state = failed ? 'failed' : busy ? 'saving' : 'ok';
  el.textContent = failed
    ? `${failed} change${failed === 1 ? '' : 's'} not saved`
    : busy
      ? 'Saving…'
      : editable
        ? 'All changes saved'
        : '';
}

/** Merge a change into what this saver will send next, and send it. */
function queue(s: Saver, patch: Record<string, unknown>) {
  window.clearTimeout(s.timer);
  s.pending = { ...(s.pending ?? {}), ...patch };
  void flush(s);
}

async function flush(s: Saver) {
  window.clearTimeout(s.timer);
  if (s.inFlight || !s.pending) return;
  const body = s.pending;
  s.pending = null;
  s.inFlight = true;
  s.status = 'saving';
  paint(s);
  try {
    await s.send(body);
    s.inFlight = false;
    s.onSaved?.(body);
    if (s.pending) return void flush(s); // more changes arrived meanwhile
    s.status = 'saved';
    paint(s);
    window.setTimeout(() => {
      if (s.status === 'saved') {
        s.status = 'idle';
        paint(s);
      }
    }, 2500);
  } catch (e) {
    s.inFlight = false;
    // Keep the failed change (under anything newer) so Retry sends it again.
    s.pending = { ...body, ...(s.pending ?? {}) };
    s.status = 'failed';
    paint(s);
    toast(`${s.label}: ${errorText(e)}`, { tone: 'bad', action: { label: 'Retry', run: () => void flush(s) } });
  }
}

window.addEventListener('beforeunload', (e) => {
  const open = [...savers.values()].some((s) => s.status !== 'idle' && s.status !== 'saved');
  if (open) {
    e.preventDefault();
    e.returnValue = '';
  }
});

/** A text field that autosaves: a moment after typing stops, and at once on leaving. */
function autosaveText(field: HTMLInputElement | HTMLTextAreaElement, s: Saver, key: string, saved: () => string, toBody: (v: string) => unknown) {
  const commit = () => {
    window.clearTimeout(s.timer);
    const v = field.value.trim();
    if (v === saved()) {
      if (s.status === 'editing') {
        s.status = s.pending ? 'failed' : 'idle';
        paint(s);
      }
      return;
    }
    queue(s, { [key]: toBody(v) });
  };
  field.addEventListener('input', () => {
    if (s.status !== 'saving' && s.status !== 'failed') {
      s.status = 'editing';
      paint(s);
    }
    window.clearTimeout(s.timer);
    s.timer = window.setTimeout(commit, 1000);
  });
  field.addEventListener('blur', commit);
}

// ---- order ----------------------------------------------------------------------

const orderSaver = () =>
  saver('order', 'Order', async (body) => {
    await menuApi.order(body as { categories: string[]; items: Record<string, string[]> });
  });

let orderTimer = 0;
function saveOrder() {
  const s = orderSaver();
  s.status = 'editing';
  paint(s);
  window.clearTimeout(orderTimer);
  // Several quick moves go as one request. The whole order is always sent.
  orderTimer = window.setTimeout(() => {
    queue(s, {
      categories: data.categories.map((c) => c.slug),
      items: Object.fromEntries(data.categories.map((c) => [c.slug, c.items.map((i) => i.key)])),
    });
  }, 700);
}

function move<T>(arr: T[], from: number, to: number) {
  const [x] = arr.splice(from, 1);
  arr.splice(to, 0, x);
}

/**
 * Drag (pointer) and keyboard (arrow keys on the grip) reordering for one list.
 * `rows()` gives the list's rows in DOM order; `onMove` gets old and new index.
 */
function sortable(list: HTMLElement, gripSel: string, rowOf: (grip: HTMLElement) => HTMLElement | null, rows: () => HTMLElement[], onMove: (from: number, to: number, row: HTMLElement) => void) {
  list.addEventListener('keydown', (e) => {
    const grip = (e.target as HTMLElement).closest<HTMLElement>(gripSel);
    if (!grip || (e.key !== 'ArrowUp' && e.key !== 'ArrowDown')) return;
    const row = rowOf(grip);
    if (!row) return;
    const all = rows();
    const from = all.indexOf(row);
    const to = from + (e.key === 'ArrowUp' ? -1 : 1);
    if (from < 0 || to < 0 || to >= all.length) return;
    e.preventDefault();
    if (to < from) all[to].before(row);
    else all[to].after(row);
    grip.focus();
    onMove(from, to, row);
  });

  list.addEventListener('pointerdown', (e) => {
    const grip = (e.target as HTMLElement).closest<HTMLElement>(gripSel);
    if (!grip || e.button !== 0) return;
    const row = rowOf(grip);
    if (!row) return;
    e.preventDefault();
    grip.setPointerCapture(e.pointerId);
    const from = rows().indexOf(row);
    row.classList.add('is-dragging');
    list.classList.add('is-sorting');
    const onMoveP = (ev: PointerEvent) => {
      const all = rows();
      const i = all.indexOf(row);
      const prev = all[i - 1];
      const next = all[i + 1];
      if (prev) {
        const r = prev.getBoundingClientRect();
        if (ev.clientY < r.top + r.height / 2) return void prev.before(row);
      }
      if (next) {
        const r = next.getBoundingClientRect();
        if (ev.clientY > r.top + r.height / 2) return void next.after(row);
      }
    };
    const end = () => {
      grip.removeEventListener('pointermove', onMoveP);
      grip.removeEventListener('pointerup', end);
      grip.removeEventListener('pointercancel', end);
      row.classList.remove('is-dragging');
      list.classList.remove('is-sorting');
      const to = rows().indexOf(row);
      if (to !== from && to >= 0) onMove(from, to, row);
    };
    grip.addEventListener('pointermove', onMoveP);
    grip.addEventListener('pointerup', end);
    grip.addEventListener('pointercancel', end);
  });
}

// ---- rendering ---------------------------------------------------------------------

const GRIP = '<svg viewBox="0 0 16 16" width="16" height="16" aria-hidden="true"><g fill="currentColor"><circle cx="5.5" cy="3.5" r="1.3"/><circle cx="10.5" cy="3.5" r="1.3"/><circle cx="5.5" cy="8" r="1.3"/><circle cx="10.5" cy="8" r="1.3"/><circle cx="5.5" cy="12.5" r="1.3"/><circle cx="10.5" cy="12.5" r="1.3"/></g></svg>';

function grip(kind: 'cat' | 'item', name: string) {
  const b = h('button', {
    type: 'button',
    class: 'mn-grip',
    'data-grip': kind,
    'aria-label': `Move ${name}`,
    'aria-describedby': 'mn-move-help',
    title: 'Drag, or press the up and down arrow keys',
  });
  b.innerHTML = GRIP;
  return b;
}

function sizeText(z: Size): string {
  const price = z.price_pence === null ? 'no price' : gbp(z.price_pence);
  return z.code === 'One' ? price : `${z.code} ${price}`;
}

function prices(sizes: Size[]) {
  if (!sizes.length) return h('span', { class: 'mn-prices is-none' }, 'No sizes in the back office');
  return h(
    'span',
    { class: 'mn-prices adm-fig' },
    sizes.flatMap((z, i) => [i ? h('span', { class: 'mn-dot', 'aria-hidden': 'true' }, ' · ') : null, h('span', { class: 'mn-size' }, sizeText(z))]),
  );
}

function toggleSwitch(label: string, on: boolean, onChange: (on: boolean) => void) {
  const b = h(
    'button',
    { type: 'button', class: 'mn-switch', role: 'switch', 'aria-checked': String(on), disabled: !editable },
    h('span', { class: 'mn-switch__track', 'aria-hidden': 'true' }, h('span', { class: 'mn-switch__knob' })),
    h('span', { class: 'mn-switch__label' }, label),
  );
  b.addEventListener('click', () => {
    const next = b.getAttribute('aria-checked') !== 'true';
    b.setAttribute('aria-checked', String(next));
    onChange(next);
  });
  return b;
}

let uid = 0;

function itemRow(c: Category, it: Item): HTMLElement {
  const id = `mn-i-${++uid}`;
  const status = h('span', { class: 'mn-status', 'aria-live': 'polite' });
  const s = saver(`item:${it.key}`, it.name, (body) => menuApi.putItem(it.key, body), (body) => {
    if ('description' in body) it.web.description = (body.description as string | null) ?? null;
    if ('signature' in body) it.web.signature = body.signature as boolean;
    if ('hidden' in body) it.web.hidden = body.hidden as boolean;
    if ('use_ops_note' in body) it.web.use_ops_note = body.use_ops_note as boolean;
  });
  s.view = status;

  const li = h('li', { class: 'mn-item', 'data-key': it.key, 'data-name': it.name.toLowerCase() });
  li.classList.toggle('is-hidden', it.web.hidden);

  // Signature star
  const star = h(
    'button',
    { type: 'button', class: 'mn-star', 'aria-pressed': String(it.web.signature), disabled: !editable, title: 'Signature: marked with a star on the website' },
    h('span', { class: 'mn-star__glyph', 'aria-hidden': 'true' }, it.web.signature ? '★' : '☆'),
    h('span', { class: 'adm-sr' }, `Signature: ${it.name}`),
  );
  star.addEventListener('click', () => {
    const on = star.getAttribute('aria-pressed') !== 'true';
    star.setAttribute('aria-pressed', String(on));
    star.querySelector('.mn-star__glyph')!.textContent = on ? '★' : '☆';
    li.classList.toggle('is-sig', on);
    queue(s, { signature: on });
    refreshCounts();
  });
  li.classList.toggle('is-sig', it.web.signature);

  const shown = toggleSwitch('On website', !it.web.hidden, (on) => {
    li.classList.toggle('is-hidden', !on);
    queue(s, { hidden: !on });
    refreshCounts();
  });
  shown.setAttribute('aria-label', `Show ${it.name} on the website`);

  // Description, or the back-office note in its place.
  const desc = h('textarea', {
    class: 'adm-input mn-desc',
    id,
    rows: 1,
    placeholder: 'No description on the website',
    disabled: !editable,
    'aria-label': `Website description for ${it.name}`,
  }) as HTMLTextAreaElement;
  desc.value = it.web.description ?? '';
  const grow = () => {
    desc.style.height = 'auto';
    desc.style.height = `${desc.scrollHeight + 2}px`;
  };
  desc.addEventListener('input', grow);
  autosaveText(desc, s, 'description', () => it.web.description ?? '', (v) => v || null);

  const descBox = h('div', { class: 'mn-descbox' });
  if (it.note) {
    // The website shows either the note or the description; the description is
    // kept either way, so switching back loses nothing.
    const noteText = h('p', { class: 'mn-note' }, it.note);
    const cb = h('input', { type: 'checkbox', checked: it.web.use_ops_note, disabled: !editable }) as HTMLInputElement;
    const sync = (on: boolean) => {
      desc.hidden = on;
      noteText.hidden = !on;
      if (!on) requestAnimationFrame(grow);
    };
    sync(it.web.use_ops_note);
    cb.addEventListener('change', () => {
      sync(cb.checked);
      queue(s, { use_ops_note: cb.checked });
      refreshCounts();
      if (!cb.checked) desc.focus();
    });
    descBox.append(h('label', { class: 'mn-usenote' }, cb, h('span', {}, 'Use the back-office note as the description')), noteText, desc);
  } else {
    descBox.append(desc);
  }
  requestAnimationFrame(grow);

  li.append(
    editable ? grip('item', it.name) : h('span', { class: 'mn-grip-space', 'aria-hidden': 'true' }),
    h(
      'div',
      { class: 'mn-item__main' },
      h(
        'p',
        { class: 'mn-item__name' },
        h('span', {}, it.name),
        it.seasonal ? h('span', { class: 'adm-pill adm-pill--warn' }, it.seasonal) : null,
        it.seasonal && !it.in_season ? h('span', { class: 'adm-pill', title: 'The website shows it when its season opens' }, 'Out of season') : null,
        h('span', { class: 'adm-pill mn-hiddenpill' }, 'Hidden'),
      ),
      prices(it.sizes),
    ),
    h('div', { class: 'mn-item__ctl' }, star, shown, status),
    descBox,
  );
  return li;
}

function catBlock(c: Category): HTMLElement {
  const status = h('span', { class: 'mn-status', 'aria-live': 'polite' });
  const s = saver(`cat:${c.slug}`, c.name, (body) => menuApi.putCategory(c.slug, body), (body) => {
    if ('blurb' in body) c.blurb = String(body.blurb ?? '');
    if ('hidden' in body) c.hidden = body.hidden as boolean;
  });
  s.view = status;
  const headId = `mn-c-${c.slug}`;
  const sec = h('li', { class: 'mn-cat', 'data-slug': c.slug });
  sec.classList.toggle('is-hidden', c.hidden);

  const shown = toggleSwitch('On website', !c.hidden, (on) => {
    sec.classList.toggle('is-hidden', !on);
    queue(s, { hidden: !on });
  });
  shown.setAttribute('aria-label', `Show the ${c.name} section on the website`);

  const blurb = h('textarea', { class: 'adm-input mn-blurb', rows: 1, disabled: !editable, placeholder: 'No line under the heading', 'aria-label': `Line under the ${c.name} heading` }) as HTMLTextAreaElement;
  blurb.value = c.blurb;
  autosaveText(blurb, s, 'blurb', () => c.blurb.trim(), (v) => v || null);

  const list = h('ul', { class: 'mn-items', 'aria-label': `${c.name} items` });
  for (const it of c.items) list.append(itemRow(c, it));
  if (!c.items.length) list.append(h('li', { class: 'mn-empty' }, 'No items in this category.'));

  if (editable) {
    const rows = () => [...list.children].filter((x) => x.classList.contains('mn-item')) as HTMLElement[];
    sortable(list, '[data-grip="item"]', (g) => g.closest('.mn-item'), rows, (from, to, row) => {
      move(c.items, from, to);
      announce(`${row.querySelector('.mn-item__name span')?.textContent} moved to ${to + 1} of ${c.items.length}.`);
      saveOrder();
    });
  }

  sec.append(
    h(
      'div',
      { class: 'mn-cat__head' },
      editable ? grip('cat', c.name) : null,
      h(
        'div',
        { class: 'mn-cat__title' },
        h('h2', { id: headId }, c.name),
        h(
          'p',
          { class: 'mn-cat__meta' },
          c.kind ? h('span', {}, c.kind === 'FOOD' ? 'Food' : 'Drinks') : null,
          h('span', { class: 'adm-fig', 'data-count': true }, `${c.items.length} item${c.items.length === 1 ? '' : 's'}`),
          h('span', { class: 'adm-pill mn-hiddenpill' }, 'Hidden from website'),
        ),
      ),
      h('div', { class: 'mn-cat__ctl' }, shown, status),
    ),
    h('label', { class: 'mn-blurbrow' }, h('span', { class: 'adm-label' }, 'Line under the heading'), blurb),
    list,
  );
  sec.setAttribute('aria-labelledby', headId);
  return sec;
}

function banner(m: MenuAdmin) {
  const host = $('[data-banner]');
  const warn = m.warnings.length ? h('ul', { class: 'mn-warnings' }, m.warnings.map((w) => h('li', {}, w))) : null;
  if (m.source === 'ops') {
    host.replaceChildren(
      h('div', { class: 'adm-banner mn-banner', role: 'status' }, h('span', { class: 'adm-dot', 'aria-hidden': 'true' }), h('p', {}, h('strong', {}, 'Menu comes from the back office. '), 'Names, sizes and prices are the back office’s. Here you choose how the website shows them.')),
      warn ?? '',
    );
  } else {
    host.replaceChildren(
      h(
        'div',
        { class: 'adm-banner mn-banner', role: 'status' },
        h('span', { class: 'adm-dot', 'aria-hidden': 'true' }),
        h(
          'p',
          {},
          h('strong', {}, 'Showing the menu boards file until the back-office menu is ready. '),
          'Names, sizes and prices come from the TV boards for now; you can still change how the website shows them — descriptions, stars, hiding and order. Once the back office has its menu categories, the website switches over and prices will come from there.',
        ),
      ),
      warn ?? '',
    );
  }
}

function diffPanel(m: MenuAdmin) {
  const d = m.drift;
  const host = $('[data-diff]');
  const total = d.price_mismatches.length + d.board_only.length + d.ops_only.length;
  const out: (HTMLElement | null)[] = [
    h('h2', { class: 'adm-section-head', id: 'mn-diff-t' }, total ? `Differences · ${total}` : 'Differences'),
    h('p', { class: 'adm-hint' }, `Where the TV boards and the back office don’t agree. The website shows the ${m.source === 'ops' ? 'back office' : 'boards'} version. Fix it in whichever one is wrong.`),
  ];
  if (d.error) out.push(h('p', { class: 'adm-warnbox' }, `The comparison couldn’t run: ${d.error}`));
  else if (!total) out.push(h('p', { class: 'mn-none' }, 'None. The boards and the back office agree.'));
  const pence = (p: number | null) => (p === null ? 'not listed' : gbp(p));
  // Each kind of difference folds away; long lists start folded.
  const group = (title: string, n: number, list: HTMLElement) =>
    h('details', { class: 'mn-diff', open: n <= 12 }, h('summary', { class: 'mn-diff__h' }, `${title} `, h('span', { class: 'adm-fig mn-diff__n' }, String(n))), list);
  if (d.price_mismatches.length) {
    out.push(
      group(
        'Different prices',
        d.price_mismatches.length,
        h(
          'ul',
          { class: 'mn-diff__list' },
          d.price_mismatches.map((x) =>
            h('li', {}, h('span', { class: 'mn-diff__name' }, `${x.name}${x.size && x.size !== 'One' ? ` ${x.size}` : ''}: `), h('span', { class: 'adm-fig' }, `boards ${pence(x.board)}, back office ${pence(x.ops)}`)),
          ),
        ),
      ),
    );
  }
  if (d.board_only.length) {
    out.push(group('On the boards, not in the back office', d.board_only.length, h('ul', { class: 'mn-diff__list mn-diff__names' }, d.board_only.map((n) => h('li', {}, n)))));
  }
  if (d.ops_only.length) {
    out.push(group('In the back office, not on the boards', d.ops_only.length, h('ul', { class: 'mn-diff__list mn-diff__names' }, d.ops_only.map((n) => h('li', {}, n)))));
  }
  host.replaceChildren(...out.filter((x): x is HTMLElement => !!x));
}

function unassignedPanel(m: MenuAdmin) {
  const host = $('[data-unassigned]');
  host.hidden = !m.unassigned.length;
  if (!m.unassigned.length) return;
  host.replaceChildren(
    h('h2', { class: 'adm-section-head', id: 'mn-un-t' }, `Unassigned in back office · ${m.unassigned.length}`),
    h('p', { class: 'adm-hint' }, 'These items have no category in the back office, so the website can’t show them. Give each one a category in the back office; it then appears here in that category.'),
    h(
      'ul',
      { class: 'mn-unlist' },
      m.unassigned.map((it) => h('li', {}, h('span', { class: 'mn-item__name' }, it.name), prices(it.sizes))),
    ),
  );
}

function summary(m: MenuAdmin) {
  const host = $('[data-summary]');
  const n = m.drift.price_mismatches.length + m.drift.board_only.length + m.drift.ops_only.length;
  const bits: HTMLElement[] = [];
  if (m.unassigned.length) bits.push(h('a', { href: '#mn-unassigned' }, `${m.unassigned.length} item${m.unassigned.length === 1 ? '' : 's'} need a category in the back office`));
  if (n) bits.push(h('a', { href: '#mn-diff' }, `${n} difference${n === 1 ? '' : 's'} between the boards and the back office`));
  host.hidden = !bits.length;
  host.replaceChildren(...bits.flatMap((b, i) => (i ? [h('span', { 'aria-hidden': 'true' }, ' · '), b] : [b])));
}

// ---- filter -------------------------------------------------------------------------

let filter: 'all' | 'sig' | 'hidden' | 'nodesc' = 'all';

function itemFor(li: HTMLElement): Item | undefined {
  const key = li.dataset.key;
  for (const c of data.categories) for (const i of c.items) if (i.key === key) return i;
  return undefined;
}

function matches(it: Item, li: HTMLElement): boolean {
  if (filter === 'sig') return li.classList.contains('is-sig');
  if (filter === 'hidden') return li.classList.contains('is-hidden');
  if (filter === 'nodesc') return !(it.web.use_ops_note && it.note) && !it.web.description;
  return true;
}

function applyFilter() {
  const q = ($<HTMLInputElement>('[data-search]').value ?? '').trim().toLowerCase();
  const filtering = !!q || filter !== 'all';
  const root = $('[data-cats]');
  root.classList.toggle('is-filtering', filtering);
  let shown = 0;
  root.querySelectorAll<HTMLElement>('.mn-cat').forEach((sec) => {
    const catHit = !!q && sec.querySelector('h2')!.textContent!.toLowerCase().includes(q);
    let any = 0;
    sec.querySelectorAll<HTMLElement>('.mn-item').forEach((li) => {
      const it = itemFor(li);
      const ok = !!it && matches(it, li) && (!q || catHit || li.dataset.name!.includes(q));
      li.hidden = !ok;
      if (ok) any++;
    });
    const vis = !filtering || any > 0 || (catHit && filter === 'all');
    sec.hidden = !vis;
    shown += any;
  });
  const note = $('[data-filter-note]');
  note.hidden = !filtering;
  note.textContent = filtering ? `${shown} item${shown === 1 ? '' : 's'} match. Clear the search and filters to reorder.` : '';
}

function refreshCounts() {
  const root = $('[data-cats]');
  const count = (sel: string) => root.querySelectorAll(`.mn-item${sel}`).length;
  const set = (k: string, n: number) => {
    const el = document.querySelector(`[data-filter="${k}"] .adm-chip-count`);
    if (el) el.textContent = String(n);
  };
  set('all', count(''));
  set('sig', count('.is-sig'));
  set('hidden', count('.is-hidden'));
  set('nodesc', data.categories.flatMap((c) => c.items).filter((i) => !(i.web.use_ops_note && i.note) && !i.web.description).length);
  if (filter !== 'all') applyFilter();
}

// ---- boot ---------------------------------------------------------------------------

function render(m: MenuAdmin) {
  data = m;
  // Presentation (descriptions, stars, hiding, order) is the website's own in both
  // modes; only names, sizes and prices belong to the source and stay read-only.
  editable = true;
  document.querySelector('[data-mn]')!.classList.toggle('is-readonly', !editable);
  banner(m);
  summary(m);
  const root = $('[data-cats]');
  root.replaceChildren(...m.categories.map(catBlock));
  if (!m.categories.length) root.append(h('li', { class: 'mn-empty' }, 'No categories yet.'));
  if (editable) {
    const rows = () => [...root.children].filter((x) => x.classList.contains('mn-cat')) as HTMLElement[];
    sortable(root, '[data-grip="cat"]', (g) => g.closest('.mn-cat'), rows, (from, to, row) => {
      move(data.categories, from, to);
      announce(`${row.querySelector('h2')?.textContent} moved to ${to + 1} of ${data.categories.length}.`);
      saveOrder();
    });
  }
  $('[data-move-help]').hidden = !editable;
  unassignedPanel(m);
  diffPanel(m);
  refreshCounts();
  paintSummary();
}

function wireToolbar() {
  $('[data-search]').addEventListener('input', applyFilter);
  document.querySelectorAll<HTMLButtonElement>('[data-filter]').forEach((b) =>
    b.addEventListener('click', () => {
      filter = b.dataset.filter as typeof filter;
      document.querySelectorAll('[data-filter]').forEach((x) => x.setAttribute('aria-pressed', String(x === b)));
      applyFilter();
    }),
  );
  document.querySelectorAll<HTMLButtonElement>('[data-view]').forEach((b) =>
    b.addEventListener('click', () => {
      document.querySelectorAll('[data-view]').forEach((x) => x.setAttribute('aria-pressed', String(x === b)));
      $('[data-cats]').classList.toggle('is-compact', b.dataset.view === 'cats');
    }),
  );
}

async function boot() {
  await ready();
  wireToolbar();
  const loading = $('[data-loading]');
  try {
    const m = await menuApi.load();
    loading.hidden = true;
    $('[data-body]').hidden = false;
    render(m);
  } catch (e) {
    loading.replaceChildren(
      h('p', {}, `The menu didn’t load. ${errorText(e)}`),
      h('button', { type: 'button', class: 'adm-btn adm-btn--secondary', onclick: () => location.reload() }, 'Try again'),
    );
  }
}

void boot();
