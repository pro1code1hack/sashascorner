/**
 * One member, as a page (`#/members/<id>`), laid out like the order page: the
 * card and its ledger in the main column; contact, consent, the manual
 * adjustment and erasure in the side column.
 *
 * The stamp ledger is append-only (CONTRACT §2): an undo is its own row with
 * the opposite change, and a manual adjustment is a MANUAL_FIX row carrying the
 * reason and the manager who approved it. Nothing here edits history.
 *
 * Phase 3: a member may hold several cards (one per programme) -- "All cards" lists
 * them, the ledger and rewards say which card each row is on, and a correction picks
 * its card. "Till link" is the Lightspeed customer this member is: linked by email or
 * phone at sync, or by hand here; its receipts and what each earned are listed.
 */
import { useState } from 'react'
import { useQueryClient } from '@tanstack/react-query'
import { Button, ErrorBox, Field, Input, Loading, Pill, Textarea, cx } from '../../components/ui'
import { ago, dayFull, stamp } from '../../lib/format'
import { MEMBERS_KEY, membersApi, useMember, usePosCustomers } from '../../lib/members-api'
import { gbp } from '../../lib/format'
import { href, navigate } from '../../lib/router'
import type { MemberCard, MemberDetail, MemberDetailRow, MemberReward, ReferredBy } from '../../lib/types/members'
import {
  OutcomeLine,
  PIN_RE,
  Panel,
  PinInput,
  REASON_LABEL,
  REWARD_LABEL,
  StampStickers,
  WALLET_LABEL,
  birthdayText,
  signed,
  sourceLabel,
  useWriteState,
} from './shared'

export function MemberPage({ memberId }: { memberId: number }) {
  const q = useMember(memberId)
  const d = q.data
  return (
    <>
      <header className="flex flex-none flex-wrap items-center gap-x-3 gap-y-1 border-b border-line px-4 py-3 sm:px-5">
        <a href={href('/members')} className="text-base font-bold text-brand-ink no-underline hover:underline">
          ‹ Members
        </a>
        <span aria-hidden="true" className="text-ink-3">
          /
        </span>
        <h1 className="min-w-0 flex-1 truncate text-2xl font-extrabold tracking-[-.01em]">
          {d ? d.member.first_name : 'Member'} <span className="font-normal text-ink-3">#{memberId}</span>
        </h1>
        {d?.member.reward_available && <Pill tone="brand">Reward ready</Pill>}
      </header>
      <div className="min-h-0 flex-1 overflow-y-auto bg-canvas">
        <div className="px-4 pb-10 pt-5 sm:px-6 2xl:px-8">
          {q.isPending && <Loading what="Reading the card" />}
          {q.isError && <ErrorBox error={q.error} what={`member #${memberId}`} />}
          {d && (
            <div className="grid gap-5 compact:grid-cols-[minmax(0,1fr)_minmax(280px,360px)]">
              <div className="flex min-w-0 flex-col gap-5">
                <CardPanel d={d} />
                {(d.cards?.length ?? 0) > 1 && <CardsPanel cards={d.cards ?? []} />}
                <RewardsPanel rewards={d.rewards} names={programNames(d)} />
                <LedgerPanel d={d} />
              </div>
              <aside className="flex min-w-0 flex-col gap-5">
                <ConsentPanel m={d.member} />
                <TillPanel d={d} />
                <AdjustPanel d={d} />
                <ErasePanel d={d} />
              </aside>
            </div>
          )}
        </div>
      </div>
    </>
  )
}

/** slug -> programme name, only when the member holds more than one card. */
function programNames(d: MemberDetail): Record<string, string> | null {
  const cards = d.cards ?? []
  if (cards.length < 2) return null
  return Object.fromEntries(cards.map((c) => [c.program_slug, c.program_name]))
}

/* ----------------------------------------------------------------- card --- */

