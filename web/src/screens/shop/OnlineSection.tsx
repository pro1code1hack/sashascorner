/**
 * "Online ordering" on the Menu item page (owner, 2026-09-29: the shop menu and
 * the menu are one thing). Everything a customer sees about this product on the
 * ordering site, edited here and saved as one PUT to
 * `/api/shop-admin/products/by-menu-item/{id}` (contract §10.B.2). Names, sizes
 * and prices are the menu item's own, set above on the same page.
 *
 * `ShopPreview` (side column) shows the product as the shop's tile renders it,
 * with a link to it on the site.
 *
 * The public website's menu page reads the same rows (DECISIONS.md 29, "one
 * menu everywhere"): `description` is the item's line there, `visible` shows or
 * hides it, `featured` is its signature mark, `sort_order` its place.
 */
import { useMemo, useState } from 'react'
import { Button, Field, FilterChip, FilterChipRow, Input, Pill, Select, Textarea, Toggle, cx } from '../../components/ui'
import { LIVE } from '../../lib/api'
import { gbp } from '../../lib/format'
import { href } from '../../lib/router'
import { SHOP_KEY, catalogueWrites, useShopCatalogue, useShopProductByMenuItem } from '../../lib/shop-api'
import { ALLERGENS, ALLERGENS_NONE, DIETARY, NUTRITION_FIELDS } from '../../lib/types/shop'
import type { AllergensState, ProductByMenuItem, ProductPatch, SizeCode } from '../../lib/types/shop'
import { livePageUrl, useWebsiteConnection } from '../../lib/website-api'
import { PhotoView } from '../menu/Photo'
import { OutcomeLine, useWrite } from '../stock/writes'
import { intOrNull, plural } from './shared'

/** The website menu page's anchor for an item: the site slugs the name the same way (`sashasite.menu.slugify`). */
const menuAnchor = (name: string) =>
  name
    .toLowerCase()
    .replace(/&/g, ' and ')
    .replace(/[^a-z0-9]+/g, '-')
    .replace(/^-+|-+$/g, '')

const toggleIn = (list: string[], v: string) => (list.includes(v) ? list.filter((x) => x !== v) : [...list, v])
const vocab = (base: readonly string[], have: string[]) => [...base, ...have.filter((v) => !base.includes(v))].filter((v) => v !== ALLERGENS_NONE)

/** `domain/shop.allergens_state`, applied to the draft so the words follow the chips. */
function stateOf(list: string[]): AllergensState {
  if (list.length === 0) return 'unknown'
  if (list.length === 1 && list[0] === ALLERGENS_NONE) return 'none'
  return 'listed'
}

/** What the customer is told, for each state; the unknown one is the prompt to fix it. */
function allergensWords(state: AllergensState, list: string[]): string {
  switch (state) {
    case 'unknown':
      return 'Allergens aren’t listed yet; customers are told to ask at the counter.'
    case 'none':
      return 'Confirmed: no allergens. Customers see “No allergens”.'
    default:
      return `Customers see: contains ${list.filter((a) => a !== ALLERGENS_NONE).join(', ')}.`
  }
}

