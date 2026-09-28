/**
 * Members list: the Menu items list layout (CLAUDE.md §10). Search and sort
 * on top, the segments as chips under them, rows below with pagination.
 *
 * Every filter lives in the URL (`#/members?seg=lapsed_30&sort=stamps&page=2`)
 * so a reload or a shared link keeps it. Filtering and paging happen on the
 * server (`GET /api/members`); the list never pretends to know a count it did
 * not ask for.
 *
 * Phase 3: `?prog=<slug>` lists one programme's card holders (their card on it);
 * the selector only appears when the café runs more than one programme.
 */
import { useEffect, useState } from 'react'
import { Empty, ErrorBox, FilterChip, FilterChipRow, Loading, PageHeader, Pill, SearchInput, cx } from '../../components/ui'
import { ActiveFilters, FilterBar, FilterSelect } from '../../components/ui/FilterBar'
import { Pagination } from '../../components/ui/Pagination'
import { ago, dayFull } from '../../lib/format'
import { useMembers, usePrograms } from '../../lib/members-api'
import { href, navigate, useLocation } from '../../lib/router'
import type { MemberRow, MemberSegment, MemberSort } from '../../lib/types/members'
import { AlertStrip } from './Alerts'
import { MembersTabs, SEGMENT_LABEL, StampDots, WALLET_LABEL, contactOf, sourceLabel } from './shared'

const SEGMENTS: MemberSegment[] = ['all', 'reward_ready', 'lapsed_30', 'opted_in', 'new_30']
const SORT_LABEL: Record<MemberSort, string> = {
  recent: 'Sort: last visit',
  stamps: 'Sort: most stamps',
  name: 'Sort: name',
}
const PAGE_SIZES = [25, 50, 100] as const

const DEFAULTS = { q: '', seg: 'all', sort: 'recent', page: '1', per: '50', prog: '' } as const
type Filters = { -readonly [K in keyof typeof DEFAULTS]: string }

function readFilters(q: URLSearchParams): Filters {
  const f = { ...DEFAULTS } as Filters
  for (const k of Object.keys(DEFAULTS) as (keyof Filters)[]) {
    const v = q.get(k)
    if (v !== null) f[k] = v
  }
  return f
}

const asSegment = (s: string): MemberSegment => ((SEGMENTS as string[]).includes(s) ? (s as MemberSegment) : 'all')
const asSort = (s: string): MemberSort => (s === 'stamps' || s === 'name' ? s : 'recent')

export function MembersList() {
  const loc = useLocation()
  const fromUrl = readFilters(loc.query)
  // Typed into, so local first; the server query follows after a short pause.
  const [q, setQ] = useState(fromUrl.q)
  const [seenQ, setSeenQ] = useState(fromUrl.q)
  if (seenQ !== fromUrl.q) {
    setSeenQ(fromUrl.q)
    setQ(fromUrl.q)
  }
  const f: Filters = { ...fromUrl, q }

  const set = (patch: Partial<Filters>) => {
    const next: Filters = { ...f, ...patch }
    if (!('page' in patch)) next.page = '1'
    const query: Record<string, string> = {}
    for (const k of Object.keys(DEFAULTS) as (keyof Filters)[]) if (next[k] !== DEFAULTS[k]) query[k] = next[k]
    navigate('/members', { query, replace: true })
  }

  const [debounced, setDebounced] = useState(q)
  useEffect(() => {
    const t = window.setTimeout(() => setDebounced(q), 250)
    return () => window.clearTimeout(t)
  }, [q])

  const segment = asSegment(f.seg)
  const sort = asSort(f.sort)
  const per = (PAGE_SIZES as readonly number[]).includes(Number(f.per)) ? Number(f.per) : 50
  const page = Math.max(1, Number(f.page) || 1)
  const programs = usePrograms().data?.programs ?? []
  const prog = programs.find((p) => p.slug === f.prog && !p.is_default)
  const data = useMembers({ q: debounced, segment, sort, limit: per, offset: (page - 1) * per, program: prog?.slug })
  const rows = data.data?.members ?? []
  const total = data.data?.total ?? 0

  const chips = []
  if (f.q) chips.push({ key: 'q', label: `“${f.q}”`, onRemove: () => set({ q: '' }) })
  if (segment !== 'all') chips.push({ key: 'seg', label: SEGMENT_LABEL[segment], onRemove: () => set({ seg: 'all' }) })
  if (prog) chips.push({ key: 'prog', label: prog.name, onRemove: () => set({ prog: '' }) })
  const clearAll = () => set({ q: '', seg: 'all', prog: '' })

  return (
    <>
      <PageHeader title="Members" subtitle={<MembersTabs current="list" />} saved={data.isFetching ? 'Loading…' : undefined} />
      <div className="flex-none border-b border-line bg-surface px-4 pb-2.5 pt-3 sm:px-5">
        <FilterBar
          label="Search members"
          fold={false}
          search={
            <SearchInput
              label="Search members"
              placeholder="Name, email or phone"
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
              {programs.length > 1 && (
                <FilterSelect
                  label="Programme"
                  value={prog?.slug ?? ''}
                  allValue=""
                  onChange={(v) => set({ prog: v })}
                  options={programs.map((p) => ({ value: p.is_default ? '' : p.slug, label: p.is_default ? `Card: ${p.name}` : `Card: ${p.name}` }))}
                />
              )}
              <FilterSelect
              label="Sort"
              value={sort}
              allValue="recent"
              onChange={(v) => set({ sort: v })}
              options={(Object.keys(SORT_LABEL) as MemberSort[]).map((k) => ({ value: k, label: SORT_LABEL[k] }))}
            />
            </>
          }
        />
        <FilterChipRow label="Segment" scroll className="mt-2.5">
          {SEGMENTS.map((s) => (
            <FilterChip key={s} active={segment === s} onClick={() => set({ seg: s })} count={segment === s && data.data ? total : undefined}>
              {SEGMENT_LABEL[s]}
            </FilterChip>
          ))}
        </FilterChipRow>
        <ActiveFilters
          className="mt-2"
          chips={chips}
          onClearAll={clearAll}
          summary={data.data ? `${total} ${total === 1 ? 'member' : 'members'}` : undefined}
        />
      </div>

      <div className="min-h-0 flex-1 overflow-y-auto bg-canvas px-4 pb-6 pt-3.5 sm:px-5">
        <AlertStrip />
        {data.isLoading && <Loading what="Reading the members" />}
        {data.error && <ErrorBox error={data.error} what="the members" />}
        {data.data && rows.length === 0 && (
          <Empty roomy>
            {chips.length ? (
              <>
                Nobody matches.{' '}
                <button type="button" className="font-bold text-brand-ink underline" onClick={clearAll}>
                  Clear the filters
                </button>
              </>
            ) : (
              'No members yet. People join at /rewards from the QR codes on the tables and the till.'
            )}
          </Empty>
        )}
        {rows.length > 0 && <MemberRows rows={rows} />}
        {data.data && total > 0 && (
          <Pagination
            className="mt-4"
            page={page}
            pageSize={per}
            total={total}
            noun="members"
            sizes={PAGE_SIZES}
            onPage={(p) => set({ page: String(p) })}
            onPageSize={(n) => set({ per: String(n) })}
          />
        )}
      </div>
    </>
  )
}

