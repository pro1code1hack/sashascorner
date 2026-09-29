// /order/p/<slug>: the item page. Photo, name, description and the info tabs on the
// left; sizes, option groups and upsells on the right (stacked under 900px); the
// stepper and ADD TO ORDER pinned to the bottom. `?line=<n>` edits basket line n.
import { useMemo, useRef, useState } from 'preact/hooks';
import { Photo } from '../components/Photo';
import { TopBar } from '../components/TopBar';
import { UpsellRow } from '../components/UpsellRow';
import { allergenState, allergenText, delta, DIETARY_WORDS, gbp, kcal, kcalDelta, plural, word } from '../format';
import { go, paths } from '../router';
import { basket, catalogue, config, loadCatalogue, loadError, productBySlug, type BasketLine } from '../store';
import { showToast } from '../toast';
import type { OptionGroup, Product as P, Size } from '../types';
import { BottomBar, Button, Loading, Notice, SkeletonItem, Stepper, Tile } from '../ui';
import { NotFound } from './NotFound';
import { useTitle } from './useTitle';

const NUTRITION: [string, string][] = [
  ['energy_kj', 'Energy (kJ)'],
  ['fat_g', 'Fat (g)'],
  ['saturates_g', 'of which saturates (g)'],
  ['carbs_g', 'Carbohydrate (g)'],
  ['sugars_g', 'of which sugars (g)'],
  ['protein_g', 'Protein (g)'],
  ['salt_g', 'Salt (g)'],
];

interface Props {
  slug: string;
  lineParam: string | null;
}

export function Product({ slug, lineParam }: Props) {
  const cat = catalogue.value;
  const p = cat ? productBySlug(cat, slug) : undefined;
  useTitle(p?.name ?? 'Item');
  if (!cat) {
    return (
      <>
        <TopBar back={{ href: paths.overview(), label: 'Menu' }} />
        <div class="wrap sh-view">
          {loadError.value ? (
            <Notice tone="error">
              {loadError.value}{' '}
              <button type="button" class="sh-linkbtn" onClick={() => void loadCatalogue()}>
                Try again
              </button>
            </Notice>
          ) : (
            <>
              <Loading what="this item" />
              <SkeletonItem />
            </>
          )}
        </div>
      </>
    );
  }
  if (!p) return <NotFound what="item" />;
  const lineIndex = lineParam !== null && /^\d+$/.test(lineParam) ? Number(lineParam) : null;
  const editing = lineIndex !== null ? basket.lines.value[lineIndex] : undefined;
  return <Editor product={p} editIndex={editing && editing.product_id === p.id ? lineIndex : null} initial={editing && editing.product_id === p.id ? editing : null} />;
}

function defaults(p: P): { sizeId: number; optionIds: number[] } {
  const size = p.sizes.find((s) => s.code === p.default_size) ?? p.sizes.reduce((a, b) => (b.price_pence < a.price_pence ? b : a), p.sizes[0]);
  const optionIds: number[] = [];
  for (const g of p.option_groups) {
    const d = g.options.filter((o) => o.is_default && o.available).map((o) => o.id);
    optionIds.push(...(g.kind === 'single' ? d.slice(0, 1) : d.slice(0, g.max_select ?? d.length)));
  }
  return { sizeId: size?.menu_item_id ?? 0, optionIds };
}

function groupError(g: OptionGroup, chosen: number[]): string | null {
  const n = g.options.filter((o) => chosen.includes(o.id)).length;
  if (g.kind === 'single') return g.required && n !== 1 ? 'Choose one' : null;
  const min = Math.max(g.min_select, g.required ? 1 : 0);
  if (n < min) return min === 1 ? 'Choose at least one' : `Choose at least ${min}`;
  if (g.max_select !== null && n > g.max_select) return `Choose up to ${g.max_select}`;
  return null;
}

