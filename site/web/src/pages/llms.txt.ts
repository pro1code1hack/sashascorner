// /llms.txt (llmstxt.org): a short, factual card for language models, built from the
// same data as the pages. Google says it ignores this file; other engines may read it.
// Only verified facts and menu data go in here (site/BRIEF.md).
import type { APIRoute } from 'astro';
import { menu } from '../lib/data';
import { gbp } from '../lib/format';
import { address, DELIVEROO, faqs, from, highlights as h, hoursText, INSTAGRAM, itemCount, JUST_EAT, phone } from './_facts';

export const GET: APIRoute = ({ site }) => {
  const u = (p: string) => new URL(p, site).href;
  const lines = [
    "# Sasha's Corner",
    '',
    `> Independent café in Dundee city centre, Scotland, at ${address}. Open since November 2025. ${h.drinkCount} drinks on the board (coffee, matcha and blue matcha whisked to order, Raff coffee, bubble tea, milkshakes, iced teas), plus breakfast, lunch, brunch waffles and cakes including Kyiv cake. Dine in, takeaway, and no-contact delivery via Deliveroo and Just Eat.`,
    '',
    `- Hours: ${hoursText}.`,
    ...(phone ? [`- Phone: ${phone} (+44 ${phone.replace(/^0/, '')}). No email address; use the contact form.`] : []),
    `- Table booking online, walk-ins welcome.`,
    `- Google rating 4.9 from 54 reviews on its Google Business Profile (September 2026). Profile attributes: women-owned, LGBTQ+ friendly.`,
    `- Menu: ${itemCount} items across ${menu.categories.length} categories.`,
    ...(h.espresso ? [`- Prices from: espresso ${gbp(Math.min(...h.espresso.sizes.map((z) => z.price_pence)))}, green matcha ${from(h.greenMatcha)}, blue matcha ${from(h.blueMatcha)}, bubble tea ${from(h.bubble)}, brunch waffles ${h.wafflesFrom}, cakes ${h.cakesFrom}.`] : []),
    ...(h.kyivSlice && h.kyivWhole ? [`- Kyiv cake: ${gbp(h.kyivSlice.sizes[0].price_pence)} a slice, ${gbp(h.kyivWhole.sizes[0].price_pence)} whole (order ahead).`] : []),
    '- Pet friendly: well-behaved dogs are welcome inside.',
    '- Not stated on the site (ask the café): wifi, wheelchair access, parking, gluten-free or vegan options, delivery radius.',
    '',
    '## Pages',
    '',
    `- [Menu with prices](${u('/menu')}): every drink and dish on the boards, with sizes and prices`,
    `- [FAQ](${u('/faq')}): hours, booking, delivery, matcha, Raff coffee, Kyiv cake, milks, prices`,
    `- [Visit](${u('/visit')}): address, opening hours, directions, contact form`,
    `- [Book a table](${u('/book')}): online table booking`,
    `- [Order online](${u('/order')}): order ahead for takeaway, collected from 23 Commercial Street; pay online or at the counter`,
    `- [Cakes & delivery](${u('/delivery')}): whole Kyiv cake to order, takeaway, Deliveroo and Just Eat`,
    `- [About](${u('/about')}): the room, photos, what reviewers mention`,
    '',
    '## Elsewhere',
    '',
    `- [Instagram](${INSTAGRAM})`,
    `- [Deliveroo](${DELIVEROO})`,
    `- [Just Eat](${JUST_EAT})`,
    '',
    '## Optional',
    '',
    `- [Full text for language models](${u('/llms-full.txt')}): the whole menu with prices and every FAQ answer (${faqs.filter((f) => !f.pageOnly).length} questions)`,
    '',
  ];
  return new Response(lines.join('\n'), { headers: { 'Content-Type': 'text/plain; charset=utf-8' } });
};
