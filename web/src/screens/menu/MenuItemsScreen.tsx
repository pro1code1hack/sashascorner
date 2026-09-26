/**
 * Menu items: everything you sell, with price and margin.
 *
 * Owner feedback (2026-09-26): filters on top (no left category rail), many
 * more of them, combinable, shown as removable chips with "Clear all"; clear
 * pagination with a page size. Every filter lives in the URL
 * (`#/menu?cat=Coffee&margin=lt60&sort=margin_asc&page=2`) so a reload or a
 * shared link keeps it. Opening an item goes to its own page, `#/menu/<id>`.
 *
 * A product is several backend rows (one per size); the API groups them and
 * this screen shows one entry per product. Margin shown is the product's
 * LOWEST margin across its sizes on sale; a missing cost is "cost unknown",
 * never 0% (invariant 8), and an estimated one is italic with a "~".
 */
import { useMemo, useState } from 'react'
import {
  Button,
  Drawer,
  Empty,
  ErrorBox,
  GridCard,
  Input,
  Loading,
  PageHeader,
  SearchInput,
  Segmented,
  Select,
  cx,
} from '../../components/ui'
import { ActiveFilters, FilterBar, FilterSelect } from '../../components/ui/FilterBar'
import type { ActiveFilterChip, FilterOption } from '../../components/ui/FilterBar'
import { Pagination } from '../../components/ui/Pagination'
import { menuApi, useInvalidateMenu, useMenuItems, useSeasons } from '../../lib/menu-api'
import { useOperator } from '../../lib/operator'
import { href, navigate, useLocation } from '../../lib/router'
import type { MenuCategory, MenuGroup, MenuKind, SizeCode } from '../../lib/types/menu'
import { MONEY_INPUT, gbp, pctText, poundsToPence, sizeLabel } from './common/figures'
import { rememberMenuListQuery } from './listMemory'
import { MenuTabs } from './MenuTabs'
import { PhotoView } from './Photo'

const PAGE_SIZES = [24, 48, 96] as const
const KIND_LABEL: Record<MenuKind, string> = { DRINKS: 'Drinks', FOOD: 'Food', OTHER: 'Other' }
const catLabel = (c: string) => (c === '' ? 'No category' : c.replace(' (May 2026)', ''))

type MarginBand = 'lt60' | '60to75' | 'gt75' | 'unknown' | 'noprice'
const MARGIN_LABEL: Record<MarginBand, string> = {
  lt60: 'Margin under 60%',
  '60to75': 'Margin 60–75%',
  gt75: 'Margin over 75%',
  unknown: 'Cost unknown',
  noprice: 'No price',
}
type SortKey = 'name' | 'margin_asc' | 'margin_desc' | 'price_asc' | 'price_desc' | 'mpm_desc' | 'mpm_asc' | 'sales_desc'
const SORT_LABEL: Record<SortKey, string> = {
  name: 'Sort: name',
  margin_asc: 'Sort: lowest margin',
  margin_desc: 'Sort: highest margin',
  price_asc: 'Sort: cheapest',
  price_desc: 'Sort: dearest',
  mpm_desc: 'Sort: best £ per minute',
  mpm_asc: 'Sort: worst £ per minute',
  sales_desc: 'Sort: best sellers (30 days)',
}

/** Every filter the URL can carry. The default value = not filtering. */
const DEFAULTS = {
  q: '',
  cat: 'all',
  kind: 'all',
  size: 'all',
  margin: 'all',
  status: 'all',
  recipe: 'all',
  season: 'all',
  pmin: '',
  pmax: '',
  sort: 'name',
  page: '1',
  per: '24',
  view: 'grid',
} as const
type Filters = { -readonly [K in keyof typeof DEFAULTS]: string }

function readFilters(q: URLSearchParams): Filters {
  const f = { ...DEFAULTS } as Filters
  for (const k of Object.keys(DEFAULTS) as (keyof Filters)[]) {
    const v = q.get(k)
    if (v !== null) f[k] = v
  }
  return f
}

/* ---------------------------------------------------- group figures --- */

