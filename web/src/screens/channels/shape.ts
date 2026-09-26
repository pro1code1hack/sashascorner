/**
 * The channel payload, read defensively.
 *
 * `lib/types.ts` types `performance` and `findings` as `unknown[]` — honestly,
 * because in every fixture and on every live day so far they are EMPTY, and a
 * fully-typed shape nobody has ever seen a row of would be a guess dressed up
 * as a contract. `lib/` is shared and not ours to edit, so the shape lives here
 * and arrives through a reader rather than a cast.
 *
 * The fields mirror `ChannelPerformanceOut` / `ChannelFinding` in
 * `cafeops/api/schemas.py`. The reader drops anything malformed instead of
 * rendering `undefined` as a number, so the day the first Deliveroo export
 * lands the populated path renders rather than the screen throwing.
 *
 * Nothing here parses money to a float: pence stay integers and the basis-point
 * ratios are divided by an exact power of ten in `lib/dec.ts`.
 */
import { fromInt, shiftDown, toFixed } from '../../lib/dec'

export interface FieldCoverage {
  field_name: string
  reported: number
  missing: number
  complete: boolean
}

export interface ChannelPerformance {
  channel: string
  since: string
  until: string
  days: number
  /** Provenance. A hand export and a scrape are not equally trustworthy. */
  sources: string[]
  gross_pence: number | null
  commission_pence: number | null
  ad_spend_pence: number | null
  attributed_revenue_pence: number | null
  orders: number | null
  impressions: number | null
  menu_views: number | null
  /** Contribution after commission AND ad spend, over days that reported all
   *  three. A day missing one is dropped whole, never part-subtracted. */
  net_pence: number | null
  net_complete_days: number
  net_incomplete_days: number
  /** Basis points. 10000 = 1.00×. */
  roas_bp: number | null
  commission_rate_bp: number | null
  net_margin_bp: number | null
  conversion_bp: number | null
  /** The reported days in order. An aggregate says how much; this says whether
   *  it was steady. Covers only days that reported -- 9 of a 28-day window. */
  daily: ChannelDay[]
  coverage: FieldCoverage[]
  caveats: string[]
}

/** One reported day. A null field means that day did not report it -- NOT zero. */
export interface ChannelDay {
  metric_date: string
  gross_pence: number | null
  orders: number | null
  ad_spend_pence: number | null
}

export interface ConversionGap {
  menu_item_id: number
  item_name: string
  best_rank: number
  views: number
  orders: number
  conversion_bp: number
  benchmark_bp: number
  shortfall_bp: number
  relative_shortfall_bp: number
  lost_orders: number
  lost_revenue_pence: number | null
  days: number
  /** The API's own sentence. Rendered verbatim — never re-worded. */
  sentence: string
}

/** Ranks well, converts badly: the cheapest thing on this screen to fix. */
export interface ChannelFinding {
  channel: string
  benchmark_bp: number | null
  considered: number
  min_views: number
  rank_threshold: number
  gaps: ConversionGap[]
  caveats: string[]
}

/* ------------------------------------------------------------- readers --- */

type Obj = Record<string, unknown>

function asObj(v: unknown): Obj | null {
  return typeof v === 'object' && v !== null && !Array.isArray(v) ? (v as Obj) : null
}

function str(o: Obj, k: string, fallback = ''): string {
  const v = o[k]
  return typeof v === 'string' ? v : fallback
}

/** An integer count or integer pence. Rounded, so `fromInt` can never throw on
 *  a payload that sent 3.0 where it promised 3. */
function int(o: Obj, k: string, fallback: number): number {
  const v = o[k]
  return typeof v === 'number' && Number.isFinite(v) ? Math.round(v) : fallback
}

function intOrNull(o: Obj, k: string): number | null {
  const v = o[k]
  return typeof v === 'number' && Number.isFinite(v) ? Math.round(v) : null
}

function strs(o: Obj, k: string): string[] {
  const v = o[k]
  return Array.isArray(v) ? v.filter((x): x is string => typeof x === 'string') : []
}

function coverage(o: Obj): FieldCoverage[] {
  const v = o['coverage']
  if (!Array.isArray(v)) return []
  const out: FieldCoverage[] = []
  for (const raw of v) {
    const c = asObj(raw)
    if (!c) continue
    out.push({
      field_name: str(c, 'field_name'),
      reported: int(c, 'reported', 0),
      missing: int(c, 'missing', 0),
      complete: c['complete'] === true,
    })
  }
  return out
}

/** Malformed day rows are dropped rather than rendered as `undefined`. */
function daily(o: Obj): ChannelDay[] {
  const raw = o['daily']
  if (!Array.isArray(raw)) return []
  const out: ChannelDay[] = []
  for (const r of raw) {
    const d = asObj(r)
    if (!d) continue
    const when = str(d, 'metric_date')
    if (!when) continue
    out.push({
      metric_date: when,
      gross_pence: intOrNull(d, 'gross_pence'),
      orders: intOrNull(d, 'orders'),
      ad_spend_pence: intOrNull(d, 'ad_spend_pence'),
    })
  }
  return out
}