function CardPanel({ d }: { d: MemberDetail }) {
  const m = d.member
  const toGo = m.stamps_required - m.stamps_current
  const ready = d.rewards.filter((r) => r.redeemed_at === null && r.voided_at === null && (r.expires_at === null || Date.parse(r.expires_at) > Date.now()))
  return (
    <Panel title="The card" id="card-h" right={<span className="fig text-sm text-ink-2">card {m.card_id.slice(0, 8)}</span>}>
      <div className="flex flex-wrap items-end gap-x-6 gap-y-3">
        <div>
          <div className="fig text-3xl font-extrabold tracking-[-.01em]">
            {m.stamps_current}
            <span className="text-xl font-bold text-ink-2">/{m.stamps_required}</span>
          </div>
          <div className="mt-2">
            <StampStickers current={m.stamps_current} required={m.stamps_required} reward={ready.some((r) => r.kind === 'STAMP_CARD')} />
          </div>
        </div>
        <p className="min-w-0 flex-1 basis-56 text-base text-ink-2">
          {toGo > 0 ? `${toGo} more ${toGo === 1 ? 'stamp' : 'stamps'} for a free drink.` : 'A free drink at the next stamp.'}
          {ready.length > 0 && (
            <>
              {' '}
              <b className="text-ink">
                {ready.length} {ready.length === 1 ? 'reward' : 'rewards'} waiting:
              </b>{' '}
              {ready.map((r) => REWARD_LABEL[r.kind]).join(', ')}.
            </>
          )}
        </p>
      </div>
      <dl className="mt-4 grid grid-cols-2 gap-x-4 gap-y-2 text-base sm:grid-cols-4">
        <Fact label="Cards filled">{m.cycles_completed}</Fact>
        <Fact label="Rewards redeemed">{m.rewards_redeemed}</Fact>
        <Fact label="Member since">{dayFull(m.created_at)}</Fact>
        <Fact label="Last visit">
          <span title={stamp(m.last_activity_at)}>{ago(m.last_activity_at)}</span>
        </Fact>
        <Fact label="Wallet">{m.wallet === null ? <span className="text-ink-2">none yet</span> : WALLET_LABEL[m.wallet]}</Fact>
        <Fact label="Joined from">{sourceLabel(m.source)}</Fact>
      </dl>
    </Panel>
  )
}

/* ------------------------------------------------------------ all cards --- */

function CardsPanel({ cards }: { cards: MemberCard[] }) {
  return (
    <Panel title="All cards" id="cards-h" right={<span className="text-sm text-ink-2">one per programme</span>}>
      <ul className="flex flex-col">
        {cards.map((c) => {
          const points = c.program_kind === 'POINTS'
          return (
            <li key={c.card_id} className="flex flex-wrap items-center gap-x-4 gap-y-2 border-b border-line py-2.5 last:border-b-0">
              <div className="min-w-0 flex-1 basis-40">
                <div className="font-bold">{c.program_name}</div>
                <div className="fig text-sm text-ink-2">
                  card {c.card_id.slice(0, 8)} · since {dayFull(c.created_at)}
                  {c.voided ? ' · deleted' : ''}
                </div>
              </div>
              {points ? (
                <PointsBar current={c.stamps_current} required={c.stamps_required} />
              ) : (
                <StampStickers current={c.stamps_current} required={c.stamps_required} reward={c.reward_available} />
              )}
              <div className="fig ml-auto w-24 text-right text-md font-bold">
                {c.stamps_current}
                <span className="text-sm font-normal text-ink-2">
                  /{c.stamps_required} {points ? 'pts' : ''}
                </span>
              </div>
              {c.reward_available && <Pill tone="brand">Reward ready</Pill>}
            </li>
          )
        })}
      </ul>
    </Panel>
  )
}

/** A points balance against its target: a bar, with the figures in text beside it. */
function PointsBar({ current, required }: { current: number; required: number }) {
  const pct = Math.min(100, Math.round((current / Math.max(required, 1)) * 100))
  return (
    <span className="block h-2.5 w-40 overflow-hidden rounded-full border border-line-strong bg-surface" aria-hidden="true">
      <span className="block h-full bg-brand" style={{ width: `${pct}%` }} />
    </span>
  )
}

/* ------------------------------------------------------------ till link --- */

const LINK_SOURCE: Record<string, string> = {
  auto_email: 'matched by email at sync',
  auto_phone: 'matched by phone at sync',
  staff: 'linked at the till',
  back_office: 'linked here',
}

