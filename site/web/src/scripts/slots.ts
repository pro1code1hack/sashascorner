// Image slots, live. The HTML carries the photos as they were at build time
// (src/data/slots.json); this asks /api/slots for the current assignment and swaps
// any position that changed in the admin since -- no rebuild needed. Slots have a
// fixed aspect-ratio box, so a swap never moves the layout. If the API is down,
// nothing happens and the built page stands.
type Item = {
  src: string;
  srcset: string | string[];
  width: number;
  height: number;
  alt: string;
  focal: { x: number; y: number };
  blur: string | null;
};
type Def = { label: string; hint: string; items?: Item[] };

const sig = (it: Item | null) => (it ? `${it.src}|${it.focal.x},${it.focal.y}|${it.alt}` : '');
const srcset = (s: Item['srcset']) => (Array.isArray(s) ? s.join(', ') : s);
const pct = (n: number) => `${Math.round(n * 1000) / 10}%`;
const largest = (it: Item) => {
  let best = it.src;
  let bw = 0;
  for (const part of srcset(it.srcset).split(',')) {
    const [url, w] = part.trim().split(/\s+/);
    const n = parseInt(w ?? '', 10);
    if (url && n > bw) [best, bw] = [url, n];
  }
  return best;
};
const shortLabel = (label: string) => {
  const s = label.includes(' — ') ? label.split(' — ').slice(1).join(' — ') : label;
  return s.charAt(0).toUpperCase() + s.slice(1);
};

function markBroken(img: HTMLImageElement) {
  img.closest('.slot')?.classList.add('is-broken');
}

function fill(el: HTMLElement, it: Item) {
  const img = document.createElement('img');
  img.sizes = el.dataset.sizes || '100vw';
  img.srcset = srcset(it.srcset);
  img.src = it.src;
  img.width = it.width;
  img.height = it.height;
  img.alt = it.alt;
  img.decoding = 'async';
  img.loading = el.hasAttribute('data-eager') ? 'eager' : 'lazy';
  img.style.objectPosition = `${pct(it.focal.x)} ${pct(it.focal.y)}`;
  img.addEventListener('error', () => markBroken(img), { once: true });
  const pic = document.createElement('picture');
  pic.append(img);
  el.replaceChildren(pic);
  el.classList.remove('slot--empty', 'is-broken');
  el.classList.add('slot--filled');
  el.style.backgroundImage = it.blur ? `url("${it.blur}")` : '';
  el.dataset.sig = sig(it);
  const link = el.closest<HTMLAnchorElement>('a[data-slot-href]');
  if (link) link.href = largest(it);
}

function empty(el: HTMLElement, d: Def) {
  const span = (cls: string, text = '') => {
    const s = document.createElement('span');
    s.className = cls;
    s.textContent = text;
    return s;
  };
  const ph = document.createElement('div');
  ph.className = 'slot__ph';
  ph.setAttribute('aria-hidden', 'true');
  ph.title = d.hint;
  const text = span('slot__text');
  text.append(span('slot__label', shortLabel(d.label)), span('slot__hint', d.hint));
  ph.append(span('slot__line'), span('slot__mark', 'Photo to come'), text);
  el.replaceChildren(ph);
  el.classList.remove('slot--filled', 'is-broken');
  el.classList.add('slot--empty');
  el.style.backgroundImage = '';
  el.dataset.sig = '';
}

async function refresh() {
  const els = [...document.querySelectorAll<HTMLElement>('[data-slot]')];
  if (!els.length) return;
  // Built images that already failed (e.g. a media file removed since the build).
  for (const img of document.querySelectorAll<HTMLImageElement>('.slot img')) {
    if (img.complete && img.naturalWidth === 0) markBroken(img);
    else img.addEventListener('error', () => markBroken(img), { once: true });
  }
  let slots: Record<string, Def>;
  try {
    const r = await fetch('/api/slots', { headers: { Accept: 'application/json' } });
    if (!r.ok) return;
    slots = (await r.json())?.slots;
  } catch {
    return;
  }
  if (!slots || typeof slots !== 'object') return;
  for (const el of els) {
    const d = slots[el.dataset.slot!];
    if (!d) continue; // unknown to this API: leave what the build rendered
    const it = d.items?.[Number(el.dataset.index || 0)] ?? null;
    if (sig(it) === (el.dataset.sig ?? '')) continue;
    try {
      if (it) fill(el, it);
      else empty(el, d);
    } catch {
      /* a malformed item leaves the built slot alone */
    }
  }
  document.dispatchEvent(new CustomEvent('slots:updated'));
}

refresh();
