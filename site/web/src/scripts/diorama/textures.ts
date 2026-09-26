// Procedural textures, drawn on canvas at load time so the diorama ships no image
// files beyond the few photos in its picture frames.
import * as THREE from 'three';

export const C = {
  ink: '#0d0d0b',
  olive900: '#474531',
  olive700: '#666749',
  sage: '#899c6a',
  coffee: '#9b6038',
  caramel: '#d4884e',
  paper: '#f3f1eb',
  oat: '#ebe6dd',
  // Materials that are not brand colours but have to exist in a room.
  oak: '#d7b38a',
  oakDark: '#b98c60',
  leaf: '#4b5a36',
  chrome: '#c9c7c0',
  white: '#f1efe8',
  darkWood: '#3d2b20',
} as const;

function canvas(w: number, h: number): [HTMLCanvasElement, CanvasRenderingContext2D] {
  const c = document.createElement('canvas');
  c.width = w;
  c.height = h;
  return [c, c.getContext('2d')!];
}

function tex(c: HTMLCanvasElement, repeat?: [number, number]): THREE.CanvasTexture {
  const t = new THREE.CanvasTexture(c);
  t.colorSpace = THREE.SRGBColorSpace;
  t.anisotropy = 4;
  if (repeat) {
    t.wrapS = t.wrapT = THREE.RepeatWrapping;
    t.repeat.set(...repeat);
  }
  return t;
}

// Deterministic noise so every visitor sees the same floor.
function rng(seed: number) {
  return () => {
    seed = (seed * 16807) % 2147483647;
    return (seed - 1) / 2147483646;
  };
}

export function oakFloor(): THREE.CanvasTexture {
  const [c, g] = canvas(1024, 1024);
  const r = rng(7);
  const rows = 8;
  const h = c.height / rows;
  for (let i = 0; i < rows; i++) {
    let x = -r() * 400;
    while (x < c.width) {
      const w = 260 + r() * 260;
      const tone = 0.95 + r() * 0.08;
      g.fillStyle = shade(C.oak, tone);
      g.fillRect(x, i * h, w, h);
      // grain
      g.globalAlpha = 0.13;
      for (let k = 0; k < 7; k++) {
        g.strokeStyle = shade(C.oakDark, 0.85 + r() * 0.2);
        g.lineWidth = 1 + r() * 1.5;
        g.beginPath();
        const y = i * h + 6 + r() * (h - 12);
        g.moveTo(x, y);
        g.bezierCurveTo(x + w * 0.3, y + (r() - 0.5) * 8, x + w * 0.7, y + (r() - 0.5) * 8, x + w, y);
        g.stroke();
      }
      g.globalAlpha = 1;
      g.fillStyle = 'rgba(70,50,30,.35)';
      g.fillRect(x, i * h, 2, h);
      x += w;
    }
    g.fillStyle = 'rgba(70,50,30,.3)';
    g.fillRect(0, i * h, c.width, 2);
  }
  return tex(c, [2, 2]);
}

export function plaster(base: string): THREE.CanvasTexture {
  const [c, g] = canvas(512, 512);
  const r = rng(3);
  g.fillStyle = base;
  g.fillRect(0, 0, 512, 512);
  for (let i = 0; i < 2600; i++) {
    g.fillStyle = r() > 0.5 ? 'rgba(255,255,255,.05)' : 'rgba(60,50,30,.035)';
    const s = 1 + r() * 3;
    g.fillRect(r() * 512, r() * 512, s, s);
  }
  return tex(c, [3, 2]);
}

