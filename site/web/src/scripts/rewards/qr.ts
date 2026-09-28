// A small, dependency-free QR Code encoder: byte mode, error correction level M,
// versions 1-10 (up to 213 bytes). Enough for the card payload
// `SC1:<uuid>:<8 hex>` (49 bytes, version 4) and a web-card link with its token
// (about 120 bytes, version 7).
//
// Follows ISO/IEC 18004 and the structure of Project Nayuki's reference encoder:
// build the bit stream, Reed-Solomon per block, interleave, place in the zigzag,
// then try all eight masks and keep the one with the lowest penalty.

// [data codewords per block, number of such blocks][] and EC codewords per block,
// for level M, index = version.
const BLOCKS_M: ReadonlyArray<{ groups: [number, number][]; ec: number }> = [
  { groups: [], ec: 0 },
  { groups: [[16, 1]], ec: 10 },
  { groups: [[28, 1]], ec: 16 },
  { groups: [[44, 1]], ec: 26 },
  { groups: [[32, 2]], ec: 18 },
  { groups: [[43, 2]], ec: 24 },
  { groups: [[27, 4]], ec: 16 },
  { groups: [[31, 4]], ec: 18 },
  { groups: [[38, 2], [39, 2]], ec: 22 },
  { groups: [[36, 3], [37, 2]], ec: 22 },
  { groups: [[43, 4], [44, 1]], ec: 26 },
];
const ALIGN: ReadonlyArray<number[]> = [
  [],
  [],
  [6, 18],
  [6, 22],
  [6, 26],
  [6, 30],
  [6, 34],
  [6, 22, 38],
  [6, 24, 42],
  [6, 26, 46],
  [6, 28, 50],
];
const MAX_VERSION = 10;

const dataCapacity = (v: number) => BLOCKS_M[v].groups.reduce((n, [len, count]) => n + len * count, 0);

// ---- GF(256) and Reed-Solomon ---------------------------------------------------
function gfMul(x: number, y: number): number {
  let z = 0;
  for (let i = 7; i >= 0; i--) {
    z = (z << 1) ^ ((z >>> 7) * 0x11d);
    z ^= ((y >>> i) & 1) * x;
  }
  return z & 0xff;
}
function rsDivisor(degree: number): number[] {
  const result = new Array<number>(degree).fill(0);
  result[degree - 1] = 1;
  let root = 1;
  for (let i = 0; i < degree; i++) {
    for (let j = 0; j < result.length; j++) {
      result[j] = gfMul(result[j], root);
      if (j + 1 < result.length) result[j] ^= result[j + 1];
    }
    root = gfMul(root, 0x02);
  }
  return result;
}
function rsRemainder(data: number[], divisor: number[]): number[] {
  const result = new Array<number>(divisor.length).fill(0);
  for (const b of data) {
    const factor = b ^ (result.shift() as number);
    result.push(0);
    divisor.forEach((coef, i) => (result[i] ^= gfMul(coef, factor)));
  }
  return result;
}

// ---- the encoder ------------------------------------------------------------------
export interface QrMatrix {
  size: number;
  /** modules[y][x], true = dark */
  modules: boolean[][];
}

