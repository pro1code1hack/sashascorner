// The logo's single line as a static SVG, used by Slot.astro's empty-slot
// placeholder as a CSS mask (one cached file instead of the path inlined into every
// placeholder). Same trace as LineDraw and the diorama wire: src/data/logo-line.json.
import line from '../data/logo-line.json';

type Edge = { pts: number[][] };

export function GET() {
  const [x0, y0, x1, y1] = line.bbox as number[];
  const pad = 8;
  const r = (n: number) => Math.round(n * 10) / 10;
  const d = (line.edges as Edge[])
    .map((e) => {
      // every other point is plenty at placeholder sizes; keep both ends
      const pts = e.pts.filter((_, i) => i % 2 === 0 || i === e.pts.length - 1);
      return 'M' + pts.map(([x, y]) => `${r(x)} ${r(y)}`).join('L');
    })
    .join('');
  const svg =
    `<svg xmlns="http://www.w3.org/2000/svg" viewBox="${x0 - pad} ${y0 - pad} ${x1 - x0 + 2 * pad} ${y1 - y0 + 2 * pad}">` +
    `<path d="${d}" fill="none" stroke="#000" stroke-width="3" stroke-linecap="round" stroke-linejoin="round"/></svg>`;
  return new Response(svg, { headers: { 'Content-Type': 'image/svg+xml' } });
}