/** A TV menu screen, as over the real counter, carrying real items and prices. */
export function menuScreen(title: string, rows: Array<[string, string]>): THREE.CanvasTexture {
  const W = 800;
  const H = 450;
  // Drawn at 2.5x: the boards are big on the wall now (owner, 2026-09-26).
  const K = 2.5;
  const [c, g] = canvas(W * K, H * K);
  g.scale(K, K);
  g.fillStyle = '#1b1b15';
  g.fillRect(0, 0, W, H);
  g.fillStyle = C.olive900;
  g.fillRect(0, 0, W, 86);
  g.fillStyle = C.paper;
  g.textBaseline = 'alphabetic';
  g.font = '600 44px "Saira Variable", Saira, sans-serif';
  g.fillText(title, 40, 60);
  g.fillStyle = C.caramel;
  g.beginPath();
  g.arc(W - 60, 43, 14, 0, Math.PI * 2);
  g.fill();
  g.font = '400 30px "Jost Variable", Jost, sans-serif';
  rows.slice(0, 6).forEach(([name, price], i) => {
    const y = 138 + i * 52;
    g.fillStyle = C.paper;
    const label = name.length > 26 ? `${name.slice(0, 25)}…` : name;
    g.fillText(label, 40, y);
    g.fillStyle = C.caramel;
    g.fillText(price, W - 40 - g.measureText(price).width, y);
  });
  return tex(c);
}

export function openSign(): THREE.CanvasTexture {
  const [c, g] = canvas(256, 128);
  g.fillStyle = C.paper;
  g.fillRect(0, 0, 256, 128);
  g.fillStyle = C.olive900;
  g.font = '600 64px "Saira Variable", Saira, sans-serif';
  g.textAlign = 'center';
  g.fillText('open', 128, 86);
  return tex(c);
}

/** Soft radial shadow under the whole diorama, so it sits rather than floats. */
export function contactShadow(): THREE.CanvasTexture {
  const [c, g] = canvas(256, 256);
  const grd = g.createRadialGradient(128, 128, 10, 128, 128, 128);
  grd.addColorStop(0, 'rgba(10,10,6,.55)');
  grd.addColorStop(0.55, 'rgba(10,10,6,.22)');
  grd.addColorStop(1, 'rgba(10,10,6,0)');
  g.fillStyle = grd;
  g.fillRect(0, 0, 256, 256);
  return tex(c);
}

export function windowSky(): THREE.CanvasTexture {
  const [c, g] = canvas(64, 256);
  const grd = g.createLinearGradient(0, 0, 0, 256);
  grd.addColorStop(0, '#f6efe1');
  grd.addColorStop(0.6, '#e9dcc3');
  grd.addColorStop(1, '#b9b691');
  g.fillStyle = grd;
  g.fillRect(0, 0, 64, 256);
  return tex(c);
}

/** Rasterise an SVG (the logo) onto a coloured card, for the kraft bag decal. */
export async function svgCard(url: string, bg: string, w = 512, h = 512): Promise<THREE.CanvasTexture> {
  const [c, g] = canvas(w, h);
  g.fillStyle = bg;
  g.fillRect(0, 0, w, h);
  const img = new Image();
  img.src = url;
  await img.decode().catch(() => undefined);
  if (img.naturalWidth) {
    const s = (w * 0.78) / img.naturalWidth;
    g.drawImage(img, (w - img.naturalWidth * s) / 2, h * 0.3, img.naturalWidth * s, img.naturalHeight * s);
  }
  return tex(c);
}

function shade(hex: string, k: number): string {
  const n = parseInt(hex.slice(1), 16);
  const f = (v: number) => Math.max(0, Math.min(255, Math.round(v * k)));
  return `rgb(${f(n >> 16)},${f((n >> 8) & 255)},${f(n & 255)})`;
}

export function puff(): THREE.CanvasTexture {
  const [c, g] = canvas(128, 128);
  const grd = g.createRadialGradient(64, 64, 4, 64, 64, 64);
  grd.addColorStop(0, 'rgba(255,255,255,.9)');
  grd.addColorStop(1, 'rgba(255,255,255,0)');
  g.fillStyle = grd;
  g.fillRect(0, 0, 128, 128);
  return tex(c);
}

