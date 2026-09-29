/**
 * Online orders › Banners & upsells (`#/shop/promos`): the promo carousel on the
 * shop's front page (photo, link, dates, on/off, order) and the upsell rows —
 * "Fancy a pastry?" on an item page, "Fancy a cake?" in the basket — each a
 * heading and a picked list of products. Drawers edit; one Save each.
 */
import { useMemo, useState } from 'react'
import {
  Button,
  Checkbox,
  ConfirmTwiceButton,
  Drawer,
  Empty,
  ErrorBox,
  Field,
  IconButton,
  Input,
  Loading,
  PageBody,
  PageHeader,
  Pill,
  SearchInput,
  Select,
  Toggle,
  cx,
} from '../../components/ui'
import { SHOP_KEY, catalogueWrites, useShopCatalogue } from '../../lib/shop-api'
import type { BannerAdmin, BannerIn, CatalogueAdmin, ProductAdmin, UpsellAdmin, UpsellIn, UpsellPlacement } from '../../lib/types/shop'
import { dayMonth, todayIso } from '../stock/fmt'
import { OutcomeLine, useWrite } from '../stock/writes'
import { MoveButtons, domId } from '../website/sitemenu-rows'
import { LIST_HEAD, ShopGate, ShopPhotoSlot, Thumb, moved, plural } from './shared'

type Open = { kind: 'banner'; id: number | 'new' } | { kind: 'upsell'; id: number | 'new'; placement?: UpsellPlacement } | null

const PLACEMENT_WORD: Record<UpsellPlacement, string> = { ITEM_PAGE: 'On an item page', BASKET: 'In the basket' }

export function PromosScreen() {
  const cat = useShopCatalogue()
  const [open, setOpen] = useState<Open>(null)
  const close = () => setOpen(null)
  const data = cat.data
  const banners = useMemo(() => [...(data?.banners ?? [])].sort((a, b) => a.sort_order - b.sort_order), [data])
  const upsells = useMemo(() => [...(data?.upsells ?? [])].sort((a, b) => a.sort_order - b.sort_order), [data])
  const banner = open?.kind === 'banner' && open.id !== 'new' ? (banners.find((b) => b.id === open.id) ?? null) : null
  const upsell = open?.kind === 'upsell' && open.id !== 'new' ? (upsells.find((u) => u.id === open.id) ?? null) : null

  return (
    <>
      <PageHeader title="Banners & upsells" subtitle="The promo carousel on the shop's front page, and the “fancy a…?” rows on item pages and in the basket." />
      <ShopGate>
        {cat.isPending && (
          <PageBody>
            <Loading what="Reading banners and upsells" />
          </PageBody>
        )}
        {cat.isError && (
          <PageBody>
            <ErrorBox error={cat.error} what="banners and upsells" />
          </PageBody>
        )}
        {data && (
          <div className="flex min-h-0 flex-1">
            <PageBody className="compact:px-5">
              <div className="flex flex-col gap-6">
                <Banners banners={banners} onOpen={(id) => setOpen({ kind: 'banner', id })} openId={open?.kind === 'banner' ? open.id : null} />
                <Upsells upsells={upsells} cat={data} onOpen={(id, placement) => setOpen({ kind: 'upsell', id, placement })} openId={open?.kind === 'upsell' ? open.id : null} />
              </div>
            </PageBody>
            <Drawer
              open={open !== null}
              onClose={close}
              title={open?.kind === 'banner' ? (banner?.title ?? 'New banner') : open?.kind === 'upsell' ? (upsell?.heading ?? 'New upsell') : ''}
              context={open?.kind === 'banner' ? 'Banner' : open?.kind === 'upsell' ? 'Upsell' : undefined}
              width={460}
              compactWidth={400}
            >
              {open?.kind === 'banner' && (
                <BannerEditor key={banner ? `${banner.id}` : 'new'} b={banner} onClose={close} onCreated={(b) => setOpen({ kind: 'banner', id: b.id })} />
              )}
              {open?.kind === 'upsell' && <UpsellEditor key={upsell ? `${upsell.id}` : 'new'} u={upsell} placement={open.placement} cat={data} onClose={close} />}
            </Drawer>
          </div>
        )}
      </ShopGate>
    </>
  )
}