const COLS = 'compact:grid-cols-[minmax(0,1fr)_128px_64px_80px_112px_128px_104px]'

function MemberRows({ rows }: { rows: MemberRow[] }) {
  return (
    <div className="mt-3.5 overflow-hidden rounded-card-lg bg-surface shadow-raised first:mt-0">
      <div
        className={cx(
          'hidden gap-3 border-b border-line px-3.5 py-2 text-label font-bold uppercase tracking-[.06em] text-ink-3 compact:grid',
          COLS,
        )}
      >
        <span>Member</span>
        <span>Stamps</span>
        <span className="text-right">Cycles</span>
        <span className="text-right">Redeemed</span>
        <span>Last visit</span>
        <span>Source</span>
        <span>Wallet</span>
      </div>
      <ul>
        {rows.map((m) => (
          <li key={m.member_id} className="border-b border-line-row last:border-b-0">
            <a
              href={href(`/members/${m.member_id}`)}
              className={cx(
                'grid grid-cols-[minmax(0,1fr)_auto] items-center gap-x-3 px-3.5 py-2.5 text-ink no-underline hover:bg-canvas-2',
                COLS,
              )}
            >
              <span className="min-w-0">
                <span className="flex items-center gap-2">
                  <span className="truncate text-md font-bold">{m.first_name}</span>
                  {m.reward_available && <Pill tone="brand">Reward ready</Pill>}
                </span>
                <span className="block truncate text-sm text-ink-2">
                  {contactOf(m)}
                  {!m.marketing_opt_in && ' · no marketing'}
                </span>
                <span className="block truncate text-sm text-ink-2 compact:hidden">
                  {m.cycles_completed} {m.cycles_completed === 1 ? 'card' : 'cards'} filled · {m.rewards_redeemed} redeemed ·{' '}
                  {sourceLabel(m.source)}
                </span>
              </span>
              <span className="flex flex-col items-end gap-1 compact:items-start">
                <span className="fig text-md">
                  <b>{m.stamps_current}</b>
                  <span className="text-ink-2">/{m.stamps_required}</span>
                </span>
                <StampDots current={m.stamps_current} required={m.stamps_required} />
                <span className="fig text-sm text-ink-2 compact:hidden" title={dayFull(m.last_activity_at)}>
                  {ago(m.last_activity_at)}
                </span>
              </span>
              <span className="fig hidden text-right text-base compact:block">{m.cycles_completed}</span>
              <span className="fig hidden text-right text-base compact:block">{m.rewards_redeemed}</span>
              <span className="fig hidden text-base compact:block" title={dayFull(m.last_activity_at)}>
                {ago(m.last_activity_at)}
              </span>
              <span className="hidden truncate text-base compact:block">{sourceLabel(m.source)}</span>
              <span className="hidden truncate text-base compact:block">
                {m.wallet === null ? <span className="text-sm text-ink-2">none yet</span> : WALLET_LABEL[m.wallet]}
              </span>
            </a>
          </li>
        ))}
      </ul>
      <p className="border-t border-line px-3.5 py-2 text-xs text-ink-2">
        Cycles: cards filled. Last visit: last stamp or reward. Wallet “none yet”: joined but has not added the pass or opened the web card.
      </p>
    </div>
  )
}