export function readPerformance(rows: readonly unknown[]): ChannelPerformance[] {
  const out: ChannelPerformance[] = []
  for (const raw of rows) {
    const o = asObj(raw)
    if (!o) continue
    out.push({
      channel: str(o, 'channel', 'unknown'),
      since: str(o, 'since'),
      until: str(o, 'until'),
      days: int(o, 'days', 0),
      sources: strs(o, 'sources'),
      gross_pence: intOrNull(o, 'gross_pence'),
      commission_pence: intOrNull(o, 'commission_pence'),
      ad_spend_pence: intOrNull(o, 'ad_spend_pence'),
      attributed_revenue_pence: intOrNull(o, 'attributed_revenue_pence'),
      orders: intOrNull(o, 'orders'),
      impressions: intOrNull(o, 'impressions'),
      menu_views: intOrNull(o, 'menu_views'),
      net_pence: intOrNull(o, 'net_pence'),
      net_complete_days: int(o, 'net_complete_days', 0),
      net_incomplete_days: int(o, 'net_incomplete_days', 0),
      roas_bp: intOrNull(o, 'roas_bp'),
      commission_rate_bp: intOrNull(o, 'commission_rate_bp'),
      net_margin_bp: intOrNull(o, 'net_margin_bp'),
      conversion_bp: intOrNull(o, 'conversion_bp'),
      daily: daily(o),
      coverage: coverage(o),
      caveats: strs(o, 'caveats'),
    })
  }
  return out
}

export function readFindings(rows: readonly unknown[]): ChannelFinding[] {
  const out: ChannelFinding[] = []
  for (const raw of rows) {
    const o = asObj(raw)
    if (!o) continue
    const gapsRaw = o['gaps']
    const gaps: ConversionGap[] = []
    if (Array.isArray(gapsRaw)) {
      for (const g of gapsRaw) {
        const x = asObj(g)
        if (!x) continue
        gaps.push({
          menu_item_id: int(x, 'menu_item_id', 0),
          item_name: str(x, 'item_name', 'unnamed item'),
          best_rank: int(x, 'best_rank', 0),
          views: int(x, 'views', 0),
          orders: int(x, 'orders', 0),
          conversion_bp: int(x, 'conversion_bp', 0),
          benchmark_bp: int(x, 'benchmark_bp', 0),
          shortfall_bp: int(x, 'shortfall_bp', 0),
          relative_shortfall_bp: int(x, 'relative_shortfall_bp', 0),
          lost_orders: int(x, 'lost_orders', 0),
          lost_revenue_pence: intOrNull(x, 'lost_revenue_pence'),
          days: int(x, 'days', 0),
          sentence: str(x, 'sentence'),
        })
      }
    }
    out.push({
      channel: str(o, 'channel', 'unknown'),
      benchmark_bp: intOrNull(o, 'benchmark_bp'),
      considered: int(o, 'considered', 0),
      min_views: int(o, 'min_views', 0),
      rank_threshold: int(o, 'rank_threshold', 0),
      gaps,
      caveats: strs(o, 'caveats'),
    })
  }
  return out
}

/* ------------------------------------------------------------ formatting --- */

/** Basis points as a multiple: 32000 → `3.20×`. Exact, via integer shift. */
export function bpTimes(bp: number | null): string | null {
  if (bp === null) return null
  return `${toFixed(shiftDown(fromInt(bp), 4), 2)}×`
}

/** Basis points as a percentage: 2750 → `27.50%`. Exact, via integer shift. */
export function bpPct(bp: number | null): string | null {
  if (bp === null) return null
  return `${toFixed(shiftDown(fromInt(bp), 2), 2)}%`
}

/** `DELIVEROO` → `Deliveroo`, `JUST_EAT` → `Just Eat`. */
export function channelName(code: string): string {
  return code
    .split(/[_\s]+/)
    .filter((w) => w.length > 0)
    .map((w) => w.charAt(0).toUpperCase() + w.slice(1).toLowerCase())
    .join(' ')
}

/** The API writes its em dashes as `--`. Its prose is otherwise verbatim. */
export function prose(s: string): string {
  return s.replace(/ -- /g, ' — ')
}

const LONDON = 'Europe/London'

/** A plain `YYYY-MM-DD` from the API, read as a local day. */
export function day(iso: string): string {
  if (!/^\d{4}-\d{2}-\d{2}$/.test(iso)) return iso
  return new Intl.DateTimeFormat('en-GB', {
    day: '2-digit',
    month: 'short',
    year: 'numeric',
    timeZone: LONDON,
  }).format(new Date(`${iso}T12:00:00Z`))
}

function utcDay(iso: string): number | null {
  const m = /^(\d{4})-(\d{2})-(\d{2})$/.exec(iso)
  if (!m) return null
  return Date.UTC(Number(m[1]), Number(m[2]) - 1, Number(m[3]))
}

/** Inclusive day count of the window, or null if either bound is malformed. */
export function windowDays(since: string, until: string): number | null {
  const a = utcDay(since)
  const b = utcDay(until)
  if (a === null || b === null) return null
  return Math.round((b - a) / 86_400_000) + 1
}
