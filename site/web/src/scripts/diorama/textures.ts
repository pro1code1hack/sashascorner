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
  const [c, g] = canvas(W, H);
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