/** Large grey marble-effect floor tiles, as laid in the café. */
export function marbleTiles(): THREE.CanvasTexture {
  const S = 1024;
  const [c, g] = canvas(S, S);
  const r = rng(19);
  const n = 4;
  const t = S / n;
  for (let i = 0; i < n; i++) {
    for (let j = 0; j < n; j++) {
      const k = 0.94 + r() * 0.08;
      g.fillStyle = `rgb(${Math.round(196 * k)},${Math.round(196 * k)},${Math.round(192 * k)})`;
      g.fillRect(i * t, j * t, t, t);
      // veins
      g.save();
      g.beginPath();
      g.rect(i * t, j * t, t, t);
      g.clip();
      for (let v = 0; v < 5; v++) {
        g.strokeStyle = r() > 0.5 ? 'rgba(255,255,255,.35)' : 'rgba(90,90,88,.18)';
        g.lineWidth = 0.6 + r() * 1.6;
        g.beginPath();
        let x = i * t + r() * t;
        let y = j * t;
        g.moveTo(x, y);
        for (let s = 0; s < 8; s++) {
          x += (r() - 0.5) * 50;
          y += t / 7;
          g.lineTo(x, y);
        }
        g.stroke();
      }
      g.restore();
      g.fillStyle = 'rgba(120,120,116,.55)';
      g.fillRect(i * t, j * t, t, 2);
      g.fillRect(i * t, j * t, 2, t);
    }
  }
  return tex(c, [3, 2.4]);
}

/** Dark stained tongue-and-groove panelling for the lower walls. */
export function woodPanel(): THREE.CanvasTexture {
  const [c, g] = canvas(512, 256);
  const r = rng(5);
  const boards = 10;
  const w = 512 / boards;
  for (let i = 0; i < boards; i++) {
    const k = 0.9 + r() * 0.18;
    g.fillStyle = `rgb(${Math.round(64 * k)},${Math.round(44 * k)},${Math.round(33 * k)})`;
    g.fillRect(i * w, 0, w, 256);
    g.globalAlpha = 0.25;
    for (let s = 0; s < 6; s++) {
      g.strokeStyle = 'rgb(40,26,18)';
      g.beginPath();
      const x = i * w + r() * w;
      g.moveTo(x, 0);
      g.bezierCurveTo(x + 4, 80, x - 4, 170, x + 2, 256);
      g.stroke();
    }
    g.globalAlpha = 1;
    g.fillStyle = 'rgba(20,12,8,.7)';
    g.fillRect(i * w, 0, 2, 256);
  }
  return tex(c, [6, 1]);
}

export function chessboard(): THREE.CanvasTexture {
  const [c, g] = canvas(256, 256);
  for (let i = 0; i < 8; i++)
    for (let j = 0; j < 8; j++) {
      g.fillStyle = (i + j) % 2 ? '#5a3a24' : '#e8d3ad';
      g.fillRect(i * 32, j * 32, 32, 32);
    }
  return tex(c);
}

/** A pinned "Prices" chalkboard, as on the side of the drinks fridge. */
export function pricesBoard(): THREE.CanvasTexture {
  const [c, g] = canvas(256, 512);
  g.fillStyle = '#1c1c19';
  g.fillRect(0, 0, 256, 512);
  g.fillStyle = 'rgba(243,241,235,.9)';
  g.font = '600 44px "Saira Variable", Saira, sans-serif';
  g.fillText('Prices', 36, 70);
  g.font = '400 24px "Jost Variable", Jost, sans-serif';
  const rows = ['Paninis  3.80', 'Rolls  3.80', 'Toasties', 'Cans', 'Juices', 'Still / sparkling'];
  rows.forEach((t, i) => g.fillText(t, 30, 130 + i * 52));
  return tex(c);
}

/** The white tree painted on the café wall, as a transparent decal. */
export function treeMural(): THREE.CanvasTexture {
  const W = 512;
  const H = 640;
  const [c, g] = canvas(W, H);
  const r = rng(23);
  g.strokeStyle = 'rgba(247,245,238,.95)';
  g.fillStyle = 'rgba(247,245,238,.95)';
  g.lineCap = 'round';
  const branch = (x: number, y: number, len: number, ang: number, w: number, depth: number) => {
    const x2 = x + Math.cos(ang) * len;
    const y2 = y - Math.sin(ang) * len;
    g.lineWidth = w;
    g.beginPath();
    g.moveTo(x, y);
    g.quadraticCurveTo((x + x2) / 2 + (r() - 0.5) * 20, (y + y2) / 2, x2, y2);
    g.stroke();
    if (depth === 0) {
      for (let k = 0; k < 4; k++) {
        g.beginPath();
        g.arc(x2 + (r() - 0.5) * 26, y2 + (r() - 0.5) * 26, 3 + r() * 4, 0, Math.PI * 2);
        g.fill();
      }
      return;
    }
    branch(x2, y2, len * (0.66 + r() * 0.1), ang + 0.35 + r() * 0.3, w * 0.66, depth - 1);
    branch(x2, y2, len * (0.62 + r() * 0.1), ang - 0.4 - r() * 0.3, w * 0.66, depth - 1);
    if (r() > 0.5) branch(x2, y2, len * 0.5, ang + (r() - 0.5) * 0.4, w * 0.5, depth - 1);
  };
  branch(W * 0.42, H, 190, Math.PI / 2 - 0.08, 26, 6);
  const t = tex(c);
  return t;
}