export function encodeQr(text: string): QrMatrix {
  const bytes = Array.from(new TextEncoder().encode(text));
  let version = 1;
  for (; version <= MAX_VERSION; version++) {
    const countBits = version < 10 ? 8 : 16;
    if (4 + countBits + bytes.length * 8 <= dataCapacity(version) * 8) break;
  }
  if (version > MAX_VERSION) throw new RangeError('QR payload too long');

  // Bit stream: mode 0100 (byte), length, data, terminator, pad.
  const bits: number[] = [];
  const put = (val: number, len: number) => {
    for (let i = len - 1; i >= 0; i--) bits.push((val >>> i) & 1);
  };
  put(0b0100, 4);
  put(bytes.length, version < 10 ? 8 : 16);
  bytes.forEach((b) => put(b, 8));
  const capBits = dataCapacity(version) * 8;
  put(0, Math.min(4, capBits - bits.length));
  put(0, (8 - (bits.length % 8)) % 8);
  for (let pad = 0xec; bits.length < capBits; pad ^= 0xec ^ 0x11) put(pad, 8);
  const data: number[] = [];
  for (let i = 0; i < bits.length; i += 8) data.push(parseInt(bits.slice(i, i + 8).join(''), 2));

  // Split into blocks, add EC, interleave.
  const { groups, ec } = BLOCKS_M[version];
  const divisor = rsDivisor(ec);
  const blocks: { data: number[]; ec: number[] }[] = [];
  let k = 0;
  for (const [len, count] of groups) {
    for (let c = 0; c < count; c++) {
      const d = data.slice(k, k + len);
      k += len;
      blocks.push({ data: d, ec: rsRemainder(d, divisor) });
    }
  }
  const codewords: number[] = [];
  const maxLen = Math.max(...blocks.map((b) => b.data.length));
  for (let i = 0; i < maxLen; i++) for (const b of blocks) if (i < b.data.length) codewords.push(b.data[i]);
  for (let i = 0; i < ec; i++) for (const b of blocks) codewords.push(b.ec[i]);

  // Function patterns.
  const size = version * 4 + 17;
  const grid = () => Array.from({ length: size }, () => new Array<boolean>(size).fill(false));
  const modules = grid();
  const isFn = grid();
  const set = (x: number, y: number, dark: boolean) => {
    modules[y][x] = dark;
    isFn[y][x] = true;
  };
  for (let i = 0; i < size; i++) {
    set(6, i, i % 2 === 0);
    set(i, 6, i % 2 === 0);
  }
  for (const [cx, cy] of [
    [3, 3],
    [size - 4, 3],
    [3, size - 4],
  ]) {
    for (let dy = -4; dy <= 4; dy++)
      for (let dx = -4; dx <= 4; dx++) {
        const x = cx + dx;
        const y = cy + dy;
        if (x < 0 || y < 0 || x >= size || y >= size) continue;
        const dist = Math.max(Math.abs(dx), Math.abs(dy));
        set(x, y, dist !== 2 && dist !== 4);
      }
  }
  const pos = ALIGN[version];
  pos.forEach((ay, i) =>
    pos.forEach((ax, j) => {
      const last = pos.length - 1;
      if ((i === 0 && j === 0) || (i === 0 && j === last) || (i === last && j === 0)) return;
      for (let dy = -2; dy <= 2; dy++)
        for (let dx = -2; dx <= 2; dx++) set(ax + dx, ay + dy, Math.max(Math.abs(dx), Math.abs(dy)) !== 1);
    }),
  );
  const drawFormat = (mask: number) => {
    const d = (0b00 << 3) | mask; // level M = 00
    let rem = d;
    for (let i = 0; i < 10; i++) rem = (rem << 1) ^ ((rem >>> 9) * 0x537);
    const f = ((d << 10) | rem) ^ 0x5412;
    const bit = (i: number) => ((f >>> i) & 1) === 1;
    for (let i = 0; i <= 5; i++) set(8, i, bit(i));
    set(8, 7, bit(6));
    set(8, 8, bit(7));
    set(7, 8, bit(8));
    for (let i = 9; i < 15; i++) set(14 - i, 8, bit(i));
    for (let i = 0; i < 8; i++) set(size - 1 - i, 8, bit(i));
    for (let i = 8; i < 15; i++) set(8, size - 15 + i, bit(i));
    set(8, size - 8, true);
  };
  drawFormat(0); // reserve the area before placing data
  if (version >= 7) {
    let rem = version;
    for (let i = 0; i < 12; i++) rem = (rem << 1) ^ ((rem >>> 11) * 0x1f25);
    const v = (version << 12) | rem;
    for (let i = 0; i < 18; i++) {
      const dark = ((v >>> i) & 1) === 1;
      const a = size - 11 + (i % 3);
      const b = Math.floor(i / 3);
      set(a, b, dark);
      set(b, a, dark);
    }
  }

  // Data, in the two-column zigzag from the bottom right.
  let i = 0;
  for (let right = size - 1; right >= 1; right -= 2) {
    if (right === 6) right = 5;
    for (let vert = 0; vert < size; vert++) {
      for (let j = 0; j < 2; j++) {
        const x = right - j;
        const upward = ((right + 1) & 2) === 0;
        const y = upward ? size - 1 - vert : vert;
        if (!isFn[y][x] && i < codewords.length * 8) {
          modules[y][x] = ((codewords[i >>> 3] >>> (7 - (i & 7))) & 1) === 1;
          i++;
        }
      }
    }
  }

  // Masks: try each, keep the lowest penalty.
  const maskFn: ((x: number, y: number) => boolean)[] = [
    (x, y) => (x + y) % 2 === 0,
    (_x, y) => y % 2 === 0,
    (x) => x % 3 === 0,
    (x, y) => (x + y) % 3 === 0,
    (x, y) => (Math.floor(x / 3) + Math.floor(y / 2)) % 2 === 0,
    (x, y) => ((x * y) % 2) + ((x * y) % 3) === 0,
    (x, y) => (((x * y) % 2) + ((x * y) % 3)) % 2 === 0,
    (x, y) => (((x + y) % 2) + ((x * y) % 3)) % 2 === 0,
  ];
  const applyMask = (m: number) => {
    for (let y = 0; y < size; y++)
      for (let x = 0; x < size; x++) if (!isFn[y][x] && maskFn[m](x, y)) modules[y][x] = !modules[y][x];
  };
  let best = 0;
  let bestScore = Infinity;
  for (let m = 0; m < 8; m++) {
    applyMask(m);
    drawFormat(m);
    const s = penalty(modules, size);
    if (s < bestScore) {
      bestScore = s;
      best = m;
    }
    applyMask(m); // XOR again undoes it
  }
  applyMask(best);
  drawFormat(best);
  return { size, modules };
}

