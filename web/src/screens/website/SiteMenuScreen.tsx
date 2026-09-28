/**
 * Website › Menu: how the café's menu shows on the public website (moved here
 * from the website's own admin, owner 2026-09-28; site/ADMIN.md "Website menu").
 *
 * Names, sizes and prices are NOT edited here. They come from Café Ops (or, until
 * its menu has categories, the TV boards file) and link to Menu items. This screen
 * edits only the website's presentation: the order of sections and items, the line
 * under each heading, item descriptions, the Signature star, and what is hidden.
 * It also shows where the boards and Café Ops disagree.
 *
 * Reordering is up/down buttons (keyboard-operable). Several quick moves go as
 * one request; the whole order is always sent, as the site's endpoint expects.
 */
import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import type { ReactNode } from 'react'
import { useQuery, useQueryClient } from '@tanstack/react-query'
import {
  Button,
  ErrorBox,
  FilterChip,
  FilterChipRow,
  InfoPanel,
  Loading,
  PageBody,
  PageHeader,
  SearchInput,
  SectionHead,
  Segmented,
  WarnBox,
} from '../../components/ui'
import { LIVE } from '../../lib/api'
import { gbp } from '../../lib/format'
import {
  WEBSITE_KEY,
  livePageUrl,
  siteGet,
  siteWrite,
  useInvalidateWebsite,
  useWebsiteConnection,
} from '../../lib/website-api'
import { WebsiteGate } from './shared'
import { CategoryBlock, ItemRow, Prices, domId, type Mover, type Report, type SaveStatus } from './sitemenu-rows'
import {
  driftName,
  lacksDescription,
  opsMenuHref,
  plural,
  type OrderBody,
  type SiteMenuAdmin,
  type SiteMenuCategory,
  type SiteMenuItem,
} from './sitemenu-types'

const MENU_KEY = [...WEBSITE_KEY, 'menu'] as const

type Filter = 'all' | 'sig' | 'hidden' | 'nodesc'
type View = 'items' | 'cats'

export function SiteMenuScreen() {
  const conn = useWebsiteConnection()
  const live = livePageUrl(conn.data, '/menu')
  const [summary, setSummary] = useState<string>('')
  return (
    <>
      <PageHeader
        title="Website menu"
        subtitle="How the café’s menu shows on the public website."
        saved={summary ? <span aria-live="polite">{summary}</span> : undefined}
        actions={
          live ? (
            <a
              href={live}
              target="_blank"
              rel="noreferrer"
              className="inline-flex h-10 items-center rounded-control border border-line-control bg-surface px-3.5 text-base font-semibold hover:bg-canvas"
            >
              Open the live menu
            </a>
          ) : undefined
        }
      />
      <PageBody>
        <WebsiteGate>
          <MenuBody onSummary={setSummary} />
        </WebsiteGate>
      </PageBody>
    </>
  )
}

/* ------------------------------------------------------------ ordering --- */

function currentOrder(cats: SiteMenuCategory[]): OrderBody {
  return {
    categories: cats.map((c) => c.slug),
    items: Object.fromEntries(cats.map((c) => [c.slug, c.items.map((i) => i.key)])),
  }
}

/** The server's categories, in the locally-moved order (anything unlisted keeps its place after). */
function applyOrder(cats: SiteMenuCategory[], o: OrderBody | null): SiteMenuCategory[] {
  if (!o) return cats
  const rank = (list: string[], k: string) => {
    const i = list.indexOf(k)
    return i < 0 ? Number.MAX_SAFE_INTEGER : i
  }
  return [...cats]
    .map((c, n) => ({ c, n }))
    .sort((a, b) => rank(o.categories, a.c.slug) - rank(o.categories, b.c.slug) || a.n - b.n)
    .map(({ c }) => {
      const keys = o.items[c.slug]
      if (!keys) return c
      const items = [...c.items]
        .map((i, n) => ({ i, n }))
        .sort((a, b) => rank(keys, a.i.key) - rank(keys, b.i.key) || a.n - b.n)
        .map(({ i }) => i)
      return { ...c, items }
    })
}

function moved<T>(arr: T[], from: number, to: number): T[] {
  const out = [...arr]
  const [x] = out.splice(from, 1)
  if (x !== undefined) out.splice(to, 0, x)
  return out
}

/* ---------------------------------------------------------------- body --- */

