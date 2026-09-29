/**
 * A small QR encoder for the joining link, so the Programme tab can print one
 * without a library: byte mode, error correction M, versions 1–10 (up to 213
 * bytes), best of the eight masks. ISO/IEC 18004; the layout follows Nayuki's
 * reference implementation. `qrModules` returns rows of booleans (dark = true);
 * `qrSvgPath` turns them into one SVG path in module units.
 */

// Level M, versions 1–10: total codewords, EC codewords per block, blocks, alignment centres (besides 6).
const TOTAL = [0, 26, 44, 70, 100, 134, 172, 196, 242, 292, 346]
const ECW = [0, 10, 16, 26, 18, 24, 16, 18, 22, 22, 26]
const BLOCKS = [0, 1, 1, 1, 2, 2, 4, 4, 4, 5, 5]
const ALIGN = [[], [], [18], [22], [26], [30], [34], [22, 38], [24, 42], [26, 46], [28, 50]]

// GF(256) with the QR primitive polynomial 0x11d.
const EXP = new Uint8Array(512)
const LOG = new Uint8Array(256)
for (let i = 0, x = 1; i < 255; i++) {
  EXP[i] = x
  LOG[x] = i
  x <<= 1
  if (x & 0x100) x ^= 0x11d
}
for (let i = 255; i < 512; i++) EXP[i] = EXP[i - 255]!
const mul = (a: number, b: number): number => (a && b ? EXP[LOG[a]! + LOG[b]!]! : 0)

/** Reed–Solomon remainder of `data` for `n` error-correction codewords. */
function rsRemainder(data: number[], n: number): number[] {
  const divisor = new Array<number>(n).fill(0)
  divisor[n - 1] = 1
  for (let i = 0, root = 1; i < n; i++, root = mul(root, 2)) {
    for (let j = 0; j < n; j++) {
      divisor[j] = mul(divisor[j]!, root)
      if (j + 1 < n) divisor[j]! ^= divisor[j + 1]!
    }
  }
  const result = new Array<number>(n).fill(0)
  for (const b of data) {
    const factor = b ^ result.shift()!
    result.push(0)
    for (let j = 0; j < n; j++) result[j]! ^= mul(divisor[j]!, factor)
  }
  return result
}

const MASK = [
  (r: number, c: number) => (r + c) % 2 === 0,
  (r: number) => r % 2 === 0,
  (_r: number, c: number) => c % 3 === 0,
  (r: number, c: number) => (r + c) % 3 === 0,
  (r: number, c: number) => (Math.floor(r / 2) + Math.floor(c / 3)) % 2 === 0,
  (r: number, c: number) => ((r * c) % 2) + ((r * c) % 3) === 0,
  (r: number, c: number) => (((r * c) % 2) + ((r * c) % 3)) % 2 === 0,
  (r: number, c: number) => (((r + c) % 2) + ((r * c) % 3)) % 2 === 0,
]

/** The spec's four penalty rules, so the chosen mask keeps the symbol easy to scan. */
function penalty(g: boolean[][]): number {
  const n = g.length
  let p = 0
  let dark = 0
  const lines: string[] = []
  for (let i = 0; i < n; i++) {
    let row = ''
    let col = ''
    for (let j = 0; j < n; j++) {
      row += g[i]![j] ? '1' : '0'
      col += g[j]![i] ? '1' : '0'
      if (g[i]![j]) dark++
    }
    lines.push(row, col)
  }
  for (const s of lines) {
    for (const run of s.match(/0{5,}|1{5,}/g) ?? []) p += run.length - 2
    for (const pat of ['10111010000', '00001011101']) for (let i = s.indexOf(pat); i >= 0; i = s.indexOf(pat, i + 1)) p += 40
  }
  for (let r = 0; r + 1 < n; r++)
    for (let c = 0; c + 1 < n; c++) {
      const d = g[r]![c]
      if (d === g[r + 1]![c] && d === g[r]![c + 1] && d === g[r + 1]![c + 1]) p += 3
    }
  return p + Math.max(0, Math.ceil(Math.abs(dark * 20 - n * n * 10) / (n * n)) - 1) * 10
}