function penalty(m: boolean[][], size: number): number {
  let score = 0;
  const line = (get: (i: number) => boolean) => {
    // Rule 1: runs of five or more; rule 3: 1:1:3:1:1 finder look-alikes.
    let run = 1;
    for (let i = 1; i <= size; i++) {
      if (i < size && get(i) === get(i - 1)) run++;
      else {
        if (run >= 5) score += 3 + (run - 5);
        run = 1;
      }
    }
    const pat = [true, false, true, true, true, false, true];
    for (let i = 0; i + 7 <= size; i++) {
      if (!pat.every((p, k) => get(i + k) === p)) continue;
      const lightBefore = [1, 2, 3, 4].every((k) => i - k < 0 || !get(i - k));
      const lightAfter = [0, 1, 2, 3].every((k) => i + 7 + k >= size || !get(i + 7 + k));
      if (lightBefore || lightAfter) score += 40;
    }
  };
  for (let y = 0; y < size; y++) line((x) => m[y][x]);
  for (let x = 0; x < size; x++) line((y) => m[y][x]);
  let dark = 0;
  for (let y = 0; y < size; y++)
    for (let x = 0; x < size; x++) {
      if (m[y][x]) dark++;
      if (x < size - 1 && y < size - 1) {
        const c = m[y][x];
        if (c === m[y][x + 1] && c === m[y + 1][x] && c === m[y + 1][x + 1]) score += 3;
      }
    }
  score += Math.floor(Math.abs((dark * 100) / (size * size) - 50) / 5) * 10;
  return score;
}

/** An SVG string, one path, with the four-module quiet zone the standard asks for. */
export function qrSvg(text: string, opts: { label?: string; dark?: string; light?: string } = {}): string {
  const { size, modules } = encodeQr(text);
  const q = 4;
  let d = '';
  for (let y = 0; y < size; y++) {
    for (let x = 0; x < size; x++) {
      if (!modules[y][x]) continue;
      let run = 1;
      while (x + run < size && modules[y][x + run]) run++;
      d += `M${x + q} ${y + q}h${run}v1h-${run}z`;
      x += run - 1;
    }
  }
  const n = size + q * 2;
  const label = opts.label ? `role="img" aria-label="${opts.label.replace(/"/g, '&quot;')}"` : 'aria-hidden="true"';
  return `<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 ${n} ${n}" ${label} shape-rendering="crispEdges"><rect width="${n}" height="${n}" fill="${opts.light ?? '#fff'}"/><path d="${d}" fill="${opts.dark ?? '#0d0d0b'}"/></svg>`;
}
