"""The JavaScript the executor injects to read a page and hand out element refs.

docs/agents/BROWSER-ORDERING.md §3. The model reads a page as a text tree, one line
per node, with `[ref_N]` tags on the interactive ones; a ref is a
`data-cafeops-ref` attribute the script stamps on the DOM node, so a later click is
`page.locator('[data-cafeops-ref="ref_N"]')` and nothing else. No Playwright private
API is involved.

Numbers only ever go up within a tab: the executor passes the last sequence number
in, the script continues from it, and an element that already carries a ref keeps it.
The counter lives in Python, not on `window`, so a ref from before a navigation can
never collide with one issued after it.

Everything here is a string of JavaScript. `INSTALL_JS` defines `window.__cafeops`
once per document; the three call wrappers install it lazily so the executor never
has to care whether a navigation wiped it.
"""

from __future__ import annotations

INSTALL_JS = r"""
(() => {
  if (window.__cafeops) return;

  const INTERACTIVE = new Set([
    'link', 'button', 'textbox', 'checkbox', 'radio', 'combobox', 'option', 'menuitem',
    'menuitemcheckbox', 'menuitemradio', 'tab', 'slider', 'switch', 'searchbox',
    'spinbutton', 'listbox',
  ]);
  // Names come from the subtree; descending would only repeat them.
  const NO_DESCEND = new Set([
    'button', 'textbox', 'searchbox', 'spinbutton', 'checkbox', 'radio', 'combobox',
    'slider', 'switch', 'img', 'iframe', 'progressbar', 'meter', 'option', 'menuitem', 'tab',
  ]);
  const SKIP_TAGS = new Set(['SCRIPT', 'STYLE', 'NOSCRIPT', 'TEMPLATE', 'HEAD', 'META',
    'LINK', 'TITLE', 'BASE']);
  // Roles whose accessible name may come from their content. Landmarks and
  // containers (main, list, table, form, ...) are named only by aria-label.
  const NAME_FROM_CONTENT = new Set([
    'button', 'link', 'heading', 'cell', 'columnheader', 'rowheader', 'gridcell', 'option',
    'tab', 'menuitem', 'menuitemcheckbox', 'menuitemradio', 'listitem', 'paragraph', 'term',
    'definition', 'blockquote', 'code', 'status', 'tooltip', 'switch', 'checkbox', 'radio',
    'treeitem', 'summary', 'caption', 'legend', 'label', 'alert', 'note', 'strong', 'emphasis',
  ]);
  const CONTEXT_SELECTOR = 'li, tr, article, section, fieldset, form, [role="listitem"], ' +
    '[role="row"], [role="group"], [role="article"], [role="region"]';

  const clean = (s, n) => {
    s = (s || '').replace(/\s+/g, ' ').trim();
    if (n && s.length > n) s = s.slice(0, n - 1) + '…';
    return s;
  };

  function roleOf(el) {
    const explicit = (el.getAttribute('role') || '').trim().split(/\s+/)[0].toLowerCase();
    if (explicit) return explicit;
    if (el.isContentEditable && el.getAttribute('contenteditable') !== null) return 'textbox';
    const tag = el.tagName.toLowerCase();
    switch (tag) {
      case 'a': case 'area': return el.hasAttribute('href') ? 'link' : 'generic';
      case 'button': case 'summary': return 'button';
      case 'input': {
        const t = (el.getAttribute('type') || 'text').toLowerCase();
        if (t === 'hidden') return null;
        if (['button', 'submit', 'reset', 'image', 'file'].includes(t)) return 'button';
        if (t === 'checkbox') return 'checkbox';
        if (t === 'radio') return 'radio';
        if (t === 'range') return 'slider';
        if (t === 'number') return 'spinbutton';
        if (t === 'search') return 'searchbox';
        return el.hasAttribute('list') ? 'combobox' : 'textbox';
      }
      case 'select': return (el.multiple || el.size > 1) ? 'listbox' : 'combobox';
      case 'textarea': return 'textbox';
      case 'option': return 'option';
      case 'img': case 'svg': case 'picture': return 'img';
      case 'h1': case 'h2': case 'h3': case 'h4': case 'h5': case 'h6': return 'heading';
      case 'li': return 'listitem';
      case 'ul': case 'ol': case 'menu': return 'list';
      case 'nav': return 'navigation';
      case 'main': return 'main';
      case 'header': return el.closest('article, section, main, aside') ? 'generic' : 'banner';
      case 'footer': return el.closest('article, section, main, aside') ? 'generic' : 'contentinfo';
      case 'aside': return 'complementary';
      case 'form': return 'form';
      case 'article': return 'article';
      case 'section': return (el.hasAttribute('aria-label') || el.hasAttribute('aria-labelledby'))
        ? 'region' : 'generic';
      case 'table': return 'table';
      case 'thead': case 'tbody': case 'tfoot': return 'rowgroup';
      case 'tr': return 'row';
      case 'th': return 'columnheader';
      case 'td': return 'cell';
      case 'p': return 'paragraph';
      case 'dialog': return 'dialog';
      case 'details': case 'fieldset': return 'group';
      case 'hr': return 'separator';
      case 'blockquote': return 'blockquote';
      case 'code': return 'code';
      case 'progress': return 'progressbar';
      case 'meter': return 'meter';
      case 'output': return 'status';
      case 'iframe': case 'frame': return 'iframe';
      case 'dl': return 'list';
      case 'dt': return 'term';
      case 'dd': return 'definition';
      default: return 'generic';
    }
  }

  function textOf(node, budget) {
    let out = '';
    const walk = (n) => {
      if (out.length > budget) return;
      if (n.nodeType === Node.TEXT_NODE) { out += n.data + ' '; return; }
      if (n.nodeType !== Node.ELEMENT_NODE) return;
      if (SKIP_TAGS.has(n.tagName)) return;
      if (n.getAttribute('aria-hidden') === 'true') return;
      const al = n.getAttribute('aria-label');
      if (al && al.trim()) { out += al + ' '; return; }
      const tag = n.tagName.toLowerCase();
      if (tag === 'img' || tag === 'area') { out += (n.getAttribute('alt') || '') + ' '; return; }
      if (tag === 'svg') {
        const t = n.querySelector('title');
        out += (t ? t.textContent : '') + ' ';
        return;
      }
      if (tag === 'input' || tag === 'textarea' || tag === 'select') return;
      for (const c of n.childNodes) walk(c);
      if (n.shadowRoot) for (const c of n.shadowRoot.childNodes) walk(c);
    };
    walk(node);
    return out;
  }

  function labelledBy(el) {
    const ids = (el.getAttribute('aria-labelledby') || '').trim();
    if (!ids) return '';
    return ids.split(/\s+/).map((id) => {
      const t = el.ownerDocument.getElementById(id);
      return t ? textOf(t, 200) : '';
    }).join(' ');
  }

  // Returns {name, fromText}: fromText says the name is the subtree's own text, so
  // emitting the text nodes again would only repeat it.
  function nameOf(el, role) {
    const lb = clean(labelledBy(el), 80);
    if (lb) return { name: lb, fromText: false };
    const al = clean(el.getAttribute('aria-label'), 80);
    if (al) return { name: al, fromText: false };
    const tag = el.tagName.toLowerCase();
    const type = (el.getAttribute('type') || '').toLowerCase();
    if (tag === 'img' || tag === 'area' || (tag === 'input' && type === 'image')) {
      const alt = el.getAttribute('alt') || el.getAttribute('title');
      return { name: clean(alt, 80), fromText: false };
    }
    if (tag === 'svg') {
      const t = el.querySelector('title');
      return { name: clean(t ? t.textContent : el.getAttribute('title'), 80), fromText: false };
    }
    if (tag === 'input' && ['button', 'submit', 'reset'].includes(type)) {
      const v = clean(el.value, 80);
      return { name: v || (type === 'submit' ? 'Submit' : type === 'reset' ? 'Reset' : ''),
        fromText: false };
    }
    if (tag === 'input' || tag === 'textarea' || tag === 'select') {
      const labels = el.labels ? Array.from(el.labels) : [];
      const fromLabel = clean(labels.map((l) => textOf(l, 200)).join(' '), 80);
      if (fromLabel) return { name: fromLabel, fromText: false };
      const ph = clean(el.getAttribute('placeholder'), 80);
      if (ph) return { name: ph, fromText: false };
      const ti = clean(el.getAttribute('title'), 80);
      if (ti) return { name: ti, fromText: false };
      return { name: clean(el.getAttribute('name'), 80), fromText: false };
    }
    if (tag === 'iframe') return { name: clean(el.getAttribute('title'), 80), fromText: false };
    if (NAME_FROM_CONTENT.has(role)) {
      const own = clean(textOf(el, 400), 80);
      if (own) return { name: own, fromText: true };
    }
    return { name: clean(el.getAttribute('title'), 80), fromText: false };
  }

  function rendered(el) {
    if (el.tagName === 'OPTION') return true;
    const st = el.ownerDocument.defaultView.getComputedStyle(el);
    if (st.display === 'none' || st.visibility === 'hidden') return false;
    return el.getClientRects().length > 0;
  }

  function inViewport(el) {
    const r = el.getBoundingClientRect();
    const w = el.ownerDocument.defaultView;
    return r.bottom > 0 && r.right > 0 && r.top < w.innerHeight && r.left < w.innerWidth;
  }

  function headingLevel(el) {
    const m = /^h([1-6])$/i.exec(el.tagName);
    if (m) return Number(m[1]);
    const al = Number(el.getAttribute('aria-level'));
    return al || 2;
  }

  function isChecked(el) {
    if (typeof el.checked === 'boolean') return el.checked;
    return el.getAttribute('aria-checked') === 'true';
  }

  function valueOf(el, role) {
    const tag = el.tagName.toLowerCase();
    if (tag === 'select') {
      const o = el.selectedOptions && el.selectedOptions[0];
      return o ? clean(o.textContent, 40) : '';
    }
    if (tag === 'input' || tag === 'textarea') {
      const t = (el.getAttribute('type') || '').toLowerCase();
      if (t === 'password') return el.value ? '•••' : '';
      return clean(el.value, 40);
    }
    if (role === 'textbox' && el.isContentEditable) return clean(el.textContent, 40);
    if (role === 'combobox' || role === 'slider' || role === 'spinbutton') {
      return clean(el.getAttribute('aria-valuetext') || el.getAttribute('aria-valuenow'), 40);
    }
    return '';
  }

  function lineFor(el, role, name, ref) {
    let s = role;
    if (name) s += ' "' + name.replace(/"/g, '\\"') + '"';
    if (ref) s += ' [' + ref + ']';
    if (role === 'heading') s += ' level=' + headingLevel(el);
    if (['textbox', 'searchbox', 'spinbutton', 'combobox', 'slider', 'listbox'].includes(role)) {
      const v = valueOf(el, role);
      if (v) s += ' value="' + v.replace(/"/g, '\\"') + '"';
    }
    if (['checkbox', 'radio', 'switch', 'menuitemcheckbox', 'menuitemradio'].includes(role)) {
      if (isChecked(el)) s += ' checked';
    }
    if (role === 'option' && (el.selected || el.getAttribute('aria-selected') === 'true')) {
      s += ' selected';
    }
    const exp = el.getAttribute('aria-expanded');
    if (exp === 'true') s += ' expanded';
    else if (exp === 'false') s += ' collapsed';
    if (el.disabled === true || el.getAttribute('aria-disabled') === 'true') s += ' disabled';
    return s;
  }

  function describe(el) {
    if (!el || el.nodeType !== Node.ELEMENT_NODE) return null;
    const role = roleOf(el) || 'generic';
    const n = nameOf(el, role);
    return {
      role: role,
      name: n.name || null,
      tag: el.tagName.toLowerCase(),
      href: el.href ? String(el.href) : (el.getAttribute('href') || null),
    };
  }

  function snapshot(opts) {
    const filter = opts.filter || 'visible';      // 'visible' | 'interactive' | 'all'
    const maxDepth = opts.depth || 15;
    const withContext = !!opts.context;
    let seq = opts.seq || 0;
    const lines = [];
    const nodes = [];
    let count = 0;

    let root = document.body;
    if (opts.ref) {
      root = document.querySelector('[data-cafeops-ref="' + opts.ref + '"]');
      if (!root) return { error: 'stale' };
    }
    if (!root) return { text: '', seq: seq, nodes: [], count: 0 };

    const walk = (node, depth, suppressText) => {
      if (depth > maxDepth) return;
      if (node.nodeType === Node.TEXT_NODE) {
        if (filter !== 'all' || suppressText) return;
        const t = clean(node.data, 200);
        if (t) lines.push('  '.repeat(depth) + 'text "' + t.replace(/"/g, '\\"') + '"');
        return;
      }
      if (node.nodeType !== Node.ELEMENT_NODE) return;
      const el = node;
      if (SKIP_TAGS.has(el.tagName)) return;
      if (el.getAttribute('aria-hidden') === 'true') return;
      if (!rendered(el)) return;

      const role = roleOf(el);
      let emit = !!role && role !== 'generic' && role !== 'presentation' && role !== 'none';
      if (emit && filter === 'interactive') emit = INTERACTIVE.has(role);
      if (emit && filter === 'visible' && !inViewport(el)) emit = false;
      if (emit && role === 'img') {
        const n = nameOf(el, role);
        if (!n.name) emit = false;                          // decorative
      }

      let childDepth = depth;
      let childSuppress = suppressText;
      if (emit) {
        const n = nameOf(el, role);
        let ref = null;
        if (INTERACTIVE.has(role) && el.tagName !== 'OPTION') {
          ref = el.getAttribute('data-cafeops-ref');
          if (!ref) { ref = 'ref_' + (++seq); el.setAttribute('data-cafeops-ref', ref); }
        }
        const line = lineFor(el, role, n.name, ref);
        lines.push('  '.repeat(depth) + line);
        count += 1;
        if (ref) {
          const info = { ref: ref, role: role, name: n.name || null, tag: el.tagName.toLowerCase(),
            href: el.href ? String(el.href) : (el.getAttribute('href') || null), line: line };
          if (withContext) {
            const anc = el.parentElement ? el.parentElement.closest(CONTEXT_SELECTOR) : null;
            info.context = anc ? clean(textOf(anc, 400), 200) : '';
          }
          nodes.push(info);
        }
        childDepth = depth + 1;
        if (n.fromText) childSuppress = true;
        if (NO_DESCEND.has(role) && el.tagName !== 'SELECT') return;
      }

      if (el.tagName === 'SELECT') {
        if (!emit || childDepth > maxDepth) return;
        for (const o of el.options) {
          const optLine = lineFor(o, 'option', clean(o.textContent, 80), null);
          lines.push('  '.repeat(childDepth) + optLine);
          count += 1;
        }
        return;
      }
      for (const c of el.childNodes) walk(c, childDepth, childSuppress);
      if (el.shadowRoot) {
        for (const c of el.shadowRoot.childNodes) walk(c, childDepth, childSuppress);
      }
    };

    walk(root, 0, false);
    return { text: lines.join('\n'), seq: seq, nodes: nodes, count: count };
  }

  function pageText() {
    const root = document.querySelector('main, article, [role="main"]') || document.body;
    if (!root) return '';
    let t = root.innerText || root.textContent || '';
    t = t.replace(/[ \t\u00a0]+/g, ' ').replace(/ *\n */g, '\n').replace(/\n{3,}/g, '\n\n');
    return t.trim();
  }

  window.__cafeops = { snapshot: snapshot, describe: describe, pageText: pageText };
})();
"""