/* ------------------------------------------------------------- banners --- */

const BCOLS = 'compact:grid-cols-[64px_minmax(0,1fr)_150px_110px_80px]'

function bannerWhen(b: BannerAdmin): { text: string; live: boolean } {
  const today = todayIso()
  const notYet = b.starts_on !== null && b.starts_on > today
  const over = b.ends_on !== null && b.ends_on < today
  if (!b.active) return { text: 'Off', live: false }
  if (notYet) return { text: `From ${dayMonth(b.starts_on)}`, live: false }
  if (over) return { text: `Ended ${dayMonth(b.ends_on)}`, live: false }
  if (b.ends_on) return { text: `Until ${dayMonth(b.ends_on)}`, live: true }
  return { text: 'Showing', live: true }
}

function Banners({ banners, onOpen, openId }: { banners: BannerAdmin[]; onOpen: (id: number | 'new') => void; openId: number | 'new' | null }) {
  const order = useWrite()
  const move = (from: number, to: number) => {
    if (to < 0 || to >= banners.length) return
    const b = banners[from]
    if (!b) return
    void order.run(() => catalogueWrites.bannersOrder(moved(banners, from, to).map((x) => x.id)), { invalidate: [SHOP_KEY] })
    window.requestAnimationFrame(() => document.getElementById(`${domId('shb', String(b.id))}-${to < from ? 'up' : 'down'}`)?.focus())
  }
  return (
    <section aria-labelledby="banners-h" className="flex flex-col gap-3">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <h2 id="banners-h" className="text-lg font-extrabold tracking-[-.01em]">
          Banners <span className="fig font-normal text-ink-2">{banners.length}</span>
        </h2>
        <Button onClick={() => onOpen('new')}>New banner</Button>
      </div>
      <OutcomeLine outcome={order.outcome} />
      {banners.length === 0 ? (
        <Empty>No banners. The carousel is hidden until there is one to show.</Empty>
      ) : (
        <div className="overflow-hidden rounded-card-lg bg-surface shadow-raised">
          {/* Visual column heads only; each row's cells carry their own hidden labels. */}
          <div className={cx(LIST_HEAD, BCOLS)} aria-hidden="true">
            <span />
            <span>Banner</span>
            <span>Link</span>
            <span>Showing</span>
            <span />
          </div>
          <ul>
            {banners.map((b, i) => {
              const when = bannerWhen(b)
              // A banner that is off keeps full contrast; the pill says when it shows.
              return (
                <li key={b.id} className={cx('grid grid-cols-[64px_minmax(0,1fr)_auto] items-center gap-x-3 gap-y-1 border-b border-line-row px-3.5 py-2 last:border-b-0', BCOLS, openId === b.id && 'bg-brand-wash')}>
                  <button type="button" onClick={() => onOpen(b.id)} aria-label={`Edit ${b.title}`} className="rounded-control">
                    <Thumb url={b.photo_url} className="h-9 w-16" />
                  </button>
                  <span className="min-w-0">
                    <button type="button" onClick={() => onOpen(b.id)} className="block max-w-full truncate text-left text-md font-bold text-ink hover:underline">
                      {b.title}
                    </button>
                    <span className="block truncate text-sm text-ink-2">{b.subtitle ?? 'No subtitle'}</span>
                    <span className="block truncate text-sm text-ink-2 compact:hidden">
                      {when.text}
                      {b.link_href ? ` · ${b.link_href}` : ''}
                    </span>
                  </span>
                  <span className="fig hidden min-w-0 truncate text-sm compact:block">
                    <span className="sr-only">Link </span>
                    {b.link_href ?? <span className="text-ink-2">no link</span>}
                  </span>
                  <span className="hidden compact:block">
                    <span className="sr-only">Showing </span>
                    <Pill tone={when.live ? 'brand' : 'muted'}>{when.text}</Pill>
                  </span>
                  <span className="col-start-3 row-start-1 flex justify-end compact:col-auto compact:row-auto">
                    <MoveButtons name={b.title} mover={{ enabled: !order.pending, index: i, count: banners.length, move }} idBase={domId('shb', String(b.id))} />
                  </span>
                </li>
              )
            })}
          </ul>
        </div>
      )}
    </section>
  )
}