function Editor({ product: p, editIndex, initial }: { product: P; editIndex: number | null; initial: BasketLine | null }) {
  const cat = catalogue.value!;
  const cfg = config.value;
  const d = useMemo(() => defaults(p), [p]);
  const [sizeId, setSizeId] = useState<number>(initial?.menu_item_id ?? d.sizeId);
  const [chosen, setChosen] = useState<number[]>(initial?.option_ids ?? d.optionIds);
  const [qty, setQty] = useState<number>(initial?.qty ?? 1);
  const [tab, setTab] = useState<'dietary' | 'ingredients' | 'nutrition'>('dietary');
  const [showErrors, setShowErrors] = useState(false);
  const [open, setOpen] = useState<Set<number>>(() => new Set(p.option_groups.filter((g) => g.collapsed && g.options.some((o) => chosen.includes(o.id))).map((g) => g.id)));
  const groupsRef = useRef<HTMLDivElement>(null);

  const size: Size | undefined = p.sizes.find((s) => s.menu_item_id === sizeId) ?? p.sizes[0];
  const defaultSize = p.sizes.find((s) => s.menu_item_id === d.sizeId) ?? p.sizes[0];
  const allOptions = p.option_groups.flatMap((g) => g.options);
  const optionTotal = chosen.reduce((s, id) => s + (allOptions.find((o) => o.id === id)?.price_delta_pence ?? 0), 0);
  const unit = (size?.price_pence ?? 0) + optionTotal;
  const total = unit * qty;
  const errors = new Map(p.option_groups.map((g) => [g.id, groupError(g, chosen)] as const));
  const invalid = [...errors.values()].some(Boolean);
  const sizeKcal = (s: Size): number | null => p.kcal_by_size?.[s.code] ?? s.kcal ?? null;

  const pick = (g: OptionGroup, id: number, on: boolean) => {
    setChosen((cur) => {
      const others = cur.filter((x) => !g.options.some((o) => o.id === x));
      const mine = cur.filter((x) => g.options.some((o) => o.id === x));
      if (g.kind === 'single') return on ? [...others, id] : g.required ? cur : others;
      const next = on ? [...mine.filter((x) => x !== id), id] : mine.filter((x) => x !== id);
      return [...others, ...next];
    });
  };
  const submit = () => {
    if (invalid || !size) {
      setShowErrors(true);
      const firstBad = p.option_groups.find((g) => errors.get(g.id));
      if (firstBad) {
        setOpen((o) => new Set(o).add(firstBad.id));
        requestAnimationFrame(() => {
          const el = groupsRef.current?.querySelector<HTMLElement>(`[data-group="${firstBad.id}"]`);
          el?.scrollIntoView({ block: 'center', behavior: matchMedia('(prefers-reduced-motion: reduce)').matches ? 'auto' : 'smooth' });
          el?.querySelector<HTMLElement>('input')?.focus({ preventScroll: true });
        });
      }
      return;
    }
    const line: BasketLine = { product_id: p.id, menu_item_id: size.menu_item_id, qty, option_ids: chosen };
    if (editIndex !== null) {
      basket.update(editIndex, line);
      go(paths.basket());
      return;
    }
    basket.add(line);
    showToast(`${p.name} added to your order.`, { label: 'View order', href: paths.basket() });
    go(p.category_slug ? paths.category(p.category_slug) : paths.overview());
  };

  const category = cat.categories.find((c) => c.slug === p.category_slug);
  const back = category ? { href: paths.category(category.slug), label: category.name } : { href: paths.overview(), label: 'Menu' };
  const allergens = allergenText(p);
  const allergensKnown = allergenState(p) !== 'unknown';
  const dietary = p.dietary.map((x) => word(DIETARY_WORDS, x));
  const energy = size ? sizeKcal(size) : p.kcal;

  return (
    <>
      <TopBar back={editIndex !== null ? { href: paths.basket(), label: 'My order' } : back} />
      <article class="wrap sh-view sh-item">
        <div class="sh-item__info">
          <Photo src={p.photo_url} alt={p.name} name={p.name} class="sh-item__img" eager sizes="(min-width: 900px) 520px, 100vw" />
          <div class="sh-item__head">
            {p.badge && <p class="sh-badge">{p.badge}</p>}
            <h1 class="sh-h1 sh-item__name" tabIndex={-1}>
              {p.name}
            </h1>
            {p.description && <p class="sh-item__desc">{p.description}</p>}
            {p.note && <p class="sh-item__note">{p.note}</p>}
            {!p.available && <Notice tone="warn">Sold out today.</Notice>}
          </div>

          <div class="sh-info">
            <div class="sh-info__tabs" role="tablist" aria-label="About this item">
              {(
                [
                  ['dietary', 'Dietary info'],
                  ['ingredients', 'Ingredients'],
                  ['nutrition', 'Nutrition'],
                ] as const
              ).map(([k, label]) => (
                <button
                  key={k}
                  type="button"
                  role="tab"
                  id={`tab-${k}`}
                  aria-selected={tab === k}
                  aria-controls={`panel-${k}`}
                  tabIndex={tab === k ? 0 : -1}
                  class={['sh-info__tab', tab === k && 'is-on'].filter(Boolean).join(' ')}
                  onClick={() => setTab(k)}
                  onKeyDown={(e) => {
                    const order = ['dietary', 'ingredients', 'nutrition'] as const;
                    const i = order.indexOf(k);
                    const next = e.key === 'ArrowRight' ? order[(i + 1) % 3] : e.key === 'ArrowLeft' ? order[(i + 2) % 3] : null;
                    if (next) {
                      e.preventDefault();
                      setTab(next);
                      document.getElementById(`tab-${next}`)?.focus();
                    }
                  }}
                >
                  {label}
                </button>
              ))}
            </div>
            <div id="panel-dietary" role="tabpanel" aria-labelledby="tab-dietary" hidden={tab !== 'dietary'} class="sh-info__panel">
              <p>
                <strong>Energy:</strong> {energy !== null && energy !== undefined ? <span class="num">{kcal(energy)}</span> : '—'}
                {size && p.sizes.length > 1 && size.label ? ` (${size.label})` : ''}
              </p>
              <p class={allergensKnown ? undefined : 'sh-allergens--unknown'}>
                <strong>Allergens:</strong> {allergens}
              </p>
              {dietary.length > 0 && <p class="sh-muted">{dietary.join(' · ')}</p>}
              {cfg?.allergen_notice && <p class="sh-muted sh-info__small">{cfg.allergen_notice}</p>}
            </div>
            <div id="panel-ingredients" role="tabpanel" aria-labelledby="tab-ingredients" hidden={tab !== 'ingredients'} class="sh-info__panel">
              <p>{p.ingredients_text || 'Ask us at the counter for the full list.'}</p>
            </div>
            <div id="panel-nutrition" role="tabpanel" aria-labelledby="tab-nutrition" hidden={tab !== 'nutrition'} class="sh-info__panel">
              {p.nutrition ? (
                <table class="sh-nutri">
                  <caption class="sr-only">Nutrition per serving</caption>
                  <tbody>
                    {NUTRITION.map(([k, label]) => (
                      <tr key={k}>
                        <th scope="row">{label}</th>
                        <td class="num">{p.nutrition?.[k] ?? '—'}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              ) : (
                <p>Nutrition figures are not listed for this item.</p>
              )}
            </div>
          </div>
        </div>

        <div class="sh-item__options" ref={groupsRef}>
          {p.sizes.length > 1 && (
            <fieldset class="sh-group">
              <legend class="sh-group__name">Size</legend>
              <div class="sh-tiles">
                {p.sizes.map((s) => {
                  const dp = s.price_pence - (defaultSize?.price_pence ?? 0);
                  const k = sizeKcal(s);
                  const dk = defaultSize ? sizeKcal(defaultSize) : null;
                  const isDefault = s.menu_item_id === defaultSize?.menu_item_id;
                  return (
                    <Tile
                      key={s.menu_item_id}
                      name="size"
                      value={String(s.menu_item_id)}
                      kind="single"
                      checked={s.menu_item_id === sizeId}
                      onChange={() => setSizeId(s.menu_item_id)}
                      title={s.label || 'One size'}
                      meta={
                        <>
                          <span class="num">{isDefault ? gbp(s.price_pence) : delta(dp) || gbp(s.price_pence)}</span>
                          {k !== null && (isDefault || dk === null ? <span class="num"> · {kcal(k)}</span> : dk !== k ? <span class="num"> · {kcalDelta(k - dk)}</span> : null)}
                        </>
                      }
                    />
                  );
                })}
              </div>
            </fieldset>
          )}

          {p.option_groups.map((g) => {
            const err = errors.get(g.id) ?? null;
            const chosenHere = g.options.filter((o) => chosen.includes(o.id)).length;
            const full = g.kind === 'multi' && g.max_select !== null && chosenHere >= g.max_select;
            const isOpen = !g.collapsed || open.has(g.id);
            const layout = g.layout === 'photo_tiles' ? 'photo' : g.layout === 'checklist' ? 'row' : 'tile';
            const hint =
              g.kind === 'single'
                ? g.required
                  ? 'Choose one'
                  : 'Optional'
                : g.max_select !== null
                  ? `Choose up to ${g.max_select}`
                  : g.min_select > 0
                    ? `Choose at least ${g.min_select}`
                    : 'Optional';
            return (
              <fieldset class={['sh-group', showErrors && err && 'has-error'].filter(Boolean).join(' ')} data-group={g.id} key={g.id} aria-describedby={`gh-${g.id}`}>
                <legend class="sh-group__name">{g.name}</legend>
                <p class="sh-group__hint" id={`gh-${g.id}`}>
                  {g.prompt && !g.collapsed ? `${g.prompt}. ` : ''}
                  {hint}
                  {showErrors && err && (
                    <span class="sh-group__err" role="alert">
                      {' '}
                      · {err}
                    </span>
                  )}
                </p>
                {!isOpen ? (
                  <button type="button" class="sh-disclose" aria-expanded="false" onClick={() => setOpen((o) => new Set(o).add(g.id))}>
                    <span>{g.prompt ?? `Add ${g.name.toLowerCase()}`}</span>
                    <svg viewBox="0 0 20 20" width="18" height="18" aria-hidden="true" focusable="false">
                      <path d="M10 4v12M4 10h12" stroke="currentColor" stroke-width="2" stroke-linecap="round" />
                    </svg>
                  </button>
                ) : (
                  <div class={['sh-tiles', layout === 'row' && 'sh-tiles--rows', layout === 'photo' && 'sh-tiles--photo'].filter(Boolean).join(' ')}>
                    {g.options.map((o) => {
                      const on = chosen.includes(o.id);
                      const meta = [o.price_delta_pence ? delta(o.price_delta_pence) : '', o.kcal !== null ? kcal(o.kcal) : ''].filter(Boolean).join(' · ');
                      return (
                        <Tile
                          key={o.id}
                          name={`g${g.id}`}
                          value={String(o.id)}
                          kind={g.kind}
                          checked={on}
                          disabled={!on && full}
                          soldOut={!o.available}
                          onChange={(c) => pick(g, o.id, c)}
                          title={o.name}
                          meta={meta ? <span class="num">{meta}</span> : undefined}
                          tagline={o.description}
                          photo={o.photo_url}
                          layout={layout}
                        />
                      );
                    })}
                  </div>
                )}
              </fieldset>
            );
          })}

          {p.upsells.map((u) => (
            <UpsellRow upsell={u} cat={cat} exclude={p.id} key={u.heading} />
          ))}
        </div>
      </article>

      <BottomBar label="Add to order">
        <Stepper value={qty} max={basket.MAX_QTY} onChange={setQty} onDark />
        <Button tone="caramel" class="sh-bar__cta" onClick={submit} disabled={!p.available}>
          <span class="sh-bar__label">{editIndex !== null ? 'Update order' : 'Add to order'}</span>
          <span class="num">{gbp(total)}</span>
          {qty > 1 && <span class="sr-only">, {plural(qty, 'item')}</span>}
        </Button>
      </BottomBar>
    </>
  );
}