function TillPanel({ d }: { d: MemberDetail }) {
  const qc = useQueryClient()
  const m = d.member
  const link = d.lightspeed ?? null
  const [value, setValue] = useState('')
  const [asReceipt, setAsReceipt] = useState(false)
  const w = useWriteState()
  const recent = usePosCustomers(!link)
  const save = (res: { data: MemberDetail } | null) => {
    if (res) {
      qc.setQueryData([...MEMBERS_KEY, 'detail', m.member_id], res.data)
      void qc.invalidateQueries({ queryKey: [...MEMBERS_KEY, 'pos-customers'] })
      setValue('')
    }
  }
  const doLink = async (body: { customer_id?: string; receipt_id?: string }) =>
    save(await w.run(() => membersApi.link(m.member_id, body), (data) => `Linked to till customer ${data.lightspeed?.customer_id ?? ''}.`))
  return (
    <Panel title="Till link" id="till-h" right={<span className="text-sm text-ink-2">Lightspeed</span>}>
      {d.auto_stamp === false && (
        <p className="mb-3 rounded-button bg-canvas px-3 py-2 text-sm text-ink-2">
          Stamps from receipts are switched off on this server (<span className="fig">CAFEOPS_LOYALTY_AUTO_STAMP</span>). A link is kept, and
          receipts start counting when it is switched on.
        </p>
      )}
      {link ? (
        <>
          <dl className="grid grid-cols-[minmax(0,7.5rem)_minmax(0,1fr)] gap-x-3 gap-y-1.5 text-base">
            <dt className="text-ink-2">Customer</dt>
            <dd className="fig min-w-0 break-words">{link.customer_id}</dd>
            <dt className="text-ink-2">How</dt>
            <dd>
              {LINK_SOURCE[link.source ?? ''] ?? link.source ?? '—'}
              {link.linked_at ? <span className="text-ink-2">, {dayFull(link.linked_at)}</span> : null}
            </dd>
          </dl>
          <div className="mt-3 border-t border-line pt-3">
            <div className="mb-1 text-xs font-bold text-ink-2">Receipts</div>
            {link.receipts.length === 0 ? (
              <p className="text-sm text-ink-2">No synced receipt names this customer yet.</p>
            ) : (
              <ul className="flex flex-col text-sm">
                {link.receipts.map((r) => (
                  <li key={r.receipt_id} className="grid grid-cols-[minmax(0,1fr)_auto] gap-x-2 border-b border-line py-1.5 last:border-b-0">
                    <span className="fig min-w-0 truncate">{r.receipt_id}</span>
                    <span className="fig text-right">{gbp(r.total_pence)}</span>
                    <span className="fig text-ink-2">{stamp(r.closed_at)}</span>
                    <span className="text-right text-ink-2">{r.awards.length ? r.awards.join(', ') : 'nothing earned'}</span>
                  </li>
                ))}
              </ul>
            )}
          </div>
          <div className="mt-3">
            <Button
              variant="secondary"
              size="sm"
              pending={w.busy}
              pendingLabel="Unlinking…"
              onClick={async () => save(await w.run(() => membersApi.unlink(m.member_id), () => 'Unlinked. Stamps already given stay.'))}
            >
              Unlink
            </Button>
          </div>
        </>
      ) : (
        <>
          <p className="mb-3 text-sm text-ink-2">
            Not linked. A member links by themselves when a receipt’s customer has the same email or phone. Otherwise type the customer number
            from Lightspeed, or a receipt number that has them attached.
          </p>
          <form
            className="flex flex-col gap-2"
            onSubmit={(e) => {
              e.preventDefault()
              const v = value.trim()
              if (v) void doLink(asReceipt ? { receipt_id: v } : { customer_id: v })
            }}
          >
            <Field label={asReceipt ? 'Receipt number' : 'Customer number'}>
              <Input value={value} onChange={(e) => setValue(e.target.value)} className="fig" autoComplete="off" spellCheck={false} />
            </Field>
            <label className="flex items-center gap-2 text-sm">
              <input type="checkbox" checked={asReceipt} onChange={(e) => setAsReceipt(e.target.checked)} />
              It is a receipt number
            </label>
            <div>
              <Button type="submit" variant="primary" size="sm" disabled={!value.trim()} pending={w.busy} pendingLabel="Linking…">
                Link
              </Button>
            </div>
          </form>
          {(recent.data?.customers.length ?? 0) > 0 && (
            <div className="mt-3 border-t border-line pt-3">
              <div className="mb-1 text-xs font-bold text-ink-2">Unlinked till customers, last 14 days</div>
              <ul className="flex flex-col text-sm">
                {recent.data!.customers.slice(0, 8).map((c) => (
                  <li key={c.customer_id} className="flex items-center justify-between gap-2 border-b border-line py-1.5 last:border-b-0">
                    <span className="min-w-0">
                      <span className="font-semibold">{c.label ?? 'Customer'}</span> <span className="fig text-ink-2">{c.customer_id}</span>
                      <span className="block text-ink-2">
                        {c.receipts} {c.receipts === 1 ? 'receipt' : 'receipts'}, last {ago(c.last_at)}
                      </span>
                    </span>
                    <Button variant="link" onClick={() => void doLink({ customer_id: c.customer_id })}>
                      Link
                    </Button>
                  </li>
                ))}
              </ul>
            </div>
          )}
        </>
      )}
      <OutcomeLine outcome={w.outcome} className="mt-2" />
    </Panel>
  )
}

