/**
 * Loyalty card › Members (owner's design, 2026-09-28; docs/loyalty/BACKOFFICE-V2.md).
 *
 * Search, sort and "+ Add member" on top; the segments as chips with their sizes;
 * one row per member below. Every filter lives in the URL
 * (`#/loyalty?seg=reward_ready&sort=stamps&page=2`) so a reload keeps it. Filtering
 * and paging happen on the server; counts come from the server too.
 */
import { useEffect, useState } from 'react'
import {
  Button,
  Empty,
  ErrorBox,
  FilterChip,
  FilterChipRow,
  Loading,
  SearchInput,
  Select,
  Table,
  TBody,
  Td,
  Th,
  THead,
  Tr,
} from '../../components/ui'
import { Pagination } from '../../components/ui/Pagination'
import { useLoyaltyMembers } from '../../lib/loyalty-api'
import { href, navigate, useLocation } from '../../lib/router'
import type { LoyaltyMemberRow, LoyaltySegment, LoyaltySort } from '../../lib/types/loyalty'
import { LoyaltyHeader } from './LoyaltyHeader'
import { AddMemberDrawer } from './members/AddMember'
import { Avatar, StampDots, WalletPill, phoneText, since, sourceLabel } from './members/bits'

const SEGMENTS: { id: LoyaltySegment; label: string }[] = [
  { id: 'all', label: 'All' },
  { id: 'reward_ready', label: 'Reward ready' },
  { id: 'lapsed_30', label: 'Lapsed 30+ days' },
  { id: 'new_30', label: 'New, last 30 days' },
  { id: 'opted_in', label: 'Opted in' },
  { id: 'no_wallet', label: 'No wallet yet' },
]

const SORTS: { id: LoyaltySort; label: string }[] = [
  { id: 'recent', label: 'Sort: last visit' },
  { id: 'joined', label: 'Sort: joined' },
  { id: 'stamps', label: 'Sort: stamps' },
  { id: 'name', label: 'Sort: name' },
]

const PER = 50
const DEFAULTS = { q: '', seg: 'all', sort: 'recent', page: '1' } as const
type Filters = { -readonly [K in keyof typeof DEFAULTS]: string }

function readFilters(q: URLSearchParams): Filters {
  const f = { ...DEFAULTS } as Filters
  for (const k of Object.keys(DEFAULTS) as (keyof Filters)[]) {
    const v = q.get(k)
    if (v !== null) f[k] = v
  }
  return f
}

const asSegment = (s: string): LoyaltySegment => SEGMENTS.find((x) => x.id === s)?.id ?? 'all'
const asSort = (s: string): LoyaltySort => SORTS.find((x) => x.id === s)?.id ?? 'recent'

export function MembersScreen() {
  const loc = useLocation()
  const fromUrl = readFilters(loc.query)
  const [q, setQ] = useState(fromUrl.q)
  const [adding, setAdding] = useState(false)

  const set = (patch: Partial<Filters>) => {
    const next: Filters = { ...fromUrl, q, ...patch }
    if (!('page' in patch)) next.page = '1'
    const query: Record<string, string> = {}
    for (const k of Object.keys(DEFAULTS) as (keyof Filters)[]) if (next[k] !== DEFAULTS[k]) query[k] = next[k]
    navigate('/loyalty', { query, replace: true })
  }

  const [debounced, setDebounced] = useState(q)
  useEffect(() => {
    const t = window.setTimeout(() => setDebounced(q), 250)
    return () => window.clearTimeout(t)
  }, [q])

  const segment = asSegment(fromUrl.seg)
  const sort = asSort(fromUrl.sort)
  const page = Math.max(1, Number(fromUrl.page) || 1)
  const data = useLoyaltyMembers({ q: debounced, segment, sort, limit: PER, offset: (page - 1) * PER })
  const rows = data.data?.members ?? []
  const total = data.data?.total ?? 0
  const counts = data.data?.counts
  const filtered = q.trim() !== '' || segment !== 'all'

  return (
    <>
      <LoyaltyHeader current="members" />
      <div className="flex min-h-0 flex-1">
        <div className="min-h-0 min-w-0 flex-1 overflow-y-auto">
          <div className="flex flex-col gap-3 border-b border-line px-4 pb-3 pt-4 sm:px-5">
            <div className="flex flex-wrap items-center gap-2">
              <SearchInput
                label="Search members"
                placeholder="Name, email or phone"
                className="min-w-0 flex-[1_1_16rem]"
                value={q}
                onChange={(e) => {
                  setQ(e.target.value)
                  set({ q: e.target.value })
                }}
              />
              <div className="w-[10.5rem] flex-none">
                <Select aria-label="Sort members" value={sort} onChange={(e) => set({ sort: e.target.value })}>
                  {SORTS.map((s) => (
                    <option key={s.id} value={s.id}>
                      {s.label}
                    </option>
                  ))}
                </Select>
              </div>
              <Button variant="primary" className="flex-none" onClick={() => setAdding(true)}>
                + Add member
              </Button>
            </div>
            <div className="flex flex-wrap items-center gap-x-3 gap-y-2">
              <FilterChipRow label="Filter members" scroll className="min-w-0 flex-1">
                {SEGMENTS.map((s) => (
                  <FilterChip key={s.id} active={segment === s.id} count={counts ? counts[s.id] : undefined} onClick={() => set({ seg: s.id })}>
                    {s.label}
                  </FilterChip>
                ))}
              </FilterChipRow>
              {counts && (
                <span className="fig flex-none text-sm text-ink-2">
                  {total} of {counts.all} members
                </span>
              )}
            </div>
          </div>

          {data.isError ? (
            <ErrorBox error={data.error} what="the members" />
          ) : data.isPending ? (
            <Loading what="Loading members" />
          ) : rows.length === 0 ? (
            <Empty
              action={
                filtered ? (
                  <Button
                    variant="secondary"
                    onClick={() => {
                      setQ('')
                      navigate('/loyalty', { replace: true })
                    }}
                  >
                    Show everyone
                  </Button>
                ) : (
                  <Button variant="primary" onClick={() => setAdding(true)}>+ Add member</Button>
                )
              }
            >
              {filtered
                ? 'Nobody matches this.'
                : 'No members yet. They join by scanning the code on the counter, or you can add one here.'}
            </Empty>
          ) : (
            <>
              <MemberTable rows={rows} />
              <MemberStack rows={rows} />
              {total > PER && (
                <Pagination
                  className="px-4 py-3 sm:px-5"
                  page={page}
                  pageSize={PER}
                  total={total}
                  noun="members"
                  onPage={(p) => set({ page: String(p) })}
                />
              )}
            </>
          )}
        </div>
        <AddMemberDrawer open={adding} onClose={() => setAdding(false)} />
      </div>
    </>
  )
}