function MenuBody({ onSummary }: { onSummary: (s: string) => void }) {
  const qc = useQueryClient()
  const invalidate = useInvalidateWebsite()
  const menu = useQuery({
    queryKey: MENU_KEY,
    queryFn: () => siteGet<SiteMenuAdmin>('/menu'),
    enabled: LIVE,
  })

  /* --- save states, summed for the header and the unload warning --- */
  const [statuses, setStatuses] = useState<Record<string, SaveStatus>>({})
  const [touched, setTouched] = useState(false)
  const report = useCallback<Report>((id, s) => {
    setStatuses((prev) => {
      const quiet = s === null || s === 'idle' || s === 'saved'
      if (quiet && !(id in prev)) return prev
      if (!quiet && prev[id] === s) return prev
      const next = { ...prev }
      if (quiet) delete next[id]
      else next[id] = s
      return next
    })
    if (s === 'saving') setTouched(true)
  }, [])

  /* --- order --- */
  const [override, setOverride] = useState<OrderBody | null>(null)
  const overrideRef = useRef<OrderBody | null>(null)
  overrideRef.current = override
  const [orderStatus, setOrderStatus] = useState<SaveStatus>('idle')
  const [orderMessage, setOrderMessage] = useState<string | null>(null)
  const orderTimer = useRef<number | null>(null)
  const [announcement, setAnnouncement] = useState('')

  useEffect(() => report('order', orderStatus), [orderStatus, report])
  useEffect(
    () => () => {
      if (orderTimer.current !== null) window.clearTimeout(orderTimer.current)
    },
    [],
  )

  const sendOrder = useCallback(async () => {
    const body = overrideRef.current
    if (!body) return
    setOrderStatus('saving')
    setOrderMessage(null)
    const r = await siteWrite<SiteMenuAdmin>('/menu/order', body)
    if (r.kind === 'ok') {
      qc.setQueryData(MENU_KEY, r.data)
      setOverride(null)
      await invalidate()
      setOrderStatus('saved')
      window.setTimeout(() => setOrderStatus((s) => (s === 'saved' ? 'idle' : s)), 2500)
    } else {
      setOrderMessage(r.message)
      setOrderStatus('failed')
    }
  }, [qc, invalidate])

  /* --- filters --- */
  const [search, setSearch] = useState('')
  const [filter, setFilter] = useState<Filter>('all')
  const [view, setView] = useState<View>('items')

  const data = menu.data
  const cats = useMemo(() => applyOrder(data?.categories ?? [], override), [data, override])

  const allItems = useMemo(() => cats.flatMap((c) => c.items), [cats])
  const counts = useMemo(
    () => ({
      all: allItems.length,
      sig: allItems.filter((i) => i.web.signature).length,
      hidden: allItems.filter((i) => i.web.hidden).length,
      nodesc: allItems.filter(lacksDescription).length,
    }),
    [allItems],
  )

  const needle = search.trim().toLowerCase()
  const filtering = needle !== '' || filter !== 'all'

  const visible = useMemo(() => {
    const matchesFilter = (it: SiteMenuItem) =>
      filter === 'sig'
        ? it.web.signature
        : filter === 'hidden'
          ? it.web.hidden
          : filter === 'nodesc'
            ? lacksDescription(it)
            : true
    const items = new Set<string>()
    const catsShown = new Set<string>()
    for (const c of cats) {
      const catHit = needle !== '' && c.name.toLowerCase().includes(needle)
      let any = 0
      for (const it of c.items) {
        if (matchesFilter(it) && (!needle || catHit || it.name.toLowerCase().includes(needle))) {
          items.add(it.key)
          any++
        }
      }
      if (!filtering || any > 0 || (catHit && filter === 'all')) catsShown.add(c.slug)
    }
    return { items, cats: catsShown, count: items.size }
  }, [cats, needle, filter, filtering])

  const canMove = !filtering && orderStatus !== 'saving'

  const scheduleOrderSave = () => {
    setOrderStatus('editing')
    if (orderTimer.current !== null) window.clearTimeout(orderTimer.current)
    orderTimer.current = window.setTimeout(() => void sendOrder(), 700)
  }

  /** After a move the row re-renders elsewhere; put focus back on the same button (or its twin at an end). */
  const refocus = (base: string, dir: 'up' | 'down') => {
    window.requestAnimationFrame(() => {
      const want = document.getElementById(`${base}-${dir}`) as HTMLButtonElement | null
      const twin = document.getElementById(`${base}-${dir === 'up' ? 'down' : 'up'}`) as HTMLButtonElement | null
      if (want && !want.disabled) want.focus()
      else twin?.focus()
    })
  }

  const moveCategory = (from: number, to: number) => {
    const o = currentOrder(cats)
    const c = cats[from]
    if (!c || to < 0 || to >= cats.length) return
    setOverride({ ...o, categories: moved(o.categories, from, to) })
    setAnnouncement(`${c.name} moved to ${to + 1} of ${cats.length}.`)
    refocus(domId('smc', c.slug), to < from ? 'up' : 'down')
    scheduleOrderSave()
  }

  const moveItem = (cat: SiteMenuCategory) => (from: number, to: number) => {
    const o = currentOrder(cats)
    const it = cat.items[from]
    const keys = o.items[cat.slug]
    if (!it || !keys || to < 0 || to >= keys.length) return
    setOverride({ ...o, items: { ...o.items, [cat.slug]: moved(keys, from, to) } })
    setAnnouncement(`${it.name} moved to ${to + 1} of ${keys.length}.`)
    refocus(domId('smi', it.key), to < from ? 'up' : 'down')
    scheduleOrderSave()
  }

  /* --- header summary + unload warning --- */
  const open = Object.values(statuses)
  const failed = open.filter((s) => s === 'failed').length
  const busy = open.some((s) => s === 'saving' || s === 'editing')
  const summaryText = failed
    ? `${plural(failed, 'change')} not saved`
    : busy
      ? 'Saving…'
      : touched
        ? 'All changes saved'
        : ''
  useEffect(() => onSummary(summaryText), [summaryText, onSummary])
  useEffect(() => () => onSummary(''), [onSummary])

  const unsaved = open.length > 0
  useEffect(() => {
    if (!unsaved) return
    const warn = (e: BeforeUnloadEvent) => {
      e.preventDefault()
      e.returnValue = ''
    }
    window.addEventListener('beforeunload', warn)
    return () => window.removeEventListener('beforeunload', warn)
  }, [unsaved])

  if (menu.isPending) return <Loading what="Loading the website menu" />
  if (menu.isError || !data) {
    return (
      <>
        <ErrorBox error={menu.error} what="the website menu" />
        <div className="px-5">
          <Button onClick={() => void menu.refetch()}>Try again</Button>
        </div>
      </>
    )
  }

  const d = data.drift
  const diffTotal = d.price_mismatches.length + d.board_only.length + d.ops_only.length
  const jump = (id: string) => {
    const el = document.getElementById(id)
    el?.scrollIntoView({ block: 'start' })
    el?.focus({ preventScroll: true })
  }

  return (
    <div className="flex flex-col gap-4">
      <SourcePanel data={data} />

      {(data.unassigned.length > 0 || diffTotal > 0) && (
        <p className="flex flex-wrap gap-x-4 gap-y-1">
          {data.unassigned.length > 0 && (
            <Button variant="link" className="min-h-11 text-base sm:min-h-0" onClick={() => jump('sm-unassigned')}>
              {plural(data.unassigned.length, 'item')} need a category in Café Ops
            </Button>
          )}
          {diffTotal > 0 && (
            <Button variant="link" className="min-h-11 text-base sm:min-h-0" onClick={() => jump('sm-diff')}>
              {plural(diffTotal, 'difference')} between the boards and Café Ops
            </Button>
          )}
        </p>
      )}

      <div className="grid grid-cols-1 gap-5 xl:grid-cols-[minmax(0,1fr)_360px]">
        <div className="flex min-w-0 flex-col gap-3">
          <div className="flex flex-wrap items-center gap-2.5">
            <SearchInput
              label="Find an item or section"
              placeholder="Find an item"
              value={search}
              onChange={(e) => setSearch(e.target.value)}
              className="w-full sm:w-64"
            />
            <FilterChipRow label="Show">
              <FilterChip active={filter === 'all'} onClick={() => setFilter('all')} count={counts.all}>
                All
              </FilterChip>
              <FilterChip active={filter === 'sig'} onClick={() => setFilter('sig')} count={counts.sig}>
                Signature
              </FilterChip>
              <FilterChip active={filter === 'hidden'} onClick={() => setFilter('hidden')} count={counts.hidden}>
                Hidden
              </FilterChip>
              <FilterChip active={filter === 'nodesc'} onClick={() => setFilter('nodesc')} count={counts.nodesc}>
                No description
              </FilterChip>
            </FilterChipRow>
            <Segmented<View>
              label="View"
              value={view}
              onChange={setView}
              options={[
                { value: 'items', label: 'Items' },
                { value: 'cats', label: 'Categories only' },
              ]}
            />
          </div>

          <div className="flex flex-wrap items-center gap-x-3 gap-y-1 text-sm text-ink-2">
            {filtering ? (
              <span role="status">{plural(visible.count, 'item')} match. Clear the search and filters to reorder.</span>
            ) : (
              <span>Use the ↑ ↓ buttons to change the order on the website. Everything saves by itself.</span>
            )}
            <OrderNote status={orderStatus} message={orderMessage} onRetry={() => void sendOrder()} />
          </div>
          <div className="sr-only" aria-live="polite">
            {announcement}
          </div>

          {cats.length === 0 ? (
            <p className="py-6 text-base text-ink-2">No categories yet.</p>
          ) : (
            <div className="flex flex-col gap-3">
              {cats.map((c, ci) => {
                const catMover: Mover = { enabled: canMove, index: ci, count: cats.length, move: moveCategory }
                const moveHere = moveItem(c)
                return (
                  <CategoryBlock
                    key={c.slug}
                    cat={c}
                    mover={catMover}
                    report={report}
                    compact={view === 'cats'}
                    shown={visible.cats.has(c.slug)}
                  >
                    {c.items.map((it, ii) => (
                      <ItemRow
                        key={it.key}
                        item={it}
                        report={report}
                        shown={visible.items.has(it.key)}
                        mover={{ enabled: canMove, index: ii, count: c.items.length, move: moveHere }}
                      />
                    ))}
                  </CategoryBlock>
                )
              })}
              {filtering && visible.cats.size === 0 && (
                <p className="py-6 text-base text-ink-2">Nothing matches. Clear the search or pick another filter.</p>
              )}
            </div>
          )}
        </div>

        <aside className="flex min-w-0 flex-col gap-5">
          {data.unassigned.length > 0 && <Unassigned items={data.unassigned} />}
          <Differences data={data} total={diffTotal} />
        </aside>
      </div>
    </div>
  )
}