const onSale = (g: MenuGroup) => {
  const act = g.sizes.filter((s) => s.active)
  return act.length ? act : g.sizes
}
function minPrice(g: MenuGroup): number | null {
  const p = onSale(g)
    .map((s) => s.price_pence)
    .filter((n) => n > 0)
  return p.length ? Math.min(...p) : null
}
/** The product's weakest £/min across sizes on sale (pence; for ordering only). */
function lowestMpm(g: MenuGroup): { pence: string; n: number } | null {
  let best: { pence: string; n: number } | null = null
  for (const s of onSale(g)) {
    if (s.margin_per_minute_pence == null) continue
    const n = Number(s.margin_per_minute_pence)
    if (best === null || n < best.n) best = { pence: s.margin_per_minute_pence, n }
  }
  return best
}
function band(g: MenuGroup): MarginBand {
  const lm = g.lowest_margin
  if (lm.no_price) return 'noprice'
  if (lm.pct === null) return 'unknown'
  return lm.pct < 60 ? 'lt60' : lm.pct <= 75 ? '60to75' : 'gt75'
}
const hasRecipe = (g: MenuGroup) => g.template_id !== null || g.sizes.some((s) => s.has_recipe)
const sold = (g: MenuGroup) => Number(g.sold_30d ?? '0')

function nullsLast(a: number | null, b: number | null, dir: 1 | -1): number {
  if (a === null && b === null) return 0
  if (a === null) return 1
  if (b === null) return -1
  return (a - b) * dir
}

/* -------------------------------------------------------------- screen --- */

