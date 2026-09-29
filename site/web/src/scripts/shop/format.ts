// Pence in, text out. The only place money becomes a string in the ordering app.
import type { BasketLine } from './store';
import { optionsOf, productById, sizeOf } from './store';
import type { AllergensState, Catalogue, Product } from './types';

export const gbp = (pence: number): string => {
  const sign = pence < 0 ? '-' : '';
  const abs = Math.abs(pence);
  return `${sign}£${Math.floor(abs / 100)}.${String(abs % 100).padStart(2, '0')}`;
};

/** "+£0.50", "-£0.20"; empty for zero (a tile shows nothing rather than "+£0.00"). */
export const delta = (pence: number): string => (pence === 0 ? '' : `${pence > 0 ? '+' : '-'}${gbp(Math.abs(pence))}`);

export const kcal = (n: number | null | undefined): string => (n === null || n === undefined ? '' : `${n} kcal`);
export const kcalDelta = (n: number): string => (n === 0 ? '' : `${n > 0 ? '+' : '-'}${Math.abs(n)} kcal`);

/** "Iced latte (Medium)"; a single-size item is just its name. */
export function lineTitle(line: BasketLine, cat: Catalogue): string {
  const p = productById(cat, line.product_id);
  if (!p) return 'Item';
  const size = sizeOf(p, line.menu_item_id);
  return size && size.code !== 'ONE' && size.label ? `${p.name} (${size.label})` : p.name;
}

/** "Oat milk +£0.50, Extra shot +£0.60, Extra hot" */
export function optionSummary(line: BasketLine, cat: Catalogue): string {
  const p = productById(cat, line.product_id);
  if (!p) return '';
  return optionsOf(p, line.option_ids)
    .map(({ option }) => (option.price_delta_pence ? `${option.name} ${delta(option.price_delta_pence)}` : option.name))
    .join(', ');
}

export const plural = (n: number, one: string, many = `${one}s`) => `${n} ${n === 1 ? one : many}`;

/** Category tile / photo placeholder initial. */
export const initial = (name: string) => (name.trim()[0] ?? '?').toUpperCase();

// ---- allergens, honestly ---------------------------------------------------------
// An empty list means "nobody has filled this in yet" until the API says otherwise
// (`allergens_state`, Agent F). "No listed allergens" would read as a promise.
export const ALLERGEN_WORDS: Record<string, string> = {
  milk: 'Milk',
  gluten: 'Gluten',
  nuts: 'Nuts',
  soya: 'Soya',
  egg: 'Egg',
  sesame: 'Sesame',
  sulphites: 'Sulphites',
};
export const DIETARY_WORDS: Record<string, string> = {
  vegan: 'Vegan',
  vegetarian: 'Vegetarian',
  'gluten-free': 'Gluten-free',
  'decaf-available': 'Decaf available',
  'contains-caffeine': 'Contains caffeine',
};
export const word = (map: Record<string, string>, k: string): string =>
  map[k] ?? k.replace(/-/g, ' ').replace(/^./, (c) => c.toUpperCase());

export function allergenState(p: Pick<Product, 'allergens' | 'allergens_state'>): AllergensState {
  if (p.allergens_state === 'unknown' || p.allergens_state === 'none' || p.allergens_state === 'listed') return p.allergens_state;
  return p.allergens.length ? 'listed' : 'unknown';
}
export const ALLERGENS_UNKNOWN = 'Allergens not listed yet — ask at the counter';
export const ALLERGENS_NONE = 'No allergens declared';
/** One sentence, the same everywhere allergens appear: item page, info card, basket, checkout. */
export function allergenText(p: Pick<Product, 'allergens' | 'allergens_state'>): string {
  const state = allergenState(p);
  if (state === 'unknown') return ALLERGENS_UNKNOWN;
  if (state === 'none') return ALLERGENS_NONE;
  return `Contains ${p.allergens.map((a) => word(ALLERGEN_WORDS, a).toLowerCase()).join(', ')}`;
}