function contactLine(m: LoyaltyMemberRow): string {
  const who = m.email ?? phoneText(m.phone) ?? 'no contact'
  return m.marketing_opt_in ? `${who} · offers` : who
}

/** "5/8" and the dots. A free drink waiting reads as a full card (the dots say it; no green text). */
function StampsCell({ m }: { m: LoyaltyMemberRow }) {
  const shown = m.reward_available ? Math.max(m.stamps_current, m.stamps_required) : m.stamps_current
  return (
    <span className="inline-flex items-center gap-2 whitespace-nowrap">
      <span className="fig font-bold text-ink">
        {Math.min(shown, m.stamps_required)}/{m.stamps_required}
      </span>
      <StampDots current={shown} required={m.stamps_required} />
    </span>
  )
}

/** The table, from 900px. */
function MemberTable({ rows }: { rows: LoyaltyMemberRow[] }) {
  return (
    <div className="hidden px-5 pb-2 compact:block">
      <Table label="Members" minWidth={740}>
        <THead>
          <tr className="border-b border-line">
            <Th>Member</Th>
            <Th>Stamps</Th>
            <Th numeric>Cards</Th>
            <Th numeric>Rewards</Th>
            <Th>Last visit</Th>
            <Th>Joined from</Th>
            <Th>Wallet</Th>
          </tr>
        </THead>
        <TBody>
          {rows.map((m) => (
            <Tr key={m.member_id} onClick={() => navigate(`/loyalty/members/${m.member_id}`)} label={`Open ${m.first_name}`}>
              <Td className="py-2.5">
                <div className="flex min-w-0 items-center gap-3">
                  <Avatar name={m.first_name} id={m.member_id} />
                  <div className="min-w-0">
                    <a
                      href={href(`/loyalty/members/${m.member_id}`)}
                      onClick={(e) => e.stopPropagation()}
                      tabIndex={-1}
                      className="block truncate font-semibold text-ink no-underline hover:underline"
                    >
                      {m.first_name}
                    </a>
                    <div className="truncate text-sm text-ink-2">{contactLine(m)}</div>
                  </div>
                </div>
              </Td>
              <Td>
                <StampsCell m={m} />
              </Td>
              <Td numeric>{m.cycles_completed}</Td>
              <Td numeric>{m.rewards_redeemed}</Td>
              <Td className="whitespace-nowrap">
                {m.last_visit_at ? since(m.last_visit_at) : <span className="text-ink-3">not yet</span>}
              </Td>
              <Td className="whitespace-nowrap text-ink-2">{sourceLabel(m.source)}</Td>
              <Td>
                <WalletPill wallet={m.wallet} />
              </Td>
            </Tr>
          ))}
        </TBody>
      </Table>
    </div>
  )
}

/** Stacked rows under 900px (phone, iPad in portrait). */
function MemberStack({ rows }: { rows: LoyaltyMemberRow[] }) {
  return (
    <ul className="divide-y divide-line compact:hidden">
      {rows.map((m) => (
        <li key={m.member_id}>
          <a
            href={href(`/loyalty/members/${m.member_id}`)}
            className="flex items-start gap-3 px-4 py-3 text-ink no-underline hover:bg-canvas-2 sm:px-5"
          >
            <Avatar name={m.first_name} id={m.member_id} />
            <div className="min-w-0 flex-1">
              <div className="flex items-baseline justify-between gap-2">
                <span className="truncate font-semibold">{m.first_name}</span>
                <span className="flex-none text-sm text-ink-2">{m.last_visit_at ? since(m.last_visit_at) : 'not yet'}</span>
              </div>
              <div className="truncate text-sm text-ink-2">{contactLine(m)}</div>
              <div className="mt-1.5 flex flex-wrap items-center gap-x-3 gap-y-1 text-sm">
                <StampsCell m={m} />
                <span className="fig text-ink-2">
                  {m.cycles_completed} filled · {m.rewards_redeemed} free · {sourceLabel(m.source)}
                </span>
                <WalletPill wallet={m.wallet} />
              </div>
            </div>
          </a>
        </li>
      ))}
    </ul>
  )
}