function OrderNote({ status, message, onRetry }: { status: SaveStatus; message: string | null; onRetry: () => void }) {
  return (
    <span className="flex flex-wrap items-center gap-2" aria-live="polite">
      {status === 'editing' && <span>Order not saved yet…</span>}
      {status === 'saving' && <span>Saving the order…</span>}
      {status === 'saved' && <span>Order saved</span>}
      {status === 'failed' && (
        <>
          <span className="font-bold text-bad-ink">Order not saved{message ? `: ${message}` : ''}</span>
          <Button variant="outline" onClick={onRetry}>
            Retry
          </Button>
        </>
      )}
    </span>
  )
}

/* -------------------------------------------------------------- source --- */

function SourcePanel({ data }: { data: SiteMenuAdmin }) {
  return (
    <div className="flex flex-col gap-2">
      <InfoPanel>
        {data.source === 'ops' ? (
          <p>
            <strong>The website’s menu comes from Café Ops.</strong> Names, sizes and prices are the ones in{' '}
            <a href="#/menu" className="underline underline-offset-2">
              Menu items
            </a>
            ; change them there. Here you choose how the website shows them.
          </p>
        ) : (
          <p>
            <strong>The website is showing the TV boards file until the Café Ops menu is ready.</strong> Names, sizes and
            prices come from the boards for now; descriptions, stars, hiding and order are still set here. Once items in{' '}
            <a href="#/menu" className="underline underline-offset-2">
              Menu items
            </a>{' '}
            have categories, the website switches over and prices come from Café Ops.
          </p>
        )}
      </InfoPanel>
      {data.warnings.length > 0 && (
        <ul className="list-disc pl-5 text-sm text-ink-2">
          {data.warnings.map((w) => (
            <li key={w}>{w}</li>
          ))}
        </ul>
      )}
    </div>
  )
}

