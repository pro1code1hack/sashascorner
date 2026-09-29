// Facts shared by /faq, /llms.txt and /llms-full.txt, built at build time from the
// same menu.json / info.json the pages use. The leading underscore keeps Astro from
// routing this file.
//
// Rule: every sentence here is either in site/BRIEF.md's verified facts or computed
// from the menu data. Anything else is an "ask the café" answer, never a guess.
import { info, menu } from '../lib/data';
import { clock, gbp, WEEKDAYS } from '../lib/format';
import type { MenuItem } from '../lib/types';

export const DELIVEROO = 'https://deliveroo.co.uk/menu/fife-and-perthshire/dundee/sashas-corner';
export const JUST_EAT = 'https://www.just-eat.co.uk/restaurants-sashas-corner-dundee/menu';
export const INSTAGRAM = info?.socials.instagram || 'https://www.instagram.com/sashascorner_uk';

const all = menu.categories.flatMap((c) => c.items.map((i) => ({ ...i, cat: c.slug })));
const FOOD = new Set(['breakfast', 'lunch', 'waffles', 'cakes']);
export const drinks = all.filter((i) => !FOOD.has(i.cat));
export const itemCount = all.length;

const low = (i: Pick<MenuItem, 'sizes'>) => Math.min(...i.sizes.map((z) => z.price_pence));
/** Cheapest price among the items, as "£1.90"; null when none match. */
export const from = (items: Array<Pick<MenuItem, 'sizes'>>): string | null =>
  items.length ? gbp(Math.min(...items.map(low))) : null;
export const find = (re: RegExp) => all.find((i) => re.test(i.name));
export const inCat = (slug: string) => all.filter((i) => i.cat === slug);
/** "S £3.50 / M £3.80 / XL £4.10", or "£4.00" for one size. */
export const priceList = (i: Pick<MenuItem, 'sizes'>) =>
  i.sizes.length === 1
    ? gbp(i.sizes[0].price_pence)
    : i.sizes.map((z) => `${z.label || z.code} ${gbp(z.price_pence)}`).join(' / ');

// ---- hours, grouped: "Monday to Saturday 9am–7pm; Sunday 9am–5pm" ----------------
export const hoursText = (() => {
  const hours = info?.hours ?? [];
  const runs: Array<{ from: number; to: number; text: string }> = [];
  for (const h of hours) {
    const text = h.closed ? 'closed' : `${clock(h.open)}–${clock(h.close)}`;
    const last = runs.at(-1);
    if (last && last.text === text && last.to === h.weekday - 1) last.to = h.weekday;
    else runs.push({ from: h.weekday, to: h.weekday, text });
  }
  return runs
    .map((r) =>
      r.from === r.to
        ? `${WEEKDAYS[r.from]} ${r.text}`
        : `${WEEKDAYS[r.from]} to ${WEEKDAYS[r.to]} ${r.text}`,
    )
    .join('; ');
})();

export const address = info
  ? `${info.address.line1}, ${info.address.city} ${info.address.postcode}`
  : '23 Commercial Street, Dundee DD1 3DD';
export const phone = info?.phone ?? '';

// ---- menu slices the answers quote ------------------------------------------------
const greenMatcha = drinks.filter((i) => /matcha/i.test(i.name) && !/blue/i.test(i.name));
const blueMatcha = drinks.filter((i) => /blue/i.test(i.name) && /matcha/i.test(i.name));
const hojicha = drinks.filter((i) => /hojicha/i.test(i.name));
const bubble = drinks.filter((i) => /bubble tea/i.test(i.name));
const espresso = find(/^single espresso$/i);
const latte = find(/^latte$/i);
const matchaLatte = find(/^(hot )?matcha( latte)?$/i);
const raff = find(/raff/i);
const kyivSlice = find(/kyiv.*slice/i);
const kyivWhole = find(/kyiv.*whole/i);
const extras = menu.extras?.items ?? [];
const milks = extras.filter((e) => /milk/i.test(e.name));
const syrup = extras.find((e) => /syrup/i.test(e.name));

export const highlights = {
  greenMatcha,
  blueMatcha,
  hojicha,
  bubble,
  espresso,
  latte,
  matchaLatte,
  raff,
  kyivSlice,
  kyivWhole,
  milks,
  syrup,
  breakfastFrom: from(inCat('breakfast')),
  lunchFrom: from(inCat('lunch')),
  wafflesFrom: from(inCat('waffles')),
  cakesFrom: from(inCat('cakes').filter((i) => !/whole/i.test(i.name))),
  drinkCount: drinks.length,
};