export function MenuItemsScreen() {
  const loc = useLocation()
  const data = useMenuItems()
  const seasons = useSeasons()
  const [creating, setCreating] = useState(false)
  const fromUrl = readFilters(loc.query)
  rememberMenuListQuery(loc.query)
  // The search box is typed into, so it is local state first: writing each key
  // to the hash and reading it back would reset the caret between keystrokes.
  const [q, setQ] = useState(fromUrl.q)
  const [seenQ, setSeenQ] = useState(fromUrl.q)
  if (seenQ !== fromUrl.q) {
    setSeenQ(fromUrl.q)
    setQ(fromUrl.q)
  }
  const f: Filters = { ...fromUrl, q }

  /** Change filters; anything but paging/view goes back to page 1. */
  const set = (patch: Partial<Filters>) => {
    const next: Filters = { ...f, ...patch }
    if (!('page' in patch)) next.page = '1'
    const query: Record<string, string> = {}
    for (const k of Object.keys(DEFAULTS) as (keyof Filters)[]) if (next[k] !== DEFAULTS[k]) query[k] = next[k]
    navigate('/menu', { query, replace: true })
  }

  const groups = useMemo(() => data.data?.groups ?? [], [data.data])
  const categories = data.data?.categories ?? []
  const pmin = f.pmin ? poundsToPence(f.pmin) : null
  const pmax = f.pmax ? poundsToPence(f.pmax) : null

  const shown = useMemo(() => {
    const needle = f.q.trim().toLowerCase()
    const rows = groups.filter((g) => {
      if (needle && !g.name.toLowerCase().includes(needle) && !(g.template_name ?? '').toLowerCase().includes(needle)) return false
      if (f.cat !== 'all' && (g.category ?? '') !== f.cat) return false
      if (f.kind !== 'all' && g.kind !== f.kind) return false
      if (f.size !== 'all' && !onSale(g).some((s) => (s.size_code ?? 'ONE') === f.size)) return false
      if (f.margin !== 'all' && band(g) !== f.margin) return false
      if (f.status === 'on' && !g.is_active) return false
      if (f.status === 'off' && g.is_active) return false
      if (f.status === 'untill' && g.on_till) return false
      if (f.recipe === 'template' && g.template_id === null) return false
      if (f.recipe === 'oneoff' && (g.template_id !== null || !hasRecipe(g))) return false
      if (f.recipe === 'none' && hasRecipe(g)) return false
      if (f.season === 'none' && g.season_id) return false
      if (f.season === 'any' && !g.season_id) return false
      if (/^\d+$/.test(f.season) && String(g.season_id ?? '') !== f.season) return false
      if (pmin !== null || pmax !== null) {
        const inRange = onSale(g).some(
          (s) => s.price_pence > 0 && (pmin === null || s.price_pence >= pmin) && (pmax === null || s.price_pence <= pmax),
        )
        if (!inRange) return false
      }
      return true
    })
    const byName = (a: MenuGroup, b: MenuGroup) => a.name.localeCompare(b.name)
    const mpm = (g: MenuGroup) => lowestMpm(g)?.n ?? null
    const cmp: Record<SortKey, (a: MenuGroup, b: MenuGroup) => number> = {
      name: byName,
      margin_asc: (a, b) => nullsLast(a.lowest_margin.pct, b.lowest_margin.pct, 1) || byName(a, b),
      margin_desc: (a, b) => nullsLast(a.lowest_margin.pct, b.lowest_margin.pct, -1) || byName(a, b),
      price_asc: (a, b) => nullsLast(minPrice(a), minPrice(b), 1) || byName(a, b),
      price_desc: (a, b) => nullsLast(minPrice(a), minPrice(b), -1) || byName(a, b),
      mpm_desc: (a, b) => nullsLast(mpm(a), mpm(b), -1) || byName(a, b),
      mpm_asc: (a, b) => nullsLast(mpm(a), mpm(b), 1) || byName(a, b),
      sales_desc: (a, b) => sold(b) - sold(a) || byName(a, b),
    }
    return [...rows].sort(cmp[f.sort as SortKey] ?? byName)
  }, [groups, f.q, f.cat, f.kind, f.size, f.margin, f.status, f.recipe, f.season, f.sort, pmin, pmax])

  const per = (PAGE_SIZES as readonly number[]).includes(Number(f.per)) ? Number(f.per) : 24
  const pages = Math.max(1, Math.ceil(shown.length / per))
  const page = Math.min(Math.max(1, Number(f.page) || 1), pages)
  const slice = shown.slice((page - 1) * per, page * per)

  /* ---- filter options ---- */
  const catOptions: FilterOption[] = [{ value: 'all', label: 'All categories' }]
  for (const kind of ['DRINKS', 'FOOD', 'OTHER'] as MenuKind[]) {
    for (const c of categories.filter((c) => c.kind === kind && c.count > 0)) {
      catOptions.push({ value: c.name, label: `${catLabel(c.name)} (${c.count})` })
    }
  }
  const seasonOptions: FilterOption[] = [
    { value: 'all', label: 'Any season' },
    { value: 'none', label: 'All-year items' },
    { value: 'any', label: 'Seasonal items' },
    ...(seasons.data ?? []).map((s) => ({ value: String(s.season_id), label: s.name })),
  ]

  const chips: ActiveFilterChip[] = []
  const chip = (key: keyof Filters, label: string) => chips.push({ key, label, onRemove: () => set({ [key]: DEFAULTS[key] }) })
  if (f.q) chip('q', `“${f.q}”`)
  if (f.cat !== 'all') chip('cat', catLabel(f.cat))
  if (f.kind !== 'all') chip('kind', KIND_LABEL[f.kind as MenuKind] ?? f.kind)
  if (f.size !== 'all') chip('size', `Size ${sizeLabel(f.size as SizeCode)}`)
  if (f.margin !== 'all') chip('margin', MARGIN_LABEL[f.margin as MarginBand] ?? f.margin)
  if (f.status !== 'all') chip('status', f.status === 'on' ? 'On the menu' : f.status === 'off' ? 'Off the menu' : 'Not on the till')
  if (f.recipe !== 'all')
    chip('recipe', f.recipe === 'template' ? 'Made from a recipe' : f.recipe === 'oneoff' ? 'Own recipe' : 'No recipe yet')
  if (f.season !== 'all') chip('season', seasonOptions.find((o) => o.value === f.season)?.label ?? 'Season')
  if (f.pmin) chip('pmin', `From £${f.pmin}`)
  if (f.pmax) chip('pmax', `Up to £${f.pmax}`)

  const clearAll = () =>
    set({ q: '', cat: 'all', kind: 'all', size: 'all', margin: 'all', status: 'all', recipe: 'all', season: 'all', pmin: '', pmax: '' })

  return (
    <>
      <PageHeader
        title="Menu"
        subtitle={<MenuTabs current="items" />}
        saved={data.isFetching ? 'Loading…' : undefined}
        actions={
          <Button variant="primary" className="rounded-[18px] px-[18px] text-lg" onClick={() => setCreating(true)}>
            + Add item
          </Button>
        }
      />
      <div className="flex-none border-b border-line bg-surface px-4 pb-2.5 pt-3 sm:px-5">
        <FilterBar
          label="Filter the menu"
          search={
            <SearchInput
              label="Search the menu"
              placeholder="Search items or recipes"
              value={q}
              onChange={(e) => {
                setQ(e.target.value)
                setSeenQ(e.target.value)
                set({ q: e.target.value })
              }}
            />
          }
          trailing={
            <>
              <FilterSelect
                label="Sort"
                value={f.sort}
                allValue="name"
                onChange={(v) => set({ sort: v })}
                options={(Object.keys(SORT_LABEL) as SortKey[]).map((k) => ({ value: k, label: SORT_LABEL[k] }))}
              />
              <Segmented
                label="View"
                value={f.view === 'list' ? 'list' : 'grid'}
                onChange={(v) => set({ view: v, page: f.page })}
                options={[
                  { value: 'grid', label: 'Grid' },
                  { value: 'list', label: 'List' },
                ]}
              />
            </>
          }
        >
          <FilterSelect label="Category" value={f.cat} onChange={(v) => set({ cat: v })} options={catOptions} />
          <FilterSelect
            label="Type"
            value={f.kind}
            onChange={(v) => set({ kind: v })}
            options={[
              { value: 'all', label: 'Drinks & food' },
              { value: 'DRINKS', label: 'Drinks' },
              { value: 'FOOD', label: 'Food' },
              { value: 'OTHER', label: 'Other' },
            ]}
          />
          <FilterSelect
            label="Margin"
            value={f.margin}
            onChange={(v) => set({ margin: v })}
            options={[
              { value: 'all', label: 'Any margin' },
              ...(Object.keys(MARGIN_LABEL) as MarginBand[]).map((k) => ({ value: k, label: MARGIN_LABEL[k] })),
            ]}
          />
          <FilterSelect
            label="Size"
            value={f.size}
            onChange={(v) => set({ size: v })}
            options={[
              { value: 'all', label: 'Any size' },
              ...(['S', 'M', 'XL', 'ONE'] as SizeCode[]).map((s) => ({ value: s, label: `Size ${sizeLabel(s)}` })),
            ]}
          />
          <FilterSelect
            label="On the menu"
            value={f.status}
            onChange={(v) => set({ status: v })}
            options={[
              { value: 'all', label: 'On & off menu' },
              { value: 'on', label: 'On the menu' },
              { value: 'off', label: 'Off the menu' },
              { value: 'untill', label: 'Not on the till' },
            ]}
          />
          <FilterSelect
            label="Recipe"
            value={f.recipe}
            onChange={(v) => set({ recipe: v })}
            options={[
              { value: 'all', label: 'Any recipe' },
              { value: 'template', label: 'Made from a recipe' },
              { value: 'oneoff', label: 'Own recipe' },
              { value: 'none', label: 'No recipe yet' },
            ]}
          />
          <FilterSelect label="Season" value={f.season} onChange={(v) => set({ season: v })} options={seasonOptions} />
          <PriceRange min={f.pmin} max={f.pmax} onChange={(pmin, pmax) => set({ pmin, pmax })} />
        </FilterBar>
        <ActiveFilters className="mt-2" chips={chips} onClearAll={clearAll} summary={data.data ? `${shown.length} of ${groups.length} items` : undefined} />
      </div>

      <div className="min-h-0 flex-1 overflow-y-auto bg-canvas px-4 pb-6 pt-3.5 sm:px-5">
        {data.isLoading && <Loading what="Loading the menu" />}
        {data.error && <ErrorBox error={data.error} what="the menu" />}
        {data.data && shown.length === 0 && (
          <Empty roomy>
            {chips.length ? (
              <>
                Nothing matches these filters.{' '}
                <button type="button" className="font-bold text-brand-ink underline" onClick={clearAll}>
                  Clear them
                </button>
              </>
            ) : (
              'Nothing here yet. Use “Add item”.'
            )}
          </Empty>
        )}
        {f.view === 'list' ? (
          slice.length > 0 && <ListView rows={slice} />
        ) : (
          <div className="grid grid-cols-[repeat(auto-fill,minmax(150px,1fr))] gap-3.5 compact:grid-cols-[repeat(auto-fill,minmax(170px,1fr))] wide:grid-cols-[repeat(auto-fill,minmax(200px,1fr))]">
            {slice.map((g) => (
              <Card key={g.key} group={g} />
            ))}
          </div>
        )}
        {shown.length > 0 && (
          <Pagination
            className="mt-4"
            page={page}
            pageSize={per}
            total={shown.length}
            noun="items"
            sizes={PAGE_SIZES}
            onPage={(p) => set({ page: String(p) })}
            onPageSize={(n) => set({ per: String(n) })}
          />
        )}
      </div>
      {creating && (
        <CreateDrawer
          categories={categories}
          defaultCategory={f.cat === 'all' ? '' : f.cat}
          onClose={() => setCreating(false)}
          onCreated={(id) => {
            setCreating(false)
            navigate(`/menu/${id}`)
          }}
        />
      )}
    </>
  )
}