function Fact({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <div className="min-w-0">
      <dt className="text-xs font-bold text-ink-2">{label}</dt>
      <dd className="fig truncate">{children}</dd>
    </div>
  )
}

/* -------------------------------------------------------------- rewards --- */

function rewardState(r: MemberReward): { text: string; tone: 'brand' | 'muted' | 'neutral' } {
  if (r.voided_at) return { text: `Voided ${dayFull(r.voided_at)}`, tone: 'muted' }
  if (r.redeemed_at) return { text: 'Redeemed', tone: 'neutral' }
  if (r.expires_at && Date.parse(r.expires_at) <= Date.now()) return { text: 'Expired', tone: 'muted' }
  return { text: 'Ready', tone: 'brand' }
}

function RewardsPanel({ rewards, names }: { rewards: MemberReward[]; names: Record<string, string> | null }) {
  return (
    <Panel title="Rewards" id="rewards-h">
      {rewards.length === 0 ? (
        <p className="text-base text-ink-2">No rewards yet. One is issued every time the card fills.</p>
      ) : (
        <div role="table" aria-label="Rewards">
          <div role="row" className="hidden gap-3 border-b border-line-soft pb-2 text-label font-bold uppercase tracking-[.06em] text-ink-3 sm:grid sm:grid-cols-[minmax(0,1fr)_7.5rem_minmax(0,1.3fr)]">
            <span role="columnheader">Reward</span>
            <span role="columnheader">Issued</span>
            <span role="columnheader">State</span>
          </div>
          {rewards.map((r) => {
            const s = rewardState(r)
            return (
              <div
                role="row"
                key={r.id}
                className="grid grid-cols-[minmax(0,1fr)_auto] gap-x-3 gap-y-0.5 border-b border-line py-2 text-base last:border-b-0 sm:grid-cols-[minmax(0,1fr)_7.5rem_minmax(0,1.3fr)]"
              >
                <span role="cell" className="min-w-0 font-semibold">
                  {REWARD_LABEL[r.kind]}
                  {names && r.program_slug && <span className="block text-sm font-normal text-ink-2">{names[r.program_slug] ?? r.program_slug}</span>}
                </span>
                <span role="cell" className="fig text-right text-sm text-ink-2 sm:text-left sm:text-base sm:text-ink">
                  {dayFull(r.issued_at)}
                </span>
                <span role="cell" className="col-span-2 min-w-0 sm:col-span-1">
                  <Pill tone={s.tone}>{s.text}</Pill>
                  {r.redeemed_at ? (
                    <span className="mt-0.5 block text-sm text-ink-2">
                      <span className="fig">{stamp(r.redeemed_at)}</span> · {r.redeemed_option ? `${r.redeemed_option}: ` : ''}
                      {r.redeemed_item ?? 'item not recorded'}
                      {r.staff_name ? ` · ${r.staff_name}` : ''}
                    </span>
                  ) : (
                    r.expires_at &&
                    !r.voided_at && <span className="fig ml-2 text-sm text-ink-2">until {dayFull(r.expires_at)}</span>
                  )}
                </span>
              </div>
            )
          })}
        </div>
      )}
    </Panel>
  )
}