export function OnlineSection({ menuItemId, itemName }: { menuItemId: number; itemName: string }) {
  const q = useShopProductByMenuItem(menuItemId)
  if (!LIVE) return null
  return (
    <section aria-labelledby="online-h">
      <div className="mb-1 flex flex-wrap items-baseline justify-between gap-2">
        <h2 id="online-h" className="text-xl font-extrabold tracking-[-.01em]">
          Online ordering
        </h2>
        <a href={href('/shop/options')} className="text-sm text-ink-2 underline underline-offset-2">
          Option groups
        </a>
      </div>
      <p className="mb-3 text-sm text-ink-2">
        What customers see when they order {itemName} online, and on the website's menu page (the description, whether it is shown, and the signature mark are one setting for both). Sizes and prices are the ones above.
      </p>
      {q.isPending && <p className="text-sm text-ink-2">Reading the shop product…</p>}
      {q.isError && <p className="text-sm text-bad-ink">Couldn't read the shop product for this item.</p>}
      {q.data && <Editor key={`${q.data.id}-${q.data.updated_at ?? ''}`} p={q.data} />}
    </section>
  )
}

function Editor({ p }: { p: ProductByMenuItem }) {
  const w = useWrite()
  const cat = useShopCatalogue()
  const groups = useMemo(() => [...(cat.data?.option_groups ?? [])].sort((a, b) => a.sort_order - b.sort_order), [cat.data])
  const sizes = p.sizes.filter((s) => s.active)
  const sizeCodes: SizeCode[] = sizes.length ? sizes.map((s) => s.code) : p.sizes.map((s) => s.code)
  const byCategory = new Set(p.effective_option_group_ids.filter((id) => !p.option_group_ids.includes(id)))

  const [available, setAvailable] = useState(p.available)
  const [visible, setVisible] = useState(p.visible)
  const [featured, setFeatured] = useState(p.featured)
  const [badge, setBadge] = useState(p.badge ?? '')
  const [description, setDescription] = useState(p.description ?? '')
  const [note, setNote] = useState(p.note ?? '')
  const [kcalBySize, setKcalBySize] = useState<Record<string, string>>(() => Object.fromEntries(sizeCodes.map((c) => [c, p.kcal_by_size?.[c] === undefined ? '' : String(p.kcal_by_size[c])])))
  const [kcal, setKcal] = useState(p.kcal === null ? '' : String(p.kcal))
  const [allergens, setAllergens] = useState<string[]>(p.allergens)
  const [dietary, setDietary] = useState<string[]>(p.dietary)
  const [ingredients, setIngredients] = useState(p.ingredients_text ?? '')
  const [nutrition, setNutrition] = useState<Record<string, string>>(() => Object.fromEntries(NUTRITION_FIELDS.map(([k]) => [k, p.nutrition?.[k] ?? ''])))
  const [defaultSize, setDefaultSize] = useState<SizeCode | ''>(p.default_size ?? '')
  const [groupIds, setGroupIds] = useState<number[]>(p.option_group_ids)
  const [error, setError] = useState<string | null>(null)
  const [openMore, setOpenMore] = useState(Boolean(p.ingredients_text || (p.nutrition && Object.keys(p.nutrition).length)))

  const allergenVocab = vocab(cat.data?.allergens.length ? cat.data.allergens : ALLERGENS, allergens)
  const allergensState = stateOf(allergens)
  const dietaryVocab = vocab(cat.data?.dietary.length ? cat.data.dietary : DIETARY, dietary)

  const save = () => {
    const kbs: Partial<Record<SizeCode, number>> = {}
    for (const c of sizeCodes) {
      const v = intOrNull(kcalBySize[c] ?? '')
      if (v === undefined) {
        setError('Kcal per size is a whole number.')
        return
      }
      if (v !== null) kbs[c] = v
    }
    const kcalV = intOrNull(kcal)
    if (kcalV === undefined) {
      setError('Kcal is a whole number.')
      return
    }
    setError(null)
    const nut: Record<string, string> = {}
    for (const [k] of NUTRITION_FIELDS) {
      const v = (nutrition[k] ?? '').trim()
      if (v !== '') nut[k] = v
    }
    const patch: ProductPatch = {
      available,
      visible,
      featured,
      badge: badge.trim() || null,
      description: description.trim() || null,
      note: note.trim() || null,
      kcal: kcalV ?? null,
      kcal_by_size: Object.keys(kbs).length ? kbs : null,
      allergens,
      dietary,
      ingredients_text: ingredients.trim() || null,
      nutrition: Object.keys(nut).length ? nut : null,
      default_size: defaultSize || null,
      option_group_ids: groupIds,
    }
    void w.run(() => catalogueWrites.productByMenuItem(p.menu_item_id, patch), { invalidate: [SHOP_KEY], ok: () => 'Saved. The shop shows it within a minute.' })
  }

  return (
    <div className="flex flex-col gap-4">
      <div className="flex flex-wrap gap-x-5 gap-y-2">
        <Toggle checked={available} onChange={setAvailable} label={available ? 'On sale today' : 'Sold out today'} />
        <Toggle checked={visible} onChange={setVisible} label={visible ? 'Shown online and on the website menu' : 'Hidden online and on the website menu'} />
        <Toggle checked={featured} onChange={setFeatured} label="Featured · Signature on the website" />
      </div>
      {!p.ops_active && <p className="text-sm text-ink-2">This item is off the menu, so the shop does not list it whatever these say.</p>}
      {p.category_ops_name === null && <p className="text-sm text-ink-2">No category yet: the shop lists products by category, so this one stays hidden until it has one (set it above).</p>}

      <div className="grid gap-3 sm:grid-cols-[minmax(0,1fr)_180px]">
        <Field label="Description for customers" hint="Shown in the shop and under the item on the website menu.">
          <Textarea rows={2} maxLength={600} className="min-h-0" value={description} onChange={(e) => setDescription(e.target.value)} placeholder="What it is, in a sentence or two" />
        </Field>
        <Field label="Badge" hint="A sticker on the tile: “New”.">
          <Input value={badge} maxLength={30} onChange={(e) => setBadge(e.target.value)} />
        </Field>
      </div>
      <Field label="Caveat" hint="Shown in italics under the description: “Dairy-free option not available: the sauce contains dairy”.">
        <Input value={note} maxLength={300} onChange={(e) => setNote(e.target.value)} />
      </Field>

      <div className="grid gap-3 sm:grid-cols-2">
        <Field label="Kcal per size" hint="Size tiles show the difference from the size picked first.">
          <div className="flex flex-wrap gap-2">
            {sizeCodes.map((c) => (
              <label key={c} className="flex w-24 flex-col gap-1 text-xs text-ink-2">
                {p.sizes.find((s) => s.code === c)?.label || c}
                <Input size="sm" numeric inputMode="numeric" value={kcalBySize[c] ?? ''} onChange={(e) => setKcalBySize({ ...kcalBySize, [c]: e.target.value })} />
              </label>
            ))}
            {sizeCodes.length !== 1 && (
              <label className="flex w-24 flex-col gap-1 text-xs text-ink-2">
                Overall
                <Input size="sm" numeric inputMode="numeric" value={kcal} onChange={(e) => setKcal(e.target.value)} />
              </label>
            )}
          </div>
        </Field>
        <Field label="Size picked first" hint="Blank: the cheapest.">
          <Select value={defaultSize} onChange={(e) => setDefaultSize(e.target.value as SizeCode | '')}>
            <option value="">Cheapest</option>
            {sizeCodes.map((c) => (
              <option key={c} value={c}>
                {p.sizes.find((s) => s.code === c)?.label || c}
              </option>
            ))}
          </Select>
        </Field>
      </div>

      <FilterChipRow label="Allergens">
        <span className="w-full text-xs font-bold text-ink-2">Allergens</span>
        <FilterChip active={allergens.includes(ALLERGENS_NONE)} onClick={() => setAllergens(allergens.includes(ALLERGENS_NONE) ? [] : [ALLERGENS_NONE])}>
          None (confirmed)
        </FilterChip>
        {allergenVocab.map((a) => (
          <FilterChip key={a} active={allergens.includes(a)} onClick={() => setAllergens(toggleIn(allergens.filter((x) => x !== ALLERGENS_NONE), a))}>
            {a}
          </FilterChip>
        ))}
        <span className={cx('w-full text-sm', allergensState === 'unknown' ? 'font-semibold text-ink' : 'text-ink-2')}>
          {allergensWords(allergensState, allergens)}
          {allergensState !== (p.allergens_state ?? stateOf(p.allergens)) ? ' (after saving)' : ''}
        </span>
      </FilterChipRow>
      <FilterChipRow label="Dietary">
        <span className="w-full text-xs font-bold text-ink-2">Dietary</span>
        {dietaryVocab.map((d) => (
          <FilterChip key={d} active={dietary.includes(d)} onClick={() => setDietary(toggleIn(dietary, d))}>
            {d}
          </FilterChip>
        ))}
      </FilterChipRow>

      <FilterChipRow label="Option groups">
        <span className="w-full text-xs font-bold text-ink-2">Option groups on the item page</span>
        {groups.length === 0 && (
          <span className="text-sm text-ink-2">
            None yet. <a href={href('/shop/options')}>Create one</a>.
          </span>
        )}
        {groups.map((g) => {
          const auto = byCategory.has(g.id)
          const on = auto || groupIds.includes(g.id)
          return (
            <FilterChip key={g.id} active={on} onClick={() => !auto && setGroupIds((ids) => (ids.includes(g.id) ? ids.filter((x) => x !== g.id) : [...ids, g.id]))}>
              {g.name}
              {auto ? ' · by category' : ''}
              {!g.active ? ' · inactive' : ''}
            </FilterChip>
          )
        })}
        <span className="w-full text-xs text-ink-2">
          Groups marked “by category” apply to every product in this category; tick a group to add it to this product on its own. Edit them in{' '}
          <a href={href('/shop/options')}>Option groups</a>.
        </span>
      </FilterChipRow>

      <div>
        <Button variant="link" aria-expanded={openMore} onClick={() => setOpenMore((o) => !o)}>
          {openMore ? 'Hide ingredients and nutrition' : 'Ingredients and nutrition'}
        </Button>
        {openMore && (
          <div className="mt-2 grid gap-3 sm:grid-cols-2">
            <Field label="Ingredients" hint="Plain text on the Ingredients tab.">
              <Textarea rows={3} maxLength={600} className="min-h-0" value={ingredients} onChange={(e) => setIngredients(e.target.value)} />
            </Field>
            <Field label="Nutrition per serving" hint="A blank shows as “—”, never 0.">
              <div className="grid grid-cols-2 gap-2">
                {NUTRITION_FIELDS.map(([k, label]) => (
                  <label key={k} className="flex flex-col gap-1 text-xs text-ink-2">
                    {label}
                    <Input size="sm" numeric value={nutrition[k] ?? ''} onChange={(e) => setNutrition({ ...nutrition, [k]: e.target.value })} />
                  </label>
                ))}
              </div>
            </Field>
          </div>
        )}
      </div>

      <div className="flex flex-wrap items-center gap-3">
        <Button variant="primary" pending={w.pending} pendingLabel="Saving…" onClick={save}>
          Save online details
        </Button>
        {error && (
          <span role="alert" className="text-sm text-bad-ink">
            {error}
          </span>
        )}
        <OutcomeLine outcome={w.outcome} />
      </div>
    </div>
  )
}

/* ------------------------------------------------------------- preview --- */

/** The product as the shop's tile shows it, for the item page's side column. */
export function ShopPreview({ menuItemId }: { menuItemId: number }) {
  const q = useShopProductByMenuItem(menuItemId)
  const conn = useWebsiteConnection()
  if (!LIVE || !q.data) return null
  const p = q.data
  const url = livePageUrl(conn.data, p.shop_url_path) ?? p.shop_url_path
  const menuPath = `/menu#${menuAnchor(p.name)}`
  const menuUrl = livePageUrl(conn.data, menuPath) ?? menuPath
  const listed = p.visible && p.ops_active && p.category_ops_name !== null
  return (
    <section aria-labelledby="shop-preview-h" className="flex flex-col gap-2">
      <h3 id="shop-preview-h" className="text-label font-bold uppercase tracking-[.06em] text-ink-3">
        In the shop
      </h3>
      <div className={cx('overflow-hidden rounded-card border border-line bg-surface', !p.available && 'opacity-70')}>
        <div className="relative aspect-[4/3] w-full">
          <PhotoView url={p.photo_url} placeholder={p.name.charAt(0)} className="absolute inset-0" />
          {p.badge && <span className="absolute left-2 top-2 rounded-full bg-ink px-2 py-0.5 text-xs font-bold uppercase tracking-[.04em] text-white">{p.badge}</span>}
          {!p.available && <span className="absolute inset-x-0 bottom-0 bg-scrim px-2 py-1 text-center text-xs font-bold uppercase tracking-[.04em] text-white">Sold out</span>}
        </div>
        <div className="flex flex-col gap-0.5 px-3 py-2">
          <span className="text-md font-extrabold uppercase tracking-[.02em]">{p.name}</span>
          <span className="fig text-sm text-ink-2">
            {p.from_price_pence === null ? 'no price' : `${p.sizes.length > 1 ? 'from ' : ''}${gbp(p.from_price_pence)}`}
            {p.kcal !== null ? ` · ${p.kcal} kcal` : ''}
          </span>
        </div>
      </div>
      <p className="text-sm text-ink-2">
        {p.photo_url ? (p.photo_is_own ? 'Own shop photo.' : 'Uses the photo above.') : 'No photo yet: add one above.'}
        {p.photo_is_own && p.photo_url && <ResetPhoto id={p.id} />}
      </p>
      <div className="flex flex-wrap items-center gap-2 text-sm">
        {listed ? <Pill tone="brand">Listed</Pill> : <Pill tone="muted">Not listed</Pill>}
        {p.featured && <Pill tone="neutral">Featured</Pill>}
        <a href={url} target="_blank" rel="noreferrer" className="ml-auto underline underline-offset-2">
          View in the shop
        </a>
        <a href={menuUrl} target="_blank" rel="noreferrer" className="underline underline-offset-2">
          View on the website menu
        </a>
      </div>
      {p.effective_option_groups.length > 0 && <p className="text-sm text-ink-2">Options: {p.effective_option_groups.map((g) => g.name).join(', ')} · {plural(p.sizes.filter((s) => s.active).length, 'size')}</p>}
    </section>
  )
}

function ResetPhoto({ id }: { id: number }) {
  const w = useWrite()
  return (
    <>
      {' '}
      <Button variant="link" pending={w.pending} pendingLabel="Removing…" onClick={() => void w.run(() => catalogueWrites.productPhotoClear(id), { invalidate: [SHOP_KEY], ok: () => 'Using the menu photo.' })}>
        Use the menu photo instead
      </Button>
      <OutcomeLine outcome={w.outcome} />
    </>
  )
}