/** A pavement-style A-frame chalkboard, like the one inside the front window. */
export function aFrameBoard(): THREE.CanvasTexture {
  const [c, g] = canvas(256, 384);
  g.fillStyle = '#1c1c19';
  g.fillRect(0, 0, 256, 384);
  g.fillStyle = 'rgba(214,226,170,.9)';
  g.font = '400 30px "Jost Variable", Jost, sans-serif';
  ['Hot soup ......', 'Paninis ......', 'Brunch ......', 'Waffles ......', 'Matcha ......'].forEach((t, i) =>
    g.fillText(t, 22, 70 + i * 62),
  );
  return tex(c);
}

// ---- 2026-09-26: from the owner's photos of the right-hand side ---------------------

const CHALK = 'rgba(247,245,238,.96)';

/** Thicken chalk line art by stamping the canvas onto itself round a small circle. */
function bold(c: HTMLCanvasElement, r: number): HTMLCanvasElement {
  const [o, g] = canvas(c.width, c.height);
  for (let k = 0; k < 12; k++) {
    const a = (k / 12) * Math.PI * 2;
    g.drawImage(c, Math.cos(a) * r, Math.sin(a) * r);
  }
  g.drawImage(c, 0, 0);
  return o;
}


/** Leafy blossom sprays: a twig with alternating leaves and five-petal flowers. */
function spray(g: CanvasRenderingContext2D, r: () => number, x: number, y: number, len: number, ang: number, droop: number) {
  g.strokeStyle = CHALK;
  g.fillStyle = CHALK;
  g.lineWidth = 2.2;
  const pts: Array<[number, number, number]> = [];
  g.beginPath();
  g.moveTo(x, y);
  const n = 10;
  let px = x;
  let py = y;
  for (let i = 1; i <= n; i++) {
    const t = i / n;
    const a = ang + droop * t;
    px += Math.cos(a) * (len / n);
    py -= Math.sin(a) * (len / n);
    g.lineTo(px, py);
    pts.push([px, py, a]);
  }
  g.stroke();
  pts.forEach(([lx, ly, a], i) => {
    if (i % 3 === 2 && r() > 0.35) {
      flower(g, lx + (r() - 0.5) * 10, ly + (r() - 0.5) * 10, 5 + r() * 4);
      return;
    }
    const s = i % 2 ? 1 : -1;
    const la = a + s * (0.9 + r() * 0.4);
    g.save();
    g.translate(lx, ly);
    g.rotate(-la);
    g.beginPath();
    g.ellipse(9, 0, 9, 3.6, 0, 0, Math.PI * 2);
    g.fill();
    g.restore();
  });
}

function flower(g: CanvasRenderingContext2D, x: number, y: number, s: number) {
  g.fillStyle = CHALK;
  for (let k = 0; k < 5; k++) {
    const a = (k / 5) * Math.PI * 2;
    g.beginPath();
    g.arc(x + Math.cos(a) * s * 0.6, y + Math.sin(a) * s * 0.6, s * 0.5, 0, Math.PI * 2);
    g.fill();
  }
}