/** "£ from – to": applied on blur or Enter, so typing does not refilter every key. */
function PriceRange({ min, max, onChange }: { min: string; max: string; onChange: (min: string, max: string) => void }) {
  const [lo, setLo] = useState(min)
  const [hi, setHi] = useState(max)
  const [seen, setSeen] = useState({ min, max })
  if (seen.min !== min || seen.max !== max) {
    setSeen({ min, max })
    setLo(min)
    setHi(max)
  }
  const commit = () => {
    if (lo !== min || hi !== max) onChange(lo, hi)
  }
  const cls =
    'h-[34px] w-[64px] rounded-full border border-line-control bg-surface px-2.5 text-base text-ink outline-none focus-visible:border-brand focus-visible:ring-3 focus-visible:ring-brand-wash'
  return (
    <span className={cx('flex items-center gap-1 text-sm', min || max ? 'font-bold text-brand-ink' : 'text-ink-2')}>
      £
      <input
        aria-label="Lowest price, pounds"
        placeholder="from"
        inputMode="decimal"
        value={lo}
        onChange={(e) => MONEY_INPUT.test(e.target.value) && setLo(e.target.value)}
        onBlur={commit}
        onKeyDown={(e) => e.key === 'Enter' && commit()}
        className={cls}
      />
      –
      <input
        aria-label="Highest price, pounds"
        placeholder="to"
        inputMode="decimal"
        value={hi}
        onChange={(e) => MONEY_INPUT.test(e.target.value) && setHi(e.target.value)}
        onBlur={commit}
        onKeyDown={(e) => e.key === 'Enter' && commit()}
        className={cls}
      />
    </span>
  )
}

