// Build-time data. `sashasite menu-export` / `info-export` write these JSON files
// from the live database before `astro build`, so the HTML a crawler reads carries
// the real menu. A glob (not a static import) keeps a fresh checkout building
// before the exports exist -- the pages then render their empty states.
import type { Info, Menu } from './types';

const files = import.meta.glob<{ default: unknown }>('../data/*.json', { eager: true });
const pick = <T>(name: string): T | null =>
  (files[`../data/${name}.json`]?.default as T | undefined) ?? null;

export const menu: Menu = pick<Menu>('menu') ?? { generated_at: '', categories: [] };
export const info: Info | null = pick<Info>('info');

export const signatures = menu.categories.flatMap((c) =>
  c.items.filter((i) => i.signature).map((i) => ({ ...i, category: c.name })),
);

export const socialLinks = (i: Info | null) =>
  i
    ? (
        [
          ['Instagram', i.socials.instagram],
          ['Facebook', i.socials.facebook],
          ['TikTok', i.socials.tiktok],
        ] as const
      ).filter(([, url]) => url)
    : [];