#: `page.evaluate(SNAPSHOT_JS, opts)` -> {text, seq, nodes, count} or {error: "stale"}.
SNAPSHOT_JS = "(opts) => { " + INSTALL_JS + " return window.__cafeops.snapshot(opts); }"

#: `locator.evaluate(DESCRIBE_JS)` -> {role, name, tag, href} for that element.
DESCRIBE_JS = "(el) => { " + INSTALL_JS + " return window.__cafeops.describe(el); }"

#: `page.evaluate(PAGE_TEXT_JS)` -> the article-focused plain text.
PAGE_TEXT_JS = "() => { " + INSTALL_JS + " return window.__cafeops.pageText(); }"

#: `locator.evaluate(CONTROL_KIND_JS)` -> what kind of control a ref is, for form_input.
CONTROL_KIND_JS = """(el) => ({
  tag: el.tagName.toLowerCase(),
  type: (el.getAttribute('type') || '').toLowerCase(),
  editable: !!el.isContentEditable,
  role: (el.getAttribute('role') || '').toLowerCase(),
  ariaChecked: el.getAttribute('aria-checked'),
})"""

__all__ = ["CONTROL_KIND_JS", "DESCRIBE_JS", "INSTALL_JS", "PAGE_TEXT_JS", "SNAPSHOT_JS"]