function catSitting(g: CanvasRenderingContext2D, x: number, y: number, s: number) {
  // A white cat seen from behind, sitting, ears up, tail curled round.
  g.fillStyle = CHALK;
  g.beginPath();
  g.ellipse(x, y - s * 0.55, s * 0.42, s * 0.6, 0, 0, Math.PI * 2); // body
  g.fill();
  g.beginPath();
  g.arc(x, y - s * 1.28, s * 0.27, 0, Math.PI * 2); // head
  g.fill();
  for (const d of [-1, 1]) {
    g.beginPath();
    g.moveTo(x + d * s * 0.26, y - s * 1.3);
    g.lineTo(x + d * s * 0.2, y - s * 1.68);
    g.lineTo(x + d * s * 0.05, y - s * 1.45);
    g.fill();
  }
  g.strokeStyle = CHALK;
  g.lineWidth = s * 0.12;
  g.lineCap = 'round';
  g.beginPath();
  g.moveTo(x + s * 0.3, y - s * 0.05);
  g.quadraticCurveTo(x + s * 0.8, y, x + s * 0.55, y - s * 0.4);
  g.stroke();
}

function pine(g: CanvasRenderingContext2D, x: number, y: number, h: number) {
  g.fillStyle = CHALK;
  g.strokeStyle = CHALK;
  g.lineWidth = 2;
  g.beginPath();
  g.moveTo(x, y);
  g.lineTo(x, y - h);
  g.stroke();
  const tiers = 5;
  for (let i = 0; i < tiers; i++) {
    const ty = y - h * (0.2 + (i / tiers) * 0.8);
    const w = h * 0.28 * (1 - i / tiers);
    g.beginPath();
    g.moveTo(x - w, ty);
    g.lineTo(x, ty - h * 0.24);
    g.lineTo(x + w, ty);
    g.closePath();
    g.globalAlpha = 0.9;
    g.fill();
    g.globalAlpha = 1;
  }
}

/**
 * The entrance-wall mural: mountains over a lake with a sailboat, pines, birds,
 * two white cats on the shore, and the blossom tree leaning in from the door end.
 * Transparent, so the sage plaster shows through; left edge is the room end.
 */