/* --------------------------------------------------------------- ledger --- */

function LedgerPanel({ d }: { d: MemberDetail }) {
  const names = programNames(d)
  const [all, setAll] = useState(false)
  const events = all ? d.events : d.events.slice(0, 25)
  return (
    <Panel
      title="Stamp history"
      id="ledger-h"
      right={<span className="text-sm text-ink-2">{d.events.length} {d.events.length === 1 ? 'entry' : 'entries'}, newest first</span>}
    >
      {d.events.length === 0 ? (
        <p className="text-base text-ink-2">No stamps yet.</p>
      ) : (
        <>
          <div role="table" aria-label="Stamp history">
            <div role="row" className="hidden gap-3 border-b border-line-soft pb-2 text-label font-bold uppercase tracking-[.06em] text-ink-3 sm:grid sm:grid-cols-[9.5rem_3rem_minmax(0,1fr)_minmax(0,9rem)]">
              <span role="columnheader">When</span>
              <span role="columnheader" className="text-right">
                Change
              </span>
              <span role="columnheader">Why</span>
              <span role="columnheader">By</span>
            </div>
            {events.map((e) => (
              <div
                role="row"
                key={e.id}
                className="grid grid-cols-[2.5rem_minmax(0,1fr)] gap-x-3 border-b border-line py-2 text-base last:border-b-0 sm:grid-cols-[9.5rem_3rem_minmax(0,1fr)_minmax(0,9rem)]"
              >
                <span role="cell" className="fig col-start-2 row-start-1 text-sm text-ink-2 sm:col-start-auto sm:row-start-auto sm:text-base sm:text-ink">
                  {stamp(e.created_at)}
                </span>
                <span role="cell" className="fig row-span-2 row-start-1 text-right text-md font-bold sm:row-span-1 sm:row-start-auto">
                  {signed(e.delta)}
                </span>
                <span role="cell" className="col-start-2 min-w-0 sm:col-start-auto">
                  {names && e.program_slug && e.program_slug !== 'stamp' ? `${names[e.program_slug] ?? e.program_slug}: ` : ''}
                  {REASON_LABEL[e.reason]}
                  {e.note && <span className="block text-sm text-ink-2">{e.note}</span>}
                </span>
                <span role="cell" className="col-start-2 min-w-0 text-sm text-ink-2 sm:col-start-auto sm:text-ink">
                  {e.staff_name ?? <span className="text-ink-2">{e.note?.startsWith('lightspeed:') ? 'till receipt' : 'automatic'}</span>}
                  {e.device_name && (
                    <span className="text-ink-2 sm:block">
                      <span className="sm:hidden"> · </span>
                      {e.device_name}
                    </span>
                  )}
                </span>
              </div>
            ))}
          </div>
          {d.events.length > 25 && (
            <Button variant="link" className="mt-2" onClick={() => setAll((a) => !a)}>
              {all ? 'Show the latest 25' : `Show all ${d.events.length}`}
            </Button>
          )}
        </>
      )}
    </Panel>
  )
}

/* -------------------------------------------------------------- consent --- */

function referredText(r: ReferredBy): { id: number; name: string } | null {
  if (r === null || r === undefined) return null
  if (typeof r === 'number') return { id: r, name: `Member #${r}` }
  return { id: r.member_id, name: r.first_name ?? `Member #${r.member_id}` }
}