/**
 * `onCreated` re-keys the drawer to the new banner once the server answers, so
 * the photo slot appears and a second Save updates rather than creating a twin.
 */
function BannerEditor({ b, onClose, onCreated }: { b: BannerAdmin | null; onClose: () => void; onCreated: (b: BannerAdmin) => void }) {
  const w = useWrite()
  const [title, setTitle] = useState(b?.title ?? '')
  const [subtitle, setSubtitle] = useState(b?.subtitle ?? '')
  const [link, setLink] = useState(b?.link_href ?? '')
  const [starts, setStarts] = useState(b?.starts_on ?? '')
  const [ends, setEnds] = useState(b?.ends_on ?? '')
  const [active, setActive] = useState(b?.active ?? true)
  const body: BannerIn = {
    title: title.trim(),
    subtitle: subtitle.trim() || null,
    link_href: link.trim() || null,
    starts_on: starts || null,
    ends_on: ends || null,
    active,
  }
  return (
    <>
      {b ? (
        <ShopPhotoSlot url={b.photo_url} name={b.title} upload={(blob, by) => catalogueWrites.bannerPhoto(b.id, blob, by)} clear={() => catalogueWrites.bannerPhotoClear(b.id)} />
      ) : (
        <p className="text-sm text-ink-2">Create the banner first, then add its photo.</p>
      )}
      <Toggle checked={active} onChange={setActive} label="Shown in the carousel" />
      <Field label="Title">
        <Input value={title} maxLength={120} onChange={(e) => setTitle(e.target.value)} placeholder="Pumpkin spice is back" />
      </Field>
      <Field label="Subtitle">
        <Input value={subtitle} maxLength={300} onChange={(e) => setSubtitle(e.target.value)} />
      </Field>
      <Field label="Link" hint="A shop path like /order/c/hot-drinks, or a page on the website.">
        <Input value={link} maxLength={300} onChange={(e) => setLink(e.target.value)} placeholder="/order/c/hot-drinks" />
      </Field>
      <div className="grid grid-cols-2 gap-3">
        <Field label="From" hint="Blank: straight away.">
          <Input type="date" value={starts} onChange={(e) => setStarts(e.target.value)} />
        </Field>
        <Field label="Until" hint="Blank: until switched off.">
          <Input type="date" value={ends} onChange={(e) => setEnds(e.target.value)} />
        </Field>
      </div>
      <div className="mt-auto flex flex-col gap-2">
        <OutcomeLine outcome={w.outcome} />
        <div className="flex flex-wrap gap-2">
          {b && (
            <ConfirmTwiceButton armedLabel="Tap again to delete" pending={w.pending} pendingLabel="Deleting…" onConfirm={() => void w.run(() => catalogueWrites.bannerDelete(b.id), { invalidate: [SHOP_KEY], after: onClose })}>
              Delete
            </ConfirmTwiceButton>
          )}
          <Button className="ml-auto" onClick={onClose}>
            Close
          </Button>
          <Button
            variant="primary"
            disabled={body.title === ''}
            pending={w.pending}
            pendingLabel="Saving…"
            onClick={() =>
              void w.run(() => (b ? catalogueWrites.bannerUpdate(b.id, body) : catalogueWrites.bannerCreate(body)), {
                invalidate: [SHOP_KEY],
                ok: () => (b ? 'Saved.' : 'Banner created. Add its photo next.'),
                after: (created) => {
                  if (!b) onCreated(created)
                },
              })
            }
          >
            {b ? 'Save' : 'Create banner'}
          </Button>
        </div>
      </div>
    </>
  )
}

