// robots.txt follows the configured site origin, so a domain change can't leave
// the sitemap pointing somewhere else.
//
// Search and AI crawlers are named explicitly so the intent is on record: this is a
// local business that wants to be found and quoted. A crawler obeys only the group
// that names it, so every group repeats the same Disallow lines.
import type { APIRoute } from 'astro';

const NAMED = [
  // search engines
  'Googlebot',
  'Bingbot',
  'Applebot',
  'DuckDuckBot',
  // AI answer engines and their user-triggered fetchers
  'OAI-SearchBot',
  'ChatGPT-User',
  'GPTBot',
  'Claude-SearchBot',
  'Claude-User',
  'ClaudeBot',
  'PerplexityBot',
  'Perplexity-User',
  // opt-in tokens for AI use of the content (they fetch nothing themselves)
  'Google-Extended',
  'Applebot-Extended',
];
const RULES = ['Allow: /', 'Disallow: /book/manage', 'Disallow: /admin', 'Disallow: /staff', 'Disallow: /c/'];

export const GET: APIRoute = ({ site }) =>
  new Response(
    [
      ...NAMED.map((ua) => `User-agent: ${ua}`),
      ...RULES,
      '',
      'User-agent: *',
      ...RULES,
      '',
      `Sitemap: ${new URL('/sitemap-index.xml', site).href}`,
      '',
    ].join('\n'),
    { headers: { 'Content-Type': 'text/plain; charset=utf-8' } },
  );
