/**
 * "Categories" drawer on the Menu items list (owner, 2026-09-29): the ops
 * categories are the shop's categories; the shop only adds presentation. Here
 * you set the order tiles appear in on the shop's front page (↑ ↓, one request
 * per move), whether a category shows online, the name customers see, the line
 * under it and its photo. Opening a row expands its editor inside the drawer.
 */
import { useMemo, useState } from 'react'
import { Button, Drawer, Empty, ErrorBox, Field, Input, Loading, Textarea, Toggle } from '../../components/ui'
import { LIVE } from '../../lib/api'
import { SHOP_KEY, catalogueWrites, useShopCatalogue } from '../../lib/shop-api'
import type { CategoryAdmin } from '../../lib/types/shop'
import { OutcomeLine, useWrite } from '../stock/writes'
import { MoveButtons, domId } from '../website/sitemenu-rows'
import { ShopPhotoSlot, Thumb, moved, plural } from './shared'

export function CategoriesDrawer({ open, onClose }: { open: boolean; onClose: () => void }) {
  return (
    <Drawer open={open} onClose={onClose} title="Categories" context="Order and look on the ordering site" width={460} compactWidth={400}>
      {LIVE ? <Body /> : <Empty>The shop's categories need the live API.</Empty>}
    </Drawer>
  )
}

function Body() {
  const cat = useShopCatalogue()
  const order = useWrite()
  const [openId, setOpenId] = useState<number | null>(null)
  const cats = useMemo(() => [...(cat.data?.categories ?? [])].sort((a, b) => a.sort_order - b.sort_order), [cat.data])
  const move = (from: number, to: number) => {
    if (to < 0 || to >= cats.length) return
    const c = cats[from]
    if (!c) return
    void order.run(() => catalogueWrites.categoriesOrder(moved(cats, from, to).map((x) => x.id)), { invalidate: [SHOP_KEY] })
    window.requestAnimationFrame(() => document.getElementById(`${domId('shc', String(c.id))}-${to < from ? 'up' : 'down'}`)?.focus())
  }
  if (cat.isPending) return <Loading what="Reading categories" />
  if (cat.isError) return <ErrorBox error={cat.error} what="the shop's categories" />
  return (
    <>
      <p className="text-sm text-ink-2">
        One tile per category on the shop's front page, in this order. A category comes from Menu items; give an item a new category there and it appears here.
      </p>
      <OutcomeLine outcome={order.outcome} />
      {cats.length === 0 ? (
        <Empty>No categories yet.</Empty>
      ) : (
        <ul className="flex flex-col">
          {cats.map((c, i) => (
            <CategoryRow key={c.id} c={c} open={openId === c.id} onToggle={() => setOpenId((id) => (id === c.id ? null : c.id))} mover={{ enabled: !order.pending, index: i, count: cats.length, move }} />
          ))}
        </ul>
      )}
    </>
  )
}

function CategoryRow({ c, open, onToggle, mover }: { c: CategoryAdmin; open: boolean; onToggle: () => void; mover: { enabled: boolean; index: number; count: number; move: (from: number, to: number) => void } }) {
  const w = useWrite()
  return (
    // A hidden category keeps full contrast; the sub-line says it is hidden.
    <li className="border-b border-line-row py-2 last:border-b-0">
      <div className="flex items-center gap-2.5">
        <button type="button" onClick={onToggle} aria-expanded={open} aria-label={`${open ? 'Close' : 'Edit'} ${c.name}`} className="rounded-control">
          <Thumb url={c.photo_url} className="size-9" />
        </button>
        <span className="min-w-0 flex-1">
          <button type="button" onClick={onToggle} aria-expanded={open} className="block max-w-full truncate text-left text-base font-bold text-ink hover:underline">
            {c.name}
          </button>
          <span className="block truncate text-xs text-ink-2">
            {plural(c.product_count, 'product')}
            {c.name !== c.ops_name ? ` · ${c.ops_name} in Menu items` : ''}
            {!c.visible ? ' · hidden online' : ''}
          </span>
        </span>
        <Toggle checked={c.visible} disabled={w.pending} onChange={(on) => void w.run(() => catalogueWrites.category(c.id, { visible: on }), { invalidate: [SHOP_KEY] })} label={<span className="sr-only">Shown online: {c.name}</span>} />
        <MoveButtons name={c.name} mover={mover} idBase={domId('shc', String(c.id))} />
      </div>
      <OutcomeLine outcome={w.outcome} className="mt-1" />
      {open && <Editor key={`${c.id}-${c.updated_at ?? ''}`} c={c} onClose={onToggle} />}
    </li>
  )
}

function Editor({ c, onClose }: { c: CategoryAdmin; onClose: () => void }) {
  const w = useWrite()
  const [name, setName] = useState(c.name)
  const [blurb, setBlurb] = useState(c.blurb ?? '')
  return (
    <div className="mt-2 flex flex-col gap-3 rounded-card bg-canvas-2 px-3 py-3">
      <ShopPhotoSlot url={c.photo_url} name={c.name} upload={(blob, by) => catalogueWrites.categoryPhoto(c.id, blob, by)} clear={() => catalogueWrites.categoryPhotoClear(c.id)} />
      <Field label="Name in the shop" hint={`In Menu items it is “${c.ops_name}”.`}>
        <Input size="sm" value={name} maxLength={80} onChange={(e) => setName(e.target.value)} />
      </Field>
      <Field label="Line under the heading">
        <Textarea rows={2} maxLength={300} className="min-h-0" value={blurb} onChange={(e) => setBlurb(e.target.value)} placeholder="No line under the heading" />
      </Field>
      <div className="flex items-center gap-2">
        <Button
          variant="primary"
          size="sm"
          disabled={name.trim() === ''}
          pending={w.pending}
          pendingLabel="Saving…"
          onClick={() => void w.run(() => catalogueWrites.category(c.id, { name: name.trim(), blurb: blurb.trim() || null }), { invalidate: [SHOP_KEY], ok: () => 'Saved.' })}
        >
          Save
        </Button>
        <Button variant="ghost" size="sm" onClick={onClose}>
          Close
        </Button>
        <OutcomeLine outcome={w.outcome} />
      </div>
    </div>
  )
}