function ConsentPanel({ m }: { m: MemberDetailRow }) {
  const ref = referredText(m.referred_by)
  return (
    <Panel title="Contact and consent" id="consent-h">
      <dl className="grid grid-cols-[minmax(0,7.5rem)_minmax(0,1fr)] gap-x-3 gap-y-1.5 text-base">
        <dt className="text-ink-2">Email</dt>
        <dd className="min-w-0 break-words">{m.email ?? <span className="text-ink-2">not given</span>}</dd>
        <dt className="text-ink-2">Phone</dt>
        <dd className="fig min-w-0">{m.phone ?? <span className="text-ink-2">not given</span>}</dd>
        <dt className="text-ink-2">Birthday</dt>
        <dd>{m.birthday ? birthdayText(m.birthday) : <span className="text-ink-2">not given</span>}</dd>
        <dt className="text-ink-2">Terms</dt>
        <dd>Accepted when joining, {dayFull(m.created_at)}</dd>
        {ref && (
          <>
            <dt className="text-ink-2">Referred by</dt>
            <dd>
              <a href={href(`/members/${ref.id}`)} className="text-brand-ink underline">
                {ref.name}
              </a>
            </dd>
          </>
        )}
      </dl>
      <div className="mt-3 border-t border-line pt-3 text-base">
        <div className="text-xs font-bold text-ink-2">Marketing messages</div>
        {m.marketing_opt_in ? (
          <p>
            <b>Opted in</b>
            {m.opt_in_at ? (
              <>
                {' '}
                on <span className="fig">{stamp(m.opt_in_at)}</span>
              </>
            ) : (
              <span className="text-ink-2"> (time not recorded)</span>
            )}
            {m.opt_in_source ? `, via ${m.opt_in_source}` : ''}.
          </p>
        ) : (
          <p>
            <b>Not opted in.</b> <span className="text-ink-2">Card updates only; no campaigns reach this member.</span>
          </p>
        )}
      </div>
    </Panel>
  )
}

/* --------------------------------------------------------------- adjust --- */

const DELTA_RE = /^[+\-−]?\d{1,2}$/

function parseDelta(t: string): number | null {
  const s = t.trim().replace('−', '-')
  if (!DELTA_RE.test(s)) return null
  const n = Number(s)
  return n === 0 ? null : n
}

function AdjustPanel({ d }: { d: MemberDetail }) {
  const qc = useQueryClient()
  const cards = (d.cards ?? []).filter((c) => !c.voided)
  const [program, setProgram] = useState('')
  const card = cards.find((c) => c.program_slug === program)
  const m = card
    ? { ...d.member, stamps_current: card.stamps_current, stamps_required: card.stamps_required }
    : d.member
  const [delta, setDelta] = useState('')
  const [reason, setReason] = useState('')
  const [pin, setPin] = useState('')
  const w = useWriteState()
  const n = parseDelta(delta)
  const deltaBad = delta.trim() !== '' && n === null
  const after = n === null ? null : m.stamps_current + n
  const belowZero = after !== null && after < 0
  const reasonShort = reason.trim().length > 0 && reason.trim().length < 5
  const ready = n !== null && !belowZero && reason.trim().length >= 5 && PIN_RE.test(pin)

  const submit = async () => {
    if (!ready || n === null) return
    const res = await w.run(
      () => membersApi.adjust(m.member_id, { delta: n, reason: reason.trim(), manager_pin: pin, ...(program ? { program } : {}) }),
      (data) => {
        const after = program ? data.cards?.find((c) => c.program_slug === program) : null
        return after
          ? `Done. The ${after.program_name} card now has ${after.stamps_current} of ${after.stamps_required}.`
          : `Done. The card now has ${data.member.stamps_current} of ${data.member.stamps_required}.`
      },
    )
    if (res) {
      qc.setQueryData([...MEMBERS_KEY, 'detail', m.member_id], res.data)
      void qc.invalidateQueries({ queryKey: [...MEMBERS_KEY, 'list'] })
      setDelta('')
      setReason('')
      setPin('')
    } else {
      setPin('')
    }
  }

  return (
    <Panel title="Correct the stamps" id="adjust-h">
      <p className="mb-3 text-sm text-ink-2">
        For mistakes past the scanner’s 2-minute undo. It adds a correction to the history with your reason; nothing is rewritten. A
        manager’s PIN approves it.
      </p>
      <form
        className="flex flex-col gap-3"
        onSubmit={(e) => {
          e.preventDefault()
          void submit()
        }}
      >
        {cards.length > 1 && (
          <Field label="Card">
            <select className="h-10 w-full rounded-button border border-line-strong bg-surface px-3 text-base" value={program} onChange={(e) => setProgram(e.target.value)}>
              {cards.map((c) => (
                <option key={c.card_id} value={c.program_slug === 'stamp' ? '' : c.program_slug}>
                  {c.program_name} ({c.stamps_current}/{c.stamps_required})
                </option>
              ))}
            </select>
          </Field>
        )}
        <Field
          label="Change"
          hint={after !== null && !belowZero ? `${m.stamps_current} → ${after} ${card?.program_kind === 'POINTS' ? 'points' : 'stamps'}` : 'e.g. +1 or −2'}
          error={deltaBad ? 'A whole number that is not zero, like +1 or −2.' : belowZero ? `The card only has ${m.stamps_current}.` : undefined}
        >
          <div className="w-28">
            <Input value={delta} onChange={(e) => setDelta(e.target.value)} className="fig" placeholder="+1" autoComplete="off" />
          </div>
        </Field>
        <Field label="Reason" error={reasonShort ? 'At least 5 characters: this is the record of why.' : undefined}>
          <Textarea value={reason} onChange={(e) => setReason(e.target.value)} placeholder="Missed a stamp on Saturday, till was busy" className="min-h-16" />
        </Field>
        <Field label="Manager PIN">
          <div className="w-36">
            <PinInput value={pin} onChange={setPin} />
          </div>
        </Field>
        <div>
          <Button type="submit" variant="primary" size="sm" disabled={!ready} pending={w.busy} pendingLabel="Saving…">
            Save correction
          </Button>
        </div>
        <OutcomeLine outcome={w.outcome} />
      </form>
    </Panel>
  )
}