/* ---------------------------------------------------------- unassigned --- */

function Unassigned({ items }: { items: SiteMenuItem[] }) {
  return (
    <section id="sm-unassigned" tabIndex={-1} aria-labelledby="sm-unassigned-h" className="scroll-mt-4 outline-none">
      <SectionHead>
        <span id="sm-unassigned-h">
          No category in Café Ops · <span className="fig">{items.length}</span>
        </span>
      </SectionHead>
      <p className="mt-1 text-sm text-ink-2">
        The website can’t show these until they have a category. Give each one a category in Menu items; it then
        appears here in that category.
      </p>
      <ul className="mt-2 flex flex-col">
        {items.map((it) => (
          <li key={it.key} className="flex flex-col border-t border-line-soft py-2 first:border-t-0">
            <a href={opsMenuHref(it.name)} className="text-base font-bold underline underline-offset-2">
              {it.name}
            </a>
            <Prices item={it} />
          </li>
        ))}
      </ul>
    </section>
  )
}

/* --------------------------------------------------------- differences --- */

function Group({ title, n, children }: { title: string; n: number; children: ReactNode }) {
  // Long lists start folded.
  return (
    <details open={n <= 12} className="border-t border-line-soft py-2">
      <summary className="flex min-h-11 cursor-pointer items-center gap-2 text-base font-bold sm:min-h-8">
        {title} <span className="fig text-ink-2">{n}</span>
      </summary>
      {children}
    </details>
  )
}