const names = (items: Array<{ name: string }>, n = 99) =>
  items
    .slice(0, n)
    .map((i) => i.name.toLowerCase())
    .join(', ');

// ---- the FAQ ------------------------------------------------------------------------
export interface Faq {
  id: string;
  q: string;
  /** Answer as HTML: plain text plus <a> links only. */
  html: string;
  /** Kept out of FAQPage JSON-LD and llms files: an honest "we haven't said yet". */
  pageOnly?: boolean;
  /** Rendered as an HTML comment before the answer, for the owner. */
  ownerNote?: string;
}

const a = (href: string, text: string, external = false) =>
  `<a href="${href}"${external ? ' target="_blank" rel="noopener"' : ''}>${text}</a>`;

const booking = info?.booking;

export const faqs: Faq[] = [
  {
    id: 'where',
    q: "Where is Sasha's Corner?",
    html: `At ${address}, in Dundee city centre. It's on Commercial Street between Dock Street and the High Street and Seagate crossing, about 700 m (roughly a 10-minute walk) from Dundee railway station. ${a('/visit', 'Directions and a map')}.`,
  },
  {
    id: 'hours',
    q: 'What are the opening hours?',
    html: `${hoursText}. Directions are on the ${a('/visit', 'visit page')}.`,
    ownerNote: 'bank holiday and Christmas hours are not known yet; add them here when set',
  },
  {
    id: 'book',
    q: 'Do I need to book a table?',
    html: `No. You can walk in to eat in or take away. If you'd like a table kept for you, ${a('/book', 'book online')}${
      booking
        ? ` for up to ${booking.max_party} people, up to ${booking.horizon_days} days ahead and at least ${booking.min_lead_minutes >= 60 ? `${booking.min_lead_minutes / 60} hour` : `${booking.min_lead_minutes} minutes`} before you arrive`
        : ''
    }. It's confirmed straight away.`,
  },
  {
    id: 'takeaway-delivery',
    q: 'Do you do takeaway and delivery?',
    html: `Yes. Everything on the menu can be taken away: order at the counter, or ${a('/order', 'order ahead online')} and collect it from 23 Commercial Street. For delivery, we're on ${a(DELIVEROO, 'Deliveroo', true)} and ${a(JUST_EAT, 'Just Eat', true)}, with no-contact delivery. The apps show whether they deliver to your address. ${a('/delivery', 'More on cakes and delivery')}.`,
  },
  {
    id: 'matcha',
    q: 'What matcha do you have?',
    html: `Matcha is whisked fresh in front of you. The board has ${greenMatcha.length} green matcha drinks, hot and iced (${names(greenMatcha.filter((i) => !/iced/i.test(i.name)), 6)} and more), from ${from(greenMatcha)}, and ${blueMatcha.length} blue matcha drinks (${names(blueMatcha)}), from ${from(blueMatcha)}.${
      hojicha.length ? ` This season there's also iced hojicha (roasted green tea), from ${from(hojicha)}.` : ''
    } ${a('/menu', 'Full menu with prices')}.`,
  },
  ...(raff
    ? [
        {
          id: 'raff',
          q: 'What is Raff coffee?',
          html: `${raff.description ?? 'A coffee made with cream and vanilla sugar.'} We make it in three sizes: ${priceList(raff)}.`,
        },
      ]
    : []),
  ...(kyivSlice || kyivWhole
    ? [
        {
          id: 'kyiv-cake',
          q: 'What is Kyiv cake, and can I order a whole one?',
          html: `${kyivSlice?.description ?? ''} ${kyivSlice ? `We sell it by the slice at ${gbp(low(kyivSlice))}` : ''}${
            kyivWhole
              ? `${kyivSlice ? ', and' : 'We sell'} whole at ${gbp(low(kyivWhole))}. A whole cake is made to order: ${a('/delivery#ahead', 'order it ahead')} and tell us the day you'd like to collect it from Commercial Street`
              : ''
          }.`.trim(),
          ownerNote: 'how many days notice a whole Kyiv cake needs',
        },
      ]
    : []),
  {
    id: 'prices',
    q: 'How much is a coffee?',
    html: [
      espresso && `An espresso is ${gbp(low(espresso))}`,
      latte && `a latte from ${gbp(low(latte))}`,
      matchaLatte && `a matcha latte from ${gbp(low(matchaLatte))}`,
      bubble.length && `bubble tea from ${from(bubble)}`,
    ]
      .filter(Boolean)
      .join(', ')
      .concat(
        `. Food: breakfast from ${highlights.breakfastFrom}, lunch from ${highlights.lunchFrom}, brunch waffles from ${highlights.wafflesFrom} and cakes from ${highlights.cakesFrom}. ${a('/menu', 'Every price is on the menu page')}.`,
      ),
  },
  ...(milks.length
    ? [
        {
          id: 'milks',
          q: 'Do you have dairy-free milk?',
          html: `Yes. ${milks
            .map((m, i) => `${i ? m.name.toLowerCase() : m.name} ${/free/i.test(m.price_text) ? 'at no extra charge' : m.price_text}`)
            .join(', and ')}.${syrup ? ` Syrups: ${syrup.name.toLowerCase()}, ${syrup.price_text}.` : ''} If you have an allergy, tell us at the counter before you order.`,
        },
      ]
    : []),
  {
    id: 'food',
    q: 'Do you serve breakfast and brunch?',
    html: `Yes: breakfast (${names(inCat('breakfast'), 5)}) from ${highlights.breakfastFrom}; lunch (soup of the day, salads, filled croissants, paninis and ciabatta) from ${highlights.lunchFrom}; and brunch waffles, savoury with salad or sweet with fruit and ice cream, from ${highlights.wafflesFrom}.`,
  },
  {
    id: 'bubble-tea',
    q: 'Do you sell bubble tea?',
    html: `Yes: ${bubble.length} bubble teas with tapioca pearls (${names(bubble).replace(/ bubble tea/g, '')}), from ${from(bubble)}.${bubble.some((i) => i.seasonal) ? ` Some are seasonal; the ${a('/menu', 'menu')} shows what's on now.` : ''}`,
  },
  {
    id: 'games',
    q: 'Can we play board games or chess?',
    html: 'There is a chess set and a shelf of board games in the café.',
    ownerNote: 'whether customers can use them freely, and anything else (e.g. games nights)',
  },
  {
    id: 'contact',
    q: 'How do I contact the café?',
    html: `Call ${phone ? `<a href="tel:+44${phone.replace(/\s+/g, '').replace(/^0/, '')}">${phone}</a>` : 'us'}, or ${a('/visit#contact', 'send a message')} from the website, which goes straight to the owner. We don't have an email address yet. We're also on ${a(INSTAGRAM, 'Instagram', true)}.`,
  },
  {
    id: 'pets',
    q: 'Can I bring my dog?',
    html: "Yes. Sasha's Corner is pet friendly, so well-behaved dogs are welcome inside.",
  },
  {
    id: 'seats',
    q: 'How big is the café?',
    html: `There are 24 seats: tables along the wall, round tables in the middle and a chess table by the pillar. Parties of up to ${info?.booking.max_party ?? 8} can ${a('/book', 'book online')}; for bigger groups, ${a('/visit#contact', 'send us a message')}.`,
  },
  {
    id: 'not-listed',
    q: 'Wifi, step-free access, gluten-free?',
    html: `We haven't put these on the website yet, so please ask before you come: call ${phone || 'us'} or ${a('/visit#contact', 'send a message')}.`,
    pageOnly: true,
    ownerNote: 'wifi, wheelchair / step-free access, toilets, gluten-free and vegan options: confirm each, then give it its own answer',
  },
];

/** HTML answer to plain text for JSON-LD and llms files. */
export const plain = (html: string) =>
  html
    .replace(/<a [^>]*href="([^"]+)"[^>]*>([^<]*)<\/a>/g, '$2')
    .replace(/<[^>]+>/g, '')
    .replace(/\s+/g, ' ')
    .trim();

/** Same, but keeping links as markdown for llms.txt. */
export const markdown = (html: string, site: URL) =>
  html
    .replace(/<a [^>]*href="([^"]+)"[^>]*>([^<]*)<\/a>/g, (_, href: string, text: string) =>
      href.startsWith('tel:') ? text : `[${text}](${new URL(href, site).href})`,
    )
    .replace(/<[^>]+>/g, '')
    .replace(/\s+/g, ' ')
    .trim();
