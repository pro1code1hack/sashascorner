// Menu slices for the search landing pages (/matcha-dundee, /bubble-tea-dundee,
// /kyiv-cake). Everything comes from the build-time menu.json: an item or price that
// is not on the board never appears on these pages or in their JSON-LD.
import { menu } from '../lib/data';
import type { MenuItem } from '../lib/types';

export type Item = MenuItem & { cat: string };
export interface Section {
  id: string;
  title: string;
  blurb?: string | null;
  items: Item[];
}

export const items: Item[] = menu.categories.flatMap((c) => c.items.map((i) => ({ ...i, cat: c.slug })));
export const category = (slug: string) => menu.categories.find((c) => c.slug === slug) ?? null;
export const inCategory = (slug: string) => items.filter((i) => i.cat === slug);
export const matching = (re: RegExp) => items.filter((i) => re.test(i.name));

export const lowest = (list: Array<Pick<MenuItem, 'sizes'>>): number | null => {
  const all = list.flatMap((i) => i.sizes.map((z) => z.price_pence));
  return all.length ? Math.min(...all) : null;
};

/** Drop empty sections and any item already shown in an earlier section. */
export function tidy(sections: Section[]): Section[] {
  const seen = new Set<string>();
  return sections
    .map((s) => ({
      ...s,
      items: s.items.filter((i) => (seen.has(i.id) ? false : (seen.add(i.id), true))),
    }))
    .filter((s) => s.items.length > 0);
}

/** schema.org Menu for one landing page, in the same shape as /menu's. */
export function menuJsonLd(site: URL, path: string, name: string, sections: Section[]) {
  const url = new URL(path, site).href;
  return {
    '@type': 'Menu',
    '@id': `${url}#menu`,
    name,
    url,
    inLanguage: 'en-GB',
    hasMenuSection: sections.map((s) => ({
      '@type': 'MenuSection',
      name: s.title,
      ...(s.blurb ? { description: s.blurb } : {}),
      hasMenuItem: s.items.map((i) => ({
        '@type': 'MenuItem',
        name: i.name,
        url: new URL(`/menu#${i.id}`, site).href,
        ...(i.description || i.note ? { description: i.description ?? i.note } : {}),
        offers: i.sizes.map((z) => ({
          '@type': 'Offer',
          price: (z.price_pence / 100).toFixed(2),
          priceCurrency: 'GBP',
          ...(z.code !== 'One' ? { name: z.label } : {}),
        })),
      })),
    })),
  };
}

export const DELIVEROO = 'https://deliveroo.co.uk/menu/fife-and-perthshire/dundee/sashas-corner';
export const JUST_EAT = 'https://www.just-eat.co.uk/restaurants-sashas-corner-dundee/menu';