function marginBadge(g: MenuGroup): { text: string; cls: string; italic: boolean } {
  const lm = g.lowest_margin
  if (lm.no_price) return { text: 'no price', cls: 'bg-wash text-ink-2', italic: false }
  if (lm.pct === null) return { text: 'cost unknown', cls: 'bg-wash text-ink-2', italic: false }
  return {
    text: `${lm.is_estimate ? '~' : ''}${pctText(lm.pct)}`,
    cls: lm.pct < 60 ? 'bg-bad-wash text-bad-ink' : 'bg-ok-wash text-ok-ink',
    italic: lm.is_estimate,
  }
}

function priceLine(g: MenuGroup): string {
  const priced = g.sizes.filter((s) => s.active || !g.is_active)
  if (priced.length === 0 || priced.every((s) => s.price_pence <= 0)) return 'no price'
  return priced.map((s) => `${priced.length > 1 ? `${sizeLabel(s.size_code)} ` : ''}${gbp(s.price_pence)}`).join(' · ')
}

const itemPath = (g: MenuGroup) => `/menu/${g.sizes[0]?.menu_item_id ?? g.anchor_id}`

function Card({ group }: { group: MenuGroup }) {
  const badge = marginBadge(group)
  return (
    <GridCard inactive={!group.is_active} onClick={() => navigate(itemPath(group))} label={`Open ${group.name}`}>
      <div className="relative aspect-[4/3] w-full">
        <PhotoView url={group.photo_url} placeholder="Photo" className="absolute inset-0" />
        <span
          className={cx(
            'fig pointer-events-none absolute left-2 top-2 rounded-full px-[9px] py-[3px] text-xs font-bold',
            badge.cls,
            badge.italic && 'italic',
          )}
        >
          {badge.text}
        </span>
      </div>
      <div className="flex min-h-16 flex-col gap-[3px] px-3 pb-3 pt-2.5">
        <div className="line-clamp-2 text-md font-bold leading-[1.25]">{group.name}</div>
        <div className="fig text-sm text-ink-2">{priceLine(group)}</div>
        {!group.is_active && <div className="text-xs font-bold text-ink-2">Off the menu</div>}
      </div>
    </GridCard>
  )
}

