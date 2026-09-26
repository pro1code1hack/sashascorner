// /llms-full.txt: the llms.txt card plus the full menu and every FAQ answer, as
// markdown. Built at build time from menu.json / info.json; nothing hand-written
// here that isn't a verified fact (site/BRIEF.md).
import type { APIRoute } from 'astro';
import { menu } from '../lib/data';
import { faqs, markdown, priceList } from './_facts';
import { GET as card } from './llms.txt';

export const GET: APIRoute = async (ctx) => {
  const site = ctx.site!;
  const head = await (await card(ctx)).text();
  const out = [head.replace(/\n## Optional[\s\S]*$/, '').trimEnd(), '', '## Questions and answers', ''];
  for (const f of faqs.filter((x) => !x.pageOnly)) {
    out.push(`### ${f.q}`, '', markdown(f.html, site), '');
  }
  out.push('## Menu', '', `Prices in GBP. Source: ${new URL('/menu', site).href}${menu.generated_at ? ` (exported ${menu.generated_at.slice(0, 10)})` : ''}.`, '');
  for (const c of menu.categories) {
    out.push(`### ${c.name}`, '', ...(c.blurb ? [c.blurb, ''] : []));
    for (const i of c.items) {
      const extra = [i.description, i.note, i.seasonal ? `seasonal: ${i.seasonal.name}` : null].filter(Boolean).join('; ');
      out.push(`- ${i.name}: ${priceList(i)}${extra ? ` (${extra})` : ''}`);
    }
    out.push('');
  }
  if (menu.extras?.items.length) {
    out.push(`### ${menu.extras.title}`, '', ...menu.extras.items.map((e) => `- ${e.name}: ${e.price_text}`), '');
  }
  return new Response(out.join('\n'), { headers: { 'Content-Type': 'text/plain; charset=utf-8' } });
};