function Differences({ data, total }: { data: SiteMenuAdmin; total: number }) {
  const d = data.drift
  const p = (v: number | null) => (v === null ? 'not listed' : gbp(v))
  return (
    <section id="sm-diff" tabIndex={-1} aria-labelledby="sm-diff-h" className="scroll-mt-4 outline-none">
      <SectionHead>
        <span id="sm-diff-h">
          Differences
          {total > 0 && (
            <>
              {' '}
              · <span className="fig">{total}</span>
            </>
          )}
        </span>
      </SectionHead>
      <p className="mt-1 text-sm text-ink-2">
        Where the TV boards and Café Ops don’t agree. The website shows the{' '}
        {data.source === 'ops' ? 'Café Ops' : 'boards'} version. Fix it in whichever one is wrong.
      </p>
      {!d.error && (
        <p className="fig mt-1 text-sm text-ink-2">
          {d.board_items} on the boards · {d.ops_items} in Café Ops · {d.matched} matched
        </p>
      )}
      <div className="mt-2">
        {d.error ? (
          <WarnBox>The comparison couldn’t run: {d.error}</WarnBox>
        ) : total === 0 ? (
          <p className="text-base text-ink-2">None. The boards and Café Ops agree.</p>
        ) : null}
        {d.price_mismatches.length > 0 && (
          <Group title="Different prices" n={d.price_mismatches.length}>
            <ul className="flex flex-col gap-1.5 pb-1">
              {d.price_mismatches.map((m) => (
                <li key={`${m.board_name}|${m.ops_name}|${m.size}`} className="text-base">
                  <a href={opsMenuHref(m.ops_name)} className="font-semibold underline underline-offset-2">
                    {m.board_name}
                  </a>
                  {m.size && m.size !== 'One' && <span className="font-semibold"> {m.size}</span>}
                  {m.ops_name.toLowerCase() !== m.board_name.toLowerCase() && (
                    <span className="text-ink-2"> (Café Ops: {m.ops_name})</span>
                  )}
                  <div className="fig text-sm text-bad-ink">
                    Boards {p(m.board_pence)} · Café Ops {p(m.ops_pence)}
                  </div>
                </li>
              ))}
            </ul>
          </Group>
        )}
        {d.board_only.length > 0 && (
          <Group title="On the boards, not in Café Ops" n={d.board_only.length}>
            <ul className="flex flex-col gap-0.5 pb-1 text-base">
              {d.board_only.map((n, i) => (
                <li key={`${n}|${i}`}>{driftName(n)}</li>
              ))}
            </ul>
          </Group>
        )}
        {d.ops_only.length > 0 && (
          <Group title="In Café Ops, not on the boards" n={d.ops_only.length}>
            <ul className="flex flex-col gap-0.5 pb-1 text-base">
              {d.ops_only.map((n, i) => (
                <li key={`${n}|${i}`}>
                  <a href={opsMenuHref(driftName(n))} className="underline underline-offset-2">
                    {driftName(n)}
                  </a>
                </li>
              ))}
            </ul>
          </Group>
        )}
      </div>
    </section>
  )
}