function ListView({ rows }: { rows: MenuGroup[] }) {
  return (
    <div className="overflow-hidden rounded-card-lg bg-surface shadow-raised">
      <div className="hidden grid-cols-[44px_minmax(0,1fr)_200px_104px_92px_80px] gap-3 border-b border-line px-3.5 py-2 text-label font-bold uppercase tracking-[.06em] text-ink-3 compact:grid">
        <span />
        <span>Item</span>
        <span className="text-right">Price</span>
        <span className="text-right">Margin</span>
        <span className="text-right">£ / min</span>
        <span className="text-right">Sold 30d</span>
      </div>
      <ul>
        {rows.map((g) => {
          const b = marginBadge(g)
          const mpm = lowestMpm(g)
          const mpmEst = onSale(g).some((s) => s.cost.is_estimate || s.prep_is_estimate)
          return (
            <li key={g.key} className="border-b border-line-row last:border-b-0">
              <a
                href={href(itemPath(g))}
                className={cx(
                  'grid grid-cols-[44px_minmax(0,1fr)_auto] items-center gap-x-3 px-3.5 py-2.5 text-ink no-underline hover:bg-canvas-2 compact:grid-cols-[44px_minmax(0,1fr)_200px_104px_92px_80px]',
                  !g.is_active && 'opacity-60',
                )}
              >
                <span className="relative size-11 overflow-hidden rounded-control">
                  <PhotoView url={g.photo_url} placeholder="" className="absolute inset-0" />
                </span>
                <span className="min-w-0">
                  <span className="block truncate text-md font-bold">{g.name}</span>
                  <span className="block truncate text-sm text-ink-2">
                    {catLabel(g.category ?? '')}
                    {g.template_name ? ` · ${g.template_name} recipe` : ''}
                    {g.season_name ? ` · ${g.season_name}` : ''}
                    {!g.is_active ? ' · off the menu' : ''}
                  </span>
                  <span className="fig block truncate text-sm text-ink-2 compact:hidden">{priceLine(g)}</span>
                </span>
                <span className="fig hidden truncate text-right text-base compact:block">{priceLine(g)}</span>
                <span className="text-right">
                  <span className={cx('fig inline-block whitespace-nowrap rounded-full px-2 py-0.5 text-xs font-bold', b.cls, b.italic && 'italic')}>
                    {b.text}
                  </span>
                </span>
                <span className={cx('fig hidden text-right text-base compact:block', mpmEst && 'italic')}>
                  {mpm === null ? '—' : gbp(mpm.pence)}
                </span>
                <span className="fig hidden text-right text-base compact:block">{g.sold_30d ?? '—'}</span>
              </a>
            </li>
          )
        })}
      </ul>
      <p className="border-t border-line px-3.5 py-2 text-xs text-ink-2">
        Margin and £/min are each product&rsquo;s weakest size on sale. Italic figures rest on estimated prices or untimed prep.
      </p>
    </div>
  )
}