/* ------------------------------------------------------------- upsells --- */

function Upsells({ upsells, cat, onOpen, openId }: { upsells: UpsellAdmin[]; cat: CatalogueAdmin; onOpen: (id: number | 'new', placement?: UpsellPlacement) => void; openId: number | 'new' | null }) {
  const nameOf = (id: number) => {
    const p = cat.products.find((x) => x.id === id)
    return p ? p.name : `#${id}`
  }
  return (
    <section aria-labelledby="upsells-h" className="flex flex-col gap-3">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <h2 id="upsells-h" className="text-lg font-extrabold tracking-[-.01em]">
          Upsells <span className="fig font-normal text-ink-2">{upsells.length}</span>
        </h2>
        <Button onClick={() => onOpen('new')}>New upsell</Button>
      </div>
      {(['ITEM_PAGE', 'BASKET'] as const).map((pl) => {
        const rows = upsells.filter((u) => u.placement === pl)
        return (
          <div key={pl} className="flex flex-col gap-1.5">
            <h3 className="text-label font-bold uppercase tracking-[.06em] text-ink-3">{PLACEMENT_WORD[pl]}</h3>
            {rows.length === 0 ? (
              <p className="text-sm text-ink-2">
                None here.{' '}
                <Button variant="link" onClick={() => onOpen('new', pl)}>
                  Add one
                </Button>
              </p>
            ) : (
              <ul className="overflow-hidden rounded-card-lg bg-surface shadow-raised">
                {rows.map((u) => (
                  <li key={u.id} className={cx('flex items-center gap-3 border-b border-line-row px-3.5 py-2.5 last:border-b-0', openId === u.id && 'bg-brand-wash')}>
                    <span className="min-w-0 flex-1">
                      <button type="button" onClick={() => onOpen(u.id)} className="block max-w-full truncate text-left text-md font-bold text-ink hover:underline">
                        {u.heading}
                      </button>
                      <span className="block truncate text-sm text-ink-2">{u.product_ids.length === 0 ? 'No products picked' : u.product_ids.map(nameOf).join(', ')}</span>
                    </span>
                    <Pill tone={u.active ? 'brand' : 'muted'}>{u.active ? `${plural(u.product_ids.length, 'product')}` : 'Off'}</Pill>
                  </li>
                ))}
              </ul>
            )}
          </div>
        )
      })}
    </section>
  )
}

