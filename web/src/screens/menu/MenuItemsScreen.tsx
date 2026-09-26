/**
 * Menu items (recipes-menu-ingredients.md §V2): everything you sell, with
 * price and margin. A product is several backend rows (one per size); the API
 * groups them and the grid shows one card per product.
 *
 * Selection is in the URL (`#/menu?item=<menu item id>&cat=<category>`) so the
 * Recipes and Ingredients screens can link straight to an item.
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
  Select,
  cx,
} from '../../components/ui'
import { OperatorNeeded } from '../../components/shell/Operator'
import { menuApi, useInvalidateMenu, useMenuItems } from '../../lib/menu-api'
import { useOperator } from '../../lib/operator'
import { navigate, useLocation } from '../../lib/router'
import type { MenuCategory, MenuGroup, MenuKind, SizeCode } from '../../lib/types/menu'
import { MONEY_INPUT, gbp, pctText, poundsToPence, sizeLabel } from './common/figures'
import { RailChips, RailColumn } from './common/Rail'
import type { RailItem } from './common/Rail'
import { ItemDrawer } from './ItemDrawer'
import { PhotoView } from './Photo'

const PAGE = 24
const KIND_HEAD: Record<MenuKind, string> = { DRINKS: 'Drinks', FOOD: 'Food', OTHER: 'Other' }

const catLabel = (c: string) => (c === '' ? 'No category' : c.replace(' (May 2026)', ''))

export function MenuItemsScreen() {
  const loc = useLocation()
  const data = useMenuItems()
  const [q, setQ] = useState('')
  const [limit, setLimit] = useState(PAGE)
  const [creating, setCreating] = useState(false)
  const cat = loc.query.get('cat') ?? 'All'
  const itemParam = loc.query.get('item')
  const itemId = itemParam && /^\d+$/.test(itemParam) ? Number(itemParam) : null

  const setQuery = (next: { cat?: string; item?: number | null }) => {
    const query: Record<string, string> = {}
    const c = next.cat ?? cat
    if (c !== 'All') query.cat = c
    const it = next.item === undefined ? itemId : next.item
    if (it !== null) query.item = String(it)
    navigate('/menu', { query, replace: true })
  }

  const groups = data.data?.groups ?? []
  const shown = useMemo(() => {
    const needle = q.trim().toLowerCase()
    return groups
      .filter((g) => cat === 'All' || (g.category ?? '') === cat)
      .filter((g) => needle === '' || g.name.toLowerCase().includes(needle))
  }, [groups, cat, q])
  const open = itemId !== null || creating

  const rail: RailItem[] = [
    {
      kind: 'row',
      key: 'all',
      label: 'All items',
      count: groups.length,
      active: cat === 'All',
      onSelect: () => {
        setLimit(PAGE)
        setQuery({ cat: 'All' })
      },
    },
  ]
  for (const kind of ['DRINKS', 'FOOD', 'OTHER'] as MenuKind[]) {
    const cats = (data.data?.categories ?? []).filter((c) => c.kind === kind)
    if (!cats.length) continue
    rail.push({ kind: 'head', label: KIND_HEAD[kind] })
    for (const c of cats) {
      rail.push({
        kind: 'row',
        key: `c:${c.name}`,
        label: catLabel(c.name),
        count: c.count,
        active: cat === c.name,
        onSelect: () => {
          setLimit(PAGE)
          setQuery({ cat: c.name })
        },
      })
    }
  }

  return (
    <>
      <PageHeader
        title="Menu items"
        subtitle="everything you sell, with price and margin"
        saved={data.isFetching ? 'Loading…' : 'Saved'}
        actions={
          <Button
            variant="primary"
            className="rounded-[18px] px-[18px] text-lg"
            onClick={() => {
              setCreating(true)
              setQ('')
              setQuery({ item: null })
            }}
          >
            + Add item
          </Button>
        }
      />
      <RailChips items={rail} label="Menu categories" />
      <div className="flex min-h-0 flex-1">
        <RailColumn items={rail} label="Menu categories" footer={<NewCategory />} />
        <div className={cx('flex min-h-0 min-w-0 flex-1 flex-col bg-canvas', open && 'max-compact:hidden')}>
          <div className="flex flex-none items-center gap-2.5 px-4 pb-1 pt-3.5 sm:px-5">
            <SearchInput
              label="Search the menu"
              placeholder="Search the menu"
              value={q}
              onChange={(e) => {
                setQ(e.target.value)
                setLimit(PAGE)
              }}
              className="flex-1"
            />
            <span className="whitespace-nowrap text-base text-ink-2">
              {shown.length} items{cat === 'All' ? '' : ` in ${catLabel(cat)}`}
            </span>
          </div>
          <div className="min-h-0 flex-1 overflow-y-auto px-4 pb-6 pt-3 sm:px-5">
            {data.isLoading && <Loading what="Loading the menu" />}
            {data.error && <ErrorBox error={data.error} what="the menu" />}
            {data.data && shown.length === 0 && <Empty roomy>Nothing here yet. Use “Add item”.</Empty>}
            <div
              className={cx(
                'grid gap-3.5',
                open
                  ? 'grid-cols-[repeat(auto-fill,minmax(150px,1fr))] wide:grid-cols-[repeat(auto-fill,minmax(180px,1fr))]'
                  : 'grid-cols-[repeat(auto-fill,minmax(150px,1fr))] compact:grid-cols-[repeat(auto-fill,minmax(170px,1fr))] wide:grid-cols-[repeat(auto-fill,minmax(200px,1fr))]',
              )}
            >
              {shown.slice(0, limit).map((g) => (
                <Card
                  key={g.key}
                  group={g}
                  selected={itemId !== null && g.sizes.some((s) => s.menu_item_id === itemId)}
                  onOpen={() => {
                    setCreating(false)
                    setQuery({ item: g.sizes[0]?.menu_item_id ?? g.anchor_id })
                  }}
                />
              ))}
            </div>
            {shown.length > limit && (
              <div className="flex justify-center pt-[18px]">
                <button
                  type="button"
                  onClick={() => setLimit((l) => l + 48)}
                  className="h-[42px] rounded-button border border-line-control bg-surface px-5 text-base font-bold text-brand-ink hover:bg-canvas"
                >
                  Show {Math.min(48, shown.length - limit)} more
                </button>
              </div>
            )}
          </div>
        </div>
        {itemId !== null && !creating && (
          <ItemDrawer
            menuItemId={itemId}
            categories={data.data?.categories ?? []}
            onClose={() => setQuery({ item: null })}
            onOpen={(id) => setQuery({ item: id })}
          />
        )}
        {creating && (
          <CreateDrawer
            categories={data.data?.categories ?? []}
            defaultCategory={cat === 'All' ? '' : cat}
            onClose={() => setCreating(false)}
            onCreated={(id) => {
              setCreating(false)
              setQuery({ item: id })
            }}
          />
        )}
      </div>
    </>
  )
}

function Card({ group, selected, onOpen }: { group: MenuGroup; selected: boolean; onOpen: () => void }) {
  const lm = group.lowest_margin
  const badge = lm.no_price
    ? { text: 'no price', cls: 'bg-wash text-ink-2' }
    : lm.pct === null
      ? { text: 'cost unknown', cls: 'bg-wash text-ink-2' }
      : {
          text: `${lm.is_estimate ? '~' : ''}${pctText(lm.pct)}`,
          cls: lm.pct < 60 ? 'bg-bad-wash text-bad-ink' : 'bg-ok-wash text-ok-ink',
        }
  const priced = group.sizes.filter((s) => s.active || !group.is_active)
  const line =
    priced.length === 0 || priced.every((s) => s.price_pence <= 0)
      ? 'no price'
      : priced.map((s) => `${priced.length > 1 ? `${sizeLabel(s.size_code)} ` : ''}${gbp(s.price_pence)}`).join(' · ')
  return (
    <GridCard selected={selected} inactive={!group.is_active} onClick={onOpen} label={`Open ${group.name}`}>
      <div className="relative aspect-[4/3] w-full">
        <PhotoView url={group.photo_url} placeholder="Photo" className="absolute inset-0" />
        <span className={cx('fig pointer-events-none absolute left-2 top-2 rounded-full px-[9px] py-[3px] text-xs font-bold', badge.cls, lm.is_estimate && 'italic')}>
          {badge.text}
        </span>
      </div>
      <div className="flex min-h-16 flex-col gap-[3px] px-3 pb-3 pt-2.5">
        <div className="line-clamp-2 text-md font-bold leading-[1.25]">{group.name}</div>
        <div className="fig text-sm text-ink-2">{line}</div>
        {!group.is_active && <div className="text-xs font-bold text-ink-2">Off the menu</div>}
      </div>
    </GridCard>
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
      setMsg(null)
      await invalidate()
    } else setMsg(r.message)
  }
  return (
    <div className="mx-3 mt-3.5 border-t border-line pt-2.5">
      <input
        aria-label="New category"
        placeholder="New category"
        value={name}
        onChange={(e) => setName(e.target.value)}
        className="w-full rounded-control border border-line px-2 py-1 text-base outline-none focus-visible:border-brand focus-visible:ring-3 focus-visible:ring-brand-wash"
      />
      <div className="mt-1.5 flex gap-1.5">
        <button type="button" onClick={() => add('DRINKS')} className="flex-1 rounded-button border border-line-strong py-px text-sm hover:bg-canvas">
          + drinks
        </button>
        <button type="button" onClick={() => add('FOOD')} className="flex-1 rounded-button border border-line-strong py-px text-sm hover:bg-canvas">
          + food
        </button>
      </div>
      {msg && <p className="mt-1 text-xs text-bad-ink">{msg}</p>}
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
        A new one-off item, with its own recipe lines (add them after creating it). To add a flavour to a recipe, use Recipes.
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
      <OperatorNeeded what="add an item" />
    </Drawer>
  )
}