function NewCategory() {
  const [name, setName] = useState('')
  const [msg, setMsg] = useState<string | null>(null)
  const invalidate = useInvalidateMenu()
  const add = async (kind: 'DRINKS' | 'FOOD') => {
    if (!name.trim()) return
    const r = await menuApi.category(name.trim(), kind)
    if (r.kind === 'ok') {
      setName('')
      setMsg('Added. Pick it above.')
      await invalidate()
    } else setMsg(r.message)
  }
  return (
    <div className="flex flex-col gap-1.5 border-t border-line pt-3">
      <span className="text-xs font-bold text-ink-2">Need a new category?</span>
      <div className="flex gap-1.5">
        <Input aria-label="New category" placeholder="Category name" value={name} onChange={(e) => setName(e.target.value)} />
        <Button size="sm" variant="outline" onClick={() => add('DRINKS')} disabled={!name.trim()}>
          + Drinks
        </Button>
        <Button size="sm" variant="outline" onClick={() => add('FOOD')} disabled={!name.trim()}>
          + Food
        </Button>
      </div>
      {msg && <p className="text-xs text-ink-2">{msg}</p>}
    </div>
  )
}

function CreateDrawer({
  categories,
  defaultCategory,
  onClose,
  onCreated,
}: {
  categories: MenuCategory[]
  defaultCategory: string
  onClose: () => void
  onCreated: (id: number) => void
}) {
  const [name, setName] = useState('')
  const [cat, setCat] = useState(defaultCategory)
  const [size, setSize] = useState<SizeCode | ''>('ONE')
  const [price, setPrice] = useState('')
  const [operator] = useOperator()
  const [busy, setBusy] = useState(false)
  const [msg, setMsg] = useState<string | null>(null)
  const invalidate = useInvalidateMenu()
  const pence = poundsToPence(price)
  return (
    <Drawer open onClose={onClose} title="Add an item" width={420} compactWidth={380}>
      <p className="text-base text-ink-2">
        A new one-off item. Add its ingredients, other sizes and time to make on its page after creating it. To add a
        flavour to a recipe, use Menu › Recipes.
      </p>
      <label className="flex flex-col gap-1 text-xs font-bold text-ink-2">
        Name
        <Input value={name} onChange={(e) => setName(e.target.value)} className="font-normal" autoFocus />
      </label>
      <div className="grid grid-cols-2 gap-2">
        <label className="flex min-w-0 flex-col gap-1 text-xs font-bold text-ink-2">
          Category
          <Select value={cat} onChange={(e) => setCat(e.target.value)} className="font-normal text-ink">
            <option value="">No category</option>
            {categories
              .filter((c) => c.name)
              .map((c) => (
                <option key={c.name} value={c.name}>
                  {catLabel(c.name)}
                </option>
              ))}
          </Select>
        </label>
        <label className="flex min-w-0 flex-col gap-1 text-xs font-bold text-ink-2">
          Size
          <Select value={size} onChange={(e) => setSize(e.target.value as SizeCode)} className="font-normal text-ink">
            {(['ONE', 'S', 'M', 'XL'] as SizeCode[]).map((s) => (
              <option key={s} value={s}>
                {sizeLabel(s)}
              </option>
            ))}
          </Select>
        </label>
      </div>
      <label className="flex flex-col gap-1 text-xs font-bold text-ink-2">
        Sell price £
        <Input numeric value={price} placeholder="0.00" onChange={(e) => MONEY_INPUT.test(e.target.value) && setPrice(e.target.value)} />
      </label>
      {msg && (
        <p role="alert" className="text-sm text-bad-ink">
          {msg}
        </p>
      )}
      <Button
        variant="primary"
        pending={busy}
        pendingLabel="Adding…"
        disabled={operator === null || name.trim() === '' || pence === null}
        onClick={async () => {
          if (!operator || pence === null) return
          setBusy(true)
          const r = await menuApi.create({
            name: name.trim(),
            category: cat || null,
            note: null,
            sizes: [{ size_code: size || null, price_pence: pence, lines: [] }],
            actor: operator,
          })
          setBusy(false)
          if (r.kind === 'ok' && r.data.menu_item_ids[0] !== undefined) {
            await invalidate()
            onCreated(r.data.menu_item_ids[0])
          } else if (r.kind !== 'ok') setMsg(r.message)
        }}
      >
        Add item
      </Button>
      <NewCategory />
    </Drawer>
  )
}