/* ---------------------------------------------------------------- erase --- */

function ErasePanel({ d }: { d: MemberDetail }) {
  const qc = useQueryClient()
  const m = d.member
  const [open, setOpen] = useState(false)
  const w = useWriteState()
  const waiting = d.rewards.filter((r) => r.redeemed_at === null && r.voided_at === null).length
  return (
    <Panel title="Erase this member" id="erase-h">
      {!open ? (
        <>
          <p className="mb-3 text-sm text-ink-2">When somebody asks for their data to be deleted (UK GDPR). The same as “Delete my card” on their web card.</p>
          <Button variant="danger" onClick={() => setOpen(true)}>
            Erase {m.first_name}…
          </Button>
        </>
      ) : (
        <div className={cx('rounded-button border-[1.5px] border-dashed border-alert px-3 py-2.5')}>
          <p className="text-base">
            This removes {m.first_name}’s name, email, phone and birthday, voids the card
            {waiting > 0 ? ` and ${waiting} unredeemed ${waiting === 1 ? 'reward' : 'rewards'}` : ''}, and their wallet pass changes to “Card
            deleted”. <b>It cannot be undone.</b>
          </p>
          <div className="mt-3 flex flex-wrap gap-2">
            <Button
              variant="danger"
              pending={w.busy}
              pendingLabel="Erasing…"
              disabled={w.outcome?.tone === 'ok'}
              onClick={() => void w.run(() => membersApi.erase(m.member_id), () => 'Erased.')}
            >
              Erase for good
            </Button>
            <Button variant="secondary" onClick={() => setOpen(false)} disabled={w.busy}>
              Keep
            </Button>
          </div>
          <EraseOutcome
            outcome={w.outcome}
            onErased={() => {
              void qc.invalidateQueries({ queryKey: MEMBERS_KEY })
              navigate('/members')
            }}
          />
        </div>
      )}
    </Panel>
  )
}

function EraseOutcome({ outcome, onErased }: { outcome: ReturnType<typeof useWriteState>['outcome']; onErased: () => void }) {
  if (outcome?.tone === 'ok') {
    return (
      <p role="status" className="mt-2 text-sm text-ink-2">
        Erased.{' '}
        <button type="button" className="font-bold text-brand-ink underline" onClick={onErased}>
          Back to the list
        </button>
      </p>
    )
  }
  return <OutcomeLine outcome={outcome} className="mt-2" />
}