export function landscapeMural(): THREE.CanvasTexture {
  const W = 1600;
  const H = 700;
  const [c, g] = canvas(W, H);
  const r = rng(41);
  g.lineCap = 'round';
  g.lineJoin = 'round';
  const shore = H * 0.62;
  // mountains: filled white peaks with sage hatching for the shadow side
  const peaks: Array<[number, number, number]> = [
    [W * 0.5, H * 0.3, 260],
    [W * 0.64, H * 0.38, 200],
    [W * 0.71, H * 0.36, 170],
    [W * 0.38, H * 0.44, 150],
  ];
  for (const [px, py, w] of peaks) {
    g.fillStyle = CHALK;
    g.beginPath();
    g.moveTo(px - w, shore - 20);
    g.lineTo(px - w * 0.2, py + 30);
    g.lineTo(px, py);
    g.lineTo(px + w * 0.25, py + 40);
    g.lineTo(px + w, shore - 20);
    g.closePath();
    g.fill();
    g.strokeStyle = 'rgba(137,156,106,.9)';
    g.lineWidth = 3;
    for (let k = 0; k < 9; k++) {
      const t = k / 9;
      g.beginPath();
      g.moveTo(px + 6 + t * w * 0.2, py + 20 + t * 80);
      g.lineTo(px + 30 + t * w * 0.6, shore - 30 - (1 - t) * 20);
      g.stroke();
    }
  }
  // lake: horizontal chalk strokes
  g.strokeStyle = CHALK;
  for (let i = 0; i < 70; i++) {
    const y = shore + 6 + r() * (H * 0.3);
    const x = W * 0.1 + r() * W * 0.72;
    g.lineWidth = 1.5 + r() * 1.5;
    g.beginPath();
    g.moveTo(x, y);
    g.lineTo(x + 20 + r() * 90, y);
    g.stroke();
  }
  // sailboat
  g.fillStyle = CHALK;
  g.beginPath();
  g.moveTo(W * 0.5, shore + 30);
  g.lineTo(W * 0.5, shore - 20);
  g.lineTo(W * 0.515, shore + 22);
  g.closePath();
  g.fill();
  g.fillRect(W * 0.49, shore + 30, 34, 5);
  // pines along both shores
  for (let i = 0; i < 14; i++) pine(g, W * 0.06 + i * 26 + r() * 14, shore + 10 - r() * 20, 50 + r() * 110);
  for (let i = 0; i < 10; i++) pine(g, W * 0.7 + i * 22 + r() * 10, shore - r() * 10, 40 + r() * 70);
  // shrubs and grasses on the near shore
  for (let i = 0; i < 26; i++) {
    const x = r() * W * 0.85;
    const y = H - 20 - r() * 40;
    for (let k = 0; k < 5; k++) {
      g.lineWidth = 2;
      g.beginPath();
      g.moveTo(x, y);
      g.quadraticCurveTo(x + (k - 2) * 6, y - 18, x + (k - 2) * 12, y - 30 - r() * 20);
      g.stroke();
    }
  }
  // birds
  g.lineWidth = 3;
  for (const [bx, by] of [
    [W * 0.3, H * 0.18],
    [W * 0.34, H * 0.24],
    [W * 0.6, H * 0.16],
    [W * 0.24, H * 0.3],
  ]) {
    g.beginPath();
    g.moveTo(bx - 18, by);
    g.quadraticCurveTo(bx - 8, by - 10, bx, by);
    g.quadraticCurveTo(bx + 8, by - 10, bx + 18, by);
    g.stroke();
  }
  // two cats on the shore
  catSitting(g, W * 0.58, H - 30, 70);
  catSitting(g, W * 0.72, shore - 10, 38);
  // the blossom tree leaning in from the door end, over the whole scene
  g.strokeStyle = CHALK;
  g.lineWidth = 34;
  g.beginPath();
  g.moveTo(W - 40, H);
  g.bezierCurveTo(W - 60, H * 0.6, W - 70, H * 0.3, W - 190, H * 0.08);
  g.stroke();
  g.lineWidth = 16;
  g.beginPath();
  g.moveTo(W - 90, H * 0.35);
  g.quadraticCurveTo(W - 300, H * 0.12, W * 0.55, H * 0.04);
  g.stroke();
  for (let i = 0; i < 16; i++) spray(g, r, W - 120 - i * 45, H * 0.05 + r() * 40, 90 + r() * 70, -Math.PI / 2 - 0.4 + r() * 0.8, (r() - 0.5) * 1.2);
  for (let i = 0; i < 6; i++) spray(g, r, W - 70, H * 0.2 + i * 50, 80 + r() * 50, Math.PI + (r() - 0.3) * 0.8, 0.5);
  // falling petals
  for (let i = 0; i < 18; i++) {
    g.save();
    g.translate(W * 0.2 + r() * W * 0.7, H * 0.1 + r() * H * 0.35);
    g.rotate(r() * 3);
    g.beginPath();
    g.ellipse(0, 0, 7, 3, 0, 0, Math.PI * 2);
    g.fill();
    g.restore();
  }
  return tex(bold(c, 3));
}

/**
 * The corner tree by the counter: the trunk rises at the left edge (the corner)
 * and one long bough runs right along the wall, blossoming as it goes.
 */
export function cornerTree(): THREE.CanvasTexture {
  const W = 1400;
  const H = 640;
  const [c, g] = canvas(W, H);
  const r = rng(29);
  g.strokeStyle = CHALK;
  g.fillStyle = CHALK;
  g.lineCap = 'round';
  // trunk: a few twisting strands, like the hand-painted bark
  for (let k = 0; k < 4; k++) {
    g.lineWidth = 16 - k * 2;
    g.beginPath();
    g.moveTo(40 + k * 10, H);
    g.bezierCurveTo(70 + k * 6, H * 0.7, 20 + k * 12, H * 0.4, 90 + k * 8, H * 0.12);
    g.stroke();
  }
  // main bough to the right, a second one higher, and small branches
  g.lineWidth = 18;
  g.beginPath();
  g.moveTo(90, H * 0.14);
  g.bezierCurveTo(300, H * 0.02, 600, H * 0.16, 950, H * 0.1);
  g.stroke();
  g.lineWidth = 10;
  g.beginPath();
  g.moveTo(80, H * 0.3);
  g.quadraticCurveTo(250, H * 0.3, 420, H * 0.22);
  g.stroke();
  for (let i = 0; i < 20; i++) {
    const x = 120 + i * 45 + r() * 20;
    spray(g, r, x, H * 0.12 + r() * 30, 70 + r() * 70, (r() > 0.5 ? -1 : 1) * (0.6 + r() * 0.6), (r() - 0.5) * 0.8);
  }
  for (let i = 0; i < 5; i++) spray(g, r, 70, H * 0.2 + i * 40, 60 + r() * 40, Math.PI - 0.3, -0.4);
  for (let i = 0; i < 10; i++) {
    g.save();
    g.translate(60 + r() * 500, H * 0.35 + r() * H * 0.5);
    g.rotate(r() * 3);
    g.beginPath();
    g.ellipse(0, 0, 7, 3, 0, 0, Math.PI * 2);
    g.fill();
    g.restore();
  }
  return tex(bold(c, 3));
}