export function qrModules(text: string): boolean[][] {
  const bytes = Array.from(new TextEncoder().encode(text))
  let v = 1
  const need = (ver: number) => 4 + (ver < 10 ? 8 : 16) + bytes.length * 8
  while (v <= 10 && (TOTAL[v]! - ECW[v]! * BLOCKS[v]!) * 8 < need(v)) v++
  if (v > 10) throw new Error('Too long for a QR code here (213 bytes at most).')
  const dataCw = TOTAL[v]! - ECW[v]! * BLOCKS[v]!

  // Bit stream: mode, count, bytes, terminator, byte padding, pad codewords.
  const bits: number[] = []
  const push = (val: number, n: number) => {
    for (let i = n - 1; i >= 0; i--) bits.push((val >> i) & 1)
  }
  push(4, 4)
  push(bytes.length, v < 10 ? 8 : 16)
  for (const b of bytes) push(b, 8)
  push(0, Math.min(4, dataCw * 8 - bits.length))
  while (bits.length % 8) bits.push(0)
  for (let pad = 0xec; bits.length < dataCw * 8; pad ^= 0xec ^ 0x11) push(pad, 8)
  const data: number[] = []
  for (let i = 0; i < bits.length; i += 8) data.push(bits.slice(i, i + 8).reduce((a, b) => (a << 1) | b, 0))

  // Blocks (the longer ones last), then interleave data and EC codewords.
  const nb = BLOCKS[v]!
  const short = Math.floor(dataCw / nb)
  const blocks: [number[], number[]][] = []
  for (let i = 0, k = 0; i < nb; i++) {
    const len = short + (i < nb - (dataCw % nb) ? 0 : 1)
    const d = data.slice(k, k + len)
    k += len
    blocks.push([d, rsRemainder(d, ECW[v]!)])
  }
  const cw: number[] = []
  for (let i = 0; i <= short; i++) for (const [d] of blocks) if (i < d.length) cw.push(d[i]!)
  for (let i = 0; i < ECW[v]!; i++) for (const [, e] of blocks) cw.push(e[i]!)

  // Function patterns.
  const size = v * 4 + 17
  const m: boolean[][] = Array.from({ length: size }, () => new Array<boolean>(size).fill(false))
  const fn: boolean[][] = Array.from({ length: size }, () => new Array<boolean>(size).fill(false))
  const set = (g: boolean[][], r: number, c: number, dark: boolean) => {
    if (r >= 0 && c >= 0 && r < size && c < size) {
      g[r]![c] = dark
      fn[r]![c] = true
    }
  }
  for (const [r0, c0] of [[3, 3], [3, size - 4], [size - 4, 3]] as const)
    for (let dr = -4; dr <= 4; dr++)
      for (let dc = -4; dc <= 4; dc++) {
        const d = Math.max(Math.abs(dr), Math.abs(dc))
        set(m, r0 + dr, c0 + dc, d !== 2 && d !== 4)
      }
  for (let i = 8; i < size - 8; i++) {
    set(m, 6, i, i % 2 === 0)
    set(m, i, 6, i % 2 === 0)
  }
  const al = v > 1 ? [6, ...ALIGN[v]!] : []
  const last = al.length - 1
  al.forEach((ar, i) =>
    al.forEach((ac, j) => {
      if ((i === 0 && (j === 0 || j === last)) || (i === last && j === 0)) return
      for (let dr = -2; dr <= 2; dr++) for (let dc = -2; dc <= 2; dc++) set(m, ar + dr, ac + dc, Math.max(Math.abs(dr), Math.abs(dc)) !== 1)
    }),
  )
  const drawFormat = (g: boolean[][], mask: number) => {
    let rem = mask
    for (let i = 0; i < 10; i++) rem = (rem << 1) ^ ((rem >> 9) * 0x537)
    const f = ((mask << 10) | rem) ^ 0x5412
    const b = (i: number) => ((f >> i) & 1) === 1
    for (let i = 0; i <= 5; i++) set(g, i, 8, b(i))
    set(g, 7, 8, b(6))
    set(g, 8, 8, b(7))
    set(g, 8, 7, b(8))
    for (let i = 9; i < 15; i++) set(g, 8, 14 - i, b(i))
    for (let i = 0; i < 8; i++) set(g, 8, size - 1 - i, b(i))
    for (let i = 8; i < 15; i++) set(g, size - 15 + i, 8, b(i))
    set(g, size - 8, 8, true)
  }
  drawFormat(m, 0)
  if (v >= 7) {
    let rem = v
    for (let i = 0; i < 12; i++) rem = (rem << 1) ^ ((rem >> 11) * 0x1f25)
    const vb = (v << 12) | rem
    for (let i = 0; i < 18; i++) {
      const a = size - 11 + (i % 3)
      const b = Math.floor(i / 3)
      set(m, a, b, ((vb >> i) & 1) === 1)
      set(m, b, a, ((vb >> i) & 1) === 1)
    }
  }

  // Data, zigzagging up and down in column pairs from the right, skipping column 6.
  let i = 0
  for (let right = size - 1; right >= 1; right -= 2) {
    if (right === 6) right = 5
    for (let vert = 0; vert < size; vert++)
      for (let j = 0; j < 2; j++) {
        const c = right - j
        const r = ((right + 1) & 2) === 0 ? size - 1 - vert : vert
        if (!fn[r]![c] && i < cw.length * 8) {
          m[r]![c] = ((cw[i >> 3]! >> (7 - (i & 7))) & 1) === 1
          i++
        }
      }
  }

  // The mask with the lowest penalty wins.
  let best: { g: boolean[][]; p: number } | null = null
  for (let mask = 0; mask < 8; mask++) {
    const g = m.map((row, r) => row.map((d, c) => (fn[r]![c] ? d : d !== MASK[mask]!(r, c))))
    drawFormat(g, mask)
    const p = penalty(g)
    if (best === null || p < best.p) best = { g, p }
  }
  return best!.g
}

/** One SVG path (module units, `quiet` modules of margin on every side) for `<path d=…>`. */
export function qrSvgPath(modules: boolean[][], quiet = 4): { d: string; size: number } {
  let d = ''
  modules.forEach((row, r) => row.forEach((dark, c) => dark && (d += `M${c + quiet} ${r + quiet}h1v1h-1z`)))
  return { d, size: modules.length + quiet * 2 }
}