function UpsellEditor({ u, placement, cat, onClose }: { u: UpsellAdmin | null; placement?: UpsellPlacement; cat: CatalogueAdmin; onClose: () => void }) {
  const w = useWrite()
  const [heading, setHeading] = useState(u?.heading ?? '')
  const [pl, setPl] = useState<UpsellPlacement>(u?.placement ?? placement ?? 'BASKET')
  const [active, setActive] = useState(u?.active ?? true)
  const [ids, setIds] = useState<number[]>(u?.product_ids ?? [])
  const [q, setQ] = useState('')
  const [catFilter, setCatFilter] = useState('all')
  const byId = useMemo(() => new Map(cat.products.map((p) => [p.id, p])), [cat.products])
  const needle = q.trim().toLowerCase()
  const candidates = useMemo(
    () =>
      cat.products
        .filter((p) => p.visible && (catFilter === 'all' || p.category_ops_name === catFilter))
        .filter((p) => !needle || `${p.item_name} ${p.display_name ?? ''}`.toLowerCase().includes(needle))
        .sort((a, b) => a.item_name.localeCompare(b.item_name)),
    [cat.products, catFilter, needle],
  )
  const name = (p: ProductAdmin) => p.name
  const body: UpsellIn = { placement: pl, heading: heading.trim(), product_ids: ids, active }
  return (
    <>
      <Toggle checked={active} onChange={setActive} label="Shown to customers" />
      <Field label="Heading">
        <Input value={heading} maxLength={80} onChange={(e) => setHeading(e.target.value)} placeholder="Fancy a pastry?" />
      </Field>
      <Field label="Where it shows">
        <Select value={pl} onChange={(e) => setPl(e.target.value as UpsellPlacement)}>
          <option value="ITEM_PAGE">On an item page, under the options</option>
          <option value="BASKET">In the basket, above checkout</option>
        </Select>
      </Field>

      <div className="flex flex-col gap-1.5">
        <span className="text-xs font-bold text-ink-2">Picked products, in order</span>
        {ids.length === 0 ? (
          <p className="text-sm text-ink-2">Nothing picked yet. Tick products below.</p>
        ) : (
          <ul className="flex flex-col gap-1">
            {ids.map((id, i) => {
              const p = byId.get(id)
              const label = p ? name(p) : `#${id}`
              return (
                <li key={id} className="flex items-center gap-2 rounded-control border border-line-soft px-2.5 py-1">
                  <Thumb url={p?.photo_url ?? null} className="size-8" />
                  <span className="min-w-0 flex-1 truncate text-base">
                    {label}
                    {p && !p.available && <span className="text-sm text-ink-2"> · sold out today</span>}
                  </span>
                  <MoveButtons name={label} mover={{ enabled: true, index: i, count: ids.length, move: (from, to) => setIds((s) => moved(s, from, to)) }} idBase={domId('shu', String(id))} />
                  <IconButton label={`Remove ${label}`} onClick={() => setIds((s) => s.filter((x) => x !== id))} />
                </li>
              )
            })}
          </ul>
        )}
      </div>

      <div className="flex flex-col gap-2">
        <span className="text-xs font-bold text-ink-2">Pick products</span>
        <div className="flex flex-wrap gap-2">
          <SearchInput label="Find a product" placeholder="Find a product" value={q} onChange={(e) => setQ(e.target.value)} className="min-w-0 flex-1" />
          <Select value={catFilter} onChange={(e) => setCatFilter(e.target.value)} className="w-auto">
            <option value="all">All categories</option>
            {cat.categories.map((c) => (
              <option key={c.id} value={c.ops_name}>
                {c.name}
              </option>
            ))}
          </Select>
        </div>
        <ul className="max-h-72 overflow-y-auto rounded-control border border-line-soft">
          {candidates.length === 0 && <li className="px-2.5 py-2 text-sm text-ink-2">Nothing matches.</li>}
          {candidates.map((p) => (
            <li key={p.id} className="border-b border-line-row px-2.5 py-1 last:border-b-0">
              <Checkbox
                checked={ids.includes(p.id)}
                onChange={(on) => setIds((s) => (on ? [...s, p.id] : s.filter((x) => x !== p.id)))}
                label={
                  <>
                    {name(p)}
                    <span className="text-sm text-ink-2"> · {cat.categories.find((c) => c.ops_name === p.category_ops_name)?.name ?? 'no category'}</span>
                  </>
                }
                className="min-h-9 w-full"
              />
            </li>
          ))}
        </ul>
      </div>

      <div className="mt-auto flex flex-col gap-2">
        <OutcomeLine outcome={w.outcome} />
        <div className="flex flex-wrap gap-2">
          {u && (
            <ConfirmTwiceButton armedLabel="Tap again to delete" pending={w.pending} pendingLabel="Deleting…" onConfirm={() => void w.run(() => catalogueWrites.upsellDelete(u.id), { invalidate: [SHOP_KEY], after: onClose })}>
              Delete
            </ConfirmTwiceButton>
          )}
          <Button className="ml-auto" onClick={onClose}>
            Close
          </Button>
          <Button
            variant="primary"
            disabled={body.heading === ''}
            pending={w.pending}
            pendingLabel="Saving…"
            onClick={() =>
              void w.run(() => (u ? catalogueWrites.upsellUpdate(u.id, body) : catalogueWrites.upsellCreate(body)), {
                invalidate: [SHOP_KEY],
                ok: () => (u ? 'Saved.' : 'Upsell created.'),
                after: () => {
                  if (!u) onClose()
                },
              })
            }
          >
            {u ? 'Save' : 'Create upsell'}
          </Button>
        </div>
      </div>
    </>
  )
}