/** The white cat walking along the dado rail, tail up, looking at a flower. */
export function walkingCat(): THREE.CanvasTexture {
  const [c, g] = canvas(256, 200);
  g.fillStyle = CHALK;
  g.strokeStyle = CHALK;
  g.lineCap = 'round';
  g.beginPath();
  g.ellipse(120, 120, 62, 30, 0, 0, Math.PI * 2);
  g.fill();
  g.beginPath();
  g.arc(188, 90, 24, 0, Math.PI * 2);
  g.fill();
  for (const dx of [-8, 10]) {
    g.beginPath();
    g.moveTo(180 + dx, 72);
    g.lineTo(186 + dx, 50);
    g.lineTo(196 + dx, 72);
    g.fill();
  }
  g.lineWidth = 12;
  for (const x of [80, 100, 145, 162]) {
    g.beginPath();
    g.moveTo(x, 130);
    g.lineTo(x + (x % 3) * 2, 196);
    g.stroke();
  }
  g.lineWidth = 10;
  g.beginPath();
  g.moveTo(62, 110);
  g.bezierCurveTo(30, 90, 40, 40, 20, 20);
  g.stroke();
  flower(g, 236, 36, 10);
  return tex(c);
}

/** The painted toilet corridor: green and white triangles. */
export function geoDoorway(): THREE.CanvasTexture {
  const [c, g] = canvas(256, 512);
  g.fillStyle = '#f1efe8';
  g.fillRect(0, 0, 256, 512);
  g.fillStyle = '#3f7a4e';
  const tri = (a: [number, number], b: [number, number], d: [number, number]) => {
    g.beginPath();
    g.moveTo(...a);
    g.lineTo(...b);
    g.lineTo(...d);
    g.closePath();
    g.fill();
  };
  tri([0, 0], [150, 0], [0, 260]);
  tri([256, 60], [256, 330], [120, 512]);
  tri([0, 330], [0, 512], [90, 512]);
  tri([180, 0], [256, 0], [256, 40]);
  g.fillStyle = 'rgba(0,0,0,.22)';
  g.fillRect(0, 0, 256, 512);
  return tex(c);
}

/** A small black sign with words on it: TOILET, a chalk price tag. */
export function label(text: string, w = 256, h = 64, bg = '#141412', fg = '#f1efe8'): THREE.CanvasTexture {
  const [c, g] = canvas(w, h);
  g.fillStyle = bg;
  g.fillRect(0, 0, w, h);
  g.fillStyle = fg;
  g.font = `600 ${Math.round(h * 0.55)}px "Saira Variable", Saira, sans-serif`;
  g.textAlign = 'center';
  g.textBaseline = 'middle';
  g.fillText(text, w / 2, h / 2 + 2);
  return tex(c);
}

/** The two cat posters: "More espresso / less depresso", "Before coffee / after coffee". */
export function catPoster(top: string, bottom: string, bg = '#e8dcc0'): THREE.CanvasTexture {
  const [c, g] = canvas(256, 340);
  g.fillStyle = bg;
  g.fillRect(0, 0, 256, 340);
  g.fillStyle = '#1b1712';
  g.font = '700 26px "Saira Variable", Saira, sans-serif';
  g.textAlign = 'center';
  g.fillText(top, 128, 40);
  g.fillText(bottom, 128, 322);
  // black cat with a cup
  g.beginPath();
  g.ellipse(128, 210, 62, 70, 0, 0, Math.PI * 2);
  g.fill();
  g.beginPath();
  g.arc(128, 125, 42, 0, Math.PI * 2);
  g.fill();
  for (const d of [-1, 1]) {
    g.beginPath();
    g.moveTo(128 + d * 38, 110);
    g.lineTo(128 + d * 34, 70);
    g.lineTo(128 + d * 12, 92);
    g.fill();
  }
  g.fillStyle = '#e6c34a';
  g.beginPath();
  g.arc(114, 122, 6, 0, Math.PI * 2);
  g.arc(142, 122, 6, 0, Math.PI * 2);
  g.fill();
  g.fillStyle = '#f3f1eb';
  g.fillRect(150, 200, 34, 36);
  return tex(c);
}

/** The green-apple painting by the counter. */
export function applePainting(): THREE.CanvasTexture {
  const [c, g] = canvas(256, 256);
  g.fillStyle = '#f4f2ec';
  g.fillRect(0, 0, 256, 256);
  g.fillStyle = '#7f9a3e';
  g.beginPath();
  g.arc(128, 110, 58, 0, Math.PI * 2);
  g.fill();
  g.fillStyle = '#a6b95a';
  g.beginPath();
  g.arc(110, 95, 22, 0, Math.PI * 2);
  g.fill();
  g.fillStyle = '#e9e4c4';
  g.beginPath();
  g.ellipse(80, 200, 40, 24, 0, 0, Math.PI * 2);
  g.ellipse(180, 205, 38, 22, 0, 0, Math.PI * 2);
  g.fill();
  g.fillStyle = '#4a3522';
  g.fillRect(126, 44, 5, 18);
  return tex(c);
}

/** A printed notice: the allergy sign. */
export function notice(title: string): THREE.CanvasTexture {
  const [c, g] = canvas(192, 256);
  g.fillStyle = '#fbfbf8';
  g.fillRect(0, 0, 192, 256);
  g.fillStyle = '#1b1712';
  g.font = '700 24px "Jost Variable", Jost, sans-serif';
  g.textAlign = 'center';
  title.split('\n').forEach((t, i) => g.fillText(t, 96, 44 + i * 28));
  g.fillStyle = 'rgba(0,0,0,.35)';
  for (let i = 0; i < 7; i++) g.fillRect(24, 140 + i * 14, 144 - (i % 3) * 20, 4);
  return tex(c);
}

/** What the glazed door looks out on: the sandstone tenements across the street. */
export function streetView(): THREE.CanvasTexture {
  const [c, g] = canvas(256, 512);
  g.fillStyle = '#d8cbb3';
  g.fillRect(0, 0, 256, 300);
  g.fillStyle = '#f4f1ea';
  for (let row = 0; row < 3; row++)
    for (let col = 0; col < 3; col++) {
      g.fillRect(20 + col * 80, 30 + row * 85, 44, 60);
      g.fillStyle = '#6b6f73';
      g.fillRect(24 + col * 80, 34 + row * 85, 36, 52);
      g.fillStyle = '#f4f1ea';
    }
  g.fillStyle = '#2f3a48';
  g.fillRect(0, 290, 256, 40); // shopfront awning
  g.fillStyle = '#8e9092';
  g.fillRect(0, 330, 256, 90); // road
  g.fillStyle = '#e6c34a';
  g.fillRect(0, 380, 256, 4);
  g.fillStyle = '#b7b3aa';
  g.fillRect(0, 420, 256, 92); // pavement
  return tex(c);
}

/** A small chalk counter sign. */
export function chalkSign(lines: string[]): THREE.CanvasTexture {
  const [c, g] = canvas(256, 192);
  g.fillStyle = '#1c1c19';
  g.fillRect(0, 0, 256, 192);
  g.fillStyle = 'rgba(243,241,235,.92)';
  g.font = '600 34px "Jost Variable", Jost, sans-serif';
  g.textAlign = 'center';
  lines.forEach((t, i) => g.fillText(t, 128, 70 + i * 50));
  return tex(c);
}
