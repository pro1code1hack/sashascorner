/**
 * Loyalty card › one member (owner's design, 2026-09-28; docs/loyalty/BACKOFFICE-V2.md).
 *
 * Main column: the card as the customer sees it (tap a sticker to change it), the
 * one-tap actions (add a stamp, give the free drink, undo last) and what the card
 * has done, then the history. Side column: contact (editable), news and offers,
 * notes, the wallet and the card link, erase.
 *
 * Every write goes through the server and the page shows what the server sent
 * back; a refusal is shown in the server's own words. The ledger stays
 * append-only on the server: "Undo last" writes an undo row, it deletes nothing.
 */
import { useState } from 'react'
import type { ReactNode } from 'react'
import { useQueryClient } from '@tanstack/react-query'
import { Button, ConfirmTwiceButton, ErrorBox, Field, Input, Loading, Select, StatusLine, Textarea, cx } from '../../components/ui'
import type { Outcome } from '../../components/ui'
import {
  MEMBERS_KEY,
  downloadMemberData,
  loyaltyApi,
  useInvalidateLoyalty,
  useInvalidateLoyaltyLists,
  useLoyaltyMember,
} from '../../lib/loyalty-api'
import { navigate } from '../../lib/router'
import type { WriteResult } from '../../lib/api'
import type {
  HistoryEntry,
  LoyaltyMember,
  LoyaltyMemberDetail,
  MemberPatchIn,
  SendLinkOut,
  StickerKey,
} from '../../lib/types/loyalty'
import { LoyaltyHeader } from './LoyaltyHeader'
import { PhysicalCard } from './members/PhysicalCard'
import { MONTHS, SwitchRow, WALLET_NAME, birthdayText, phoneText, shortDate, since, sourceLabel } from './members/bits'
import { Panel } from './shared'
import { StickerImg } from './stickers'

type Busy = 'stamp' | 'reward' | 'undo' | 'sticker' | 'patch' | 'link' | 'erase' | null

export function MemberCardPage({ memberId }: { memberId: number }) {
  const q = useLoyaltyMember(memberId)
  const qc = useQueryClient()
  const invalidate = useInvalidateLoyalty()
  const refreshLists = useInvalidateLoyaltyLists()
  const [busy, setBusy] = useState<Busy>(null)
  const [busySlot, setBusySlot] = useState<number | null>(null)
  const [error, setError] = useState<{ where: Busy; text: string } | null>(null)

  /** Run a write that answers with the member; show it at once, then refresh the rest. */
  const run = async (what: Exclude<Busy, null>, call: () => Promise<WriteResult<LoyaltyMemberDetail>>): Promise<boolean> => {
    setBusy(what)
    setError(null)
    const r = await call()
    setBusy(null)
    setBusySlot(null)
    if (r.kind !== 'ok') {
      setError({ where: what, text: r.message })
      return false
    }
    // The answer is the member: show it at once; only the list and insights need a refetch.
    qc.setQueryData([...MEMBERS_KEY, 'v2-detail', memberId], r.data)
    void refreshLists()
    return true
  }

  const d = q.data
  const name = d?.member.first_name ?? 'Member'

  return (
    <>
      <LoyaltyHeader current="members" crumb={{ name, id: memberId }} />
      <div className="min-h-0 flex-1 overflow-y-auto bg-canvas">
        {q.isError ? (
          <ErrorBox error={q.error} what="this member" />
        ) : !d ? (
          <Loading what="Opening the card" />
        ) : (
          <div className="grid gap-4 px-4 pb-10 pt-4 sm:px-5 compact:grid-cols-[minmax(0,1fr)_minmax(250px,300px)] wide:grid-cols-[minmax(0,1fr)_340px] wide:gap-5 wide:px-6">
            <div className="flex min-w-0 flex-col gap-4 wide:gap-5">
              <CardPanel
                d={d}
                busy={busy}
                busySlot={busySlot}
                error={error && ['stamp', 'reward', 'undo', 'sticker'].includes(error.where ?? '') ? error.text : null}
                onStamp={() => void run('stamp', () => loyaltyApi.addStamp(memberId))}
                onReward={() => void run('reward', () => loyaltyApi.giveReward(memberId, d.card.reward_id ?? undefined))}
                onUndo={() => void run('undo', () => loyaltyApi.undoLast(memberId))}
                onPick={(slot, sticker) => {
                  setBusySlot(slot)
                  void run('sticker', () => loyaltyApi.setSticker(memberId, slot, sticker))
                }}
              />
              <HistoryPanel history={d.history} />
            </div>
            <aside className="flex min-w-0 flex-col gap-4 wide:gap-5">
              <ContactPanel
                m={d.member}
                busy={busy === 'patch'}
                error={error?.where === 'patch' ? error.text : null}
                onSave={(patch) => run('patch', () => loyaltyApi.patchMember(memberId, patch))}
              />
              <NotesPanel key={`notes-${memberId}`} m={d.member} onSave={(notes) => run('patch', () => loyaltyApi.patchMember(memberId, { notes }))} />
              <WalletPanel m={d.member} memberId={memberId} />
              <DeleteRow
                m={d.member}
                memberId={memberId}
                pending={busy === 'erase'}
                error={error?.where === 'erase' ? error.text : null}
                onErase={async () => {
                  setBusy('erase')
                  const r = await loyaltyApi.erase(memberId)
                  setBusy(null)
                  if (r.kind !== 'ok') {
                    setError({ where: 'erase', text: r.message })
                    return
                  }
                  await invalidate()
                  navigate('/loyalty')
                }}
              />
            </aside>
          </div>
        )}
      </div>
    </>
  )
}

/* ------------------------------------------------------------ the card --- */

function cardSentence(d: LoyaltyMemberDetail): string {
  const c = d.card
  if (c.voided) return 'This card has been deleted.'
  if (c.reward_available) return 'A free drink is waiting. Give it when they order, any drink.'
  if (c.stamps_current === 0) return 'A fresh card. Stamps come from the till scanner when they show the code.'
  const left = c.stamps_required - c.stamps_current
  return `${left} more stamp${left === 1 ? '' : 's'} for a free drink, any drink. Tap a sticker to change it.`
}

function CardPanel({
  d,
  busy,
  busySlot,
  error,
  onStamp,
  onReward,
  onUndo,
  onPick,
}: {
  d: LoyaltyMemberDetail
  busy: Busy
  busySlot: number | null
  error: string | null
  onStamp: () => void
  onReward: () => void
  onUndo: () => void
  onPick: (slot: number, sticker: StickerKey) => void
}) {
  const { member: m, card } = d
  const locked = busy !== null || card.voided
  return (
    <section
      aria-label="The card"
      className="flex min-w-0 flex-col gap-5 rounded-card-lg border border-line bg-surface px-4 py-4 sm:px-5 sm:py-5 wide:flex-row wide:items-start"
    >
      <PhysicalCard card={card} memberName={m.first_name} onPick={onPick} busySlot={busy === 'sticker' ? busySlot : null} />
      <div className="min-w-0 flex-1">
        <div className="flex items-baseline justify-between gap-3">
          <h2 className="text-lg font-extrabold tracking-[-.01em]">The card</h2>
          <span className="fig text-sm text-ink-3" title={card.card_id}>
            card {card.short_id}
          </span>
        </div>
        <p className="mt-1 text-base text-ink-2">{cardSentence(d)}</p>
        <div className="mt-3 flex flex-wrap gap-2">
          <Button variant="primary" onClick={onStamp} disabled={locked} pending={busy === 'stamp'} pendingLabel="Adding…">
            + Add a stamp
          </Button>
          <Button
            variant="secondary"
            onClick={onReward}
            disabled={locked || !card.reward_available}
            pending={busy === 'reward'}
            pendingLabel="Giving…"
          >
            Give the free drink
          </Button>
          <Button
            variant="secondary"
            onClick={onUndo}
            disabled={locked || !card.can_undo}
            pending={busy === 'undo'}
            pendingLabel="Undoing…"
          >
            Undo last
          </Button>
        </div>
        {card.can_undo && card.undo_label && <p className="mt-1.5 text-sm text-ink-2">Undo takes back {card.undo_label}.</p>}
        <StatusLine className="mt-2" outcome={error ? { kind: 'error', text: error } : null} />
        <dl className="mt-4 grid grid-cols-2 gap-x-6 gap-y-3">
          <Stat label="Cards filled" value={m.cycles_completed} />
          <Stat label="Free drinks" value={m.rewards_redeemed} />
          <Stat label="Visits a month" value={m.visits_per_month === null ? 'not known yet' : m.visits_per_month.toFixed(1)} />
          <Stat label="Last visit" value={m.last_visit_at ? since(m.last_visit_at) : 'none yet'} />
          <Stat label="Member since" value={shortDate(m.created_at)} />
          <Stat label="Wallet" value={m.wallet ? WALLET_NAME[m.wallet] : 'none yet'} />
        </dl>
      </div>
    </section>
  )
}

function Stat({ label, value }: { label: string; value: string | number }) {
  return (
    <div className="min-w-0">
      <dt className="text-sm text-ink-2">{label}</dt>
      <dd className="fig truncate text-md font-bold">{value}</dd>
    </div>
  )
}

/* ------------------------------------------------------------- history --- */

const SHOWN = 8

function HistoryIcon({ e }: { e: HistoryEntry }) {
  if (e.sticker) return <StickerImg sticker={e.sticker} size={30} className="size-[30px]" />
  const reward = e.kind === 'free_drink' || e.kind === 'birthday'
  const glyph: Record<string, string> = {
    free_drink: '★',
    birthday: '★',
    undo: '↺',
    correction: '±',
    expired: '×',
    joined: '+',
    referral: '♥',
    welcome: '+',
    paper: '✎',
    stamp: '•',
  }
  return (
    <span
      aria-hidden="true"
      className={cx(
        'grid size-[30px] place-items-center rounded-full text-md font-extrabold',
        reward ? 'bg-brand-wash text-brand-ink' : 'bg-wash text-ink-2',
      )}
    >
      {glyph[e.kind] ?? '•'}
    </span>
  )
}

function HistoryPanel({ history }: { history: HistoryEntry[] }) {
  const [all, setAll] = useState(false)
  const rows = all ? history : history.slice(0, SHOWN)
  return (
    <Panel
      title="History"
      right={
        <span className="fig text-sm text-ink-3">
          {history.length} {history.length === 1 ? 'entry' : 'entries'}, newest first
        </span>
      }
    >
      {history.length === 0 ? (
        <p className="text-base text-ink-2">Nothing yet.</p>
      ) : (
        <ol className="divide-y divide-line">
          {rows.map((e, i) => (
            <li key={`${e.at}-${i}`} className="flex items-center gap-3 py-2.5">
              <HistoryIcon e={e} />
              <div className="min-w-0 flex-1">
                <div className="font-semibold">{e.title}</div>
                {e.detail && <div className="truncate text-sm text-ink-2">{e.detail}</div>}
              </div>
              <time dateTime={e.at} title={new Date(e.at).toLocaleString('en-GB')} className="flex-none text-sm text-ink-2">
                {since(e.at)}
              </time>
            </li>
          ))}
        </ol>
      )}
      {history.length > SHOWN && (
        <Button variant="link" className="mt-2" onClick={() => setAll(!all)}>
          {all ? 'Show fewer' : `Show all ${history.length}`}
        </Button>
      )}
    </Panel>
  )
}

/* ------------------------------------------------------------- contact --- */

function Row({ label, children }: { label: string; children: ReactNode }) {
  return (
    <div className="grid grid-cols-[5.5rem_minmax(0,1fr)] gap-2 py-1">
      <dt className="text-base text-ink-2">{label}</dt>
      <dd className="min-w-0 break-words text-base">{children}</dd>
    </div>
  )
}

function ContactPanel({
  m,
  busy,
  error,
  onSave,
}: {
  m: LoyaltyMember
  busy: boolean
  error: string | null
  onSave: (patch: MemberPatchIn) => Promise<boolean>
}) {
  const [editing, setEditing] = useState(false)
  const optIn = m.marketing_opt_in
  return (
    <Panel
      title="Contact"
      right={
        !editing && (
          <Button variant="link" onClick={() => setEditing(true)} className="text-base font-bold text-brand-ink">
            Edit
          </Button>
        )
      }
    >
      {editing ? (
        <ContactForm
          m={m}
          busy={busy}
          error={error}
          onCancel={() => setEditing(false)}
          onSave={async (patch) => {
            if (Object.keys(patch).length === 0) {
              setEditing(false)
              return
            }
            if (await onSave(patch)) setEditing(false)
          }}
        />
      ) : (
        <dl>
          <Row label="Email">{m.email ?? <span className="text-ink-2">not given</span>}</Row>
          <Row label="Phone">{phoneText(m.phone) ?? <span className="text-ink-2">not given</span>}</Row>
          <Row label="Birthday">{m.birthday ? birthdayText(m.birthday) : <span className="text-ink-2">not given</span>}</Row>
          <Row label="Joined">
            {shortDate(m.created_at, 'always')} · {sourceLabel(m.source)}
          </Row>
          <Row label="Terms">{m.terms_accepted_at ? 'Accepted when joining' : <span className="text-ink-2">not recorded</span>}</Row>
        </dl>
      )}
      <div className="mt-3">
        <SwitchRow
          label="News and offers"
          hint={optIn ? 'Opted in. Gets promotions, max 2 a month.' : 'Not opted in. Card updates only.'}
          checked={optIn}
          disabled={busy}
          onChange={(next) => void onSave({ marketing_opt_in: next })}
        />
      </div>
      <StatusLine className="mt-2" outcome={!editing && error ? { kind: 'error', text: error } : null} />
    </Panel>
  )
}

function ContactForm({
  m,
  busy,
  error,
  onCancel,
  onSave,
}: {
  m: LoyaltyMember
  busy: boolean
  error: string | null
  onCancel: () => void
  onSave: (patch: MemberPatchIn) => Promise<void>
}) {
  const [bd, bm] = m.birthday ? m.birthday.split('-') : ['', '']
  const [name, setName] = useState(m.first_name)
  const [email, setEmail] = useState(m.email ?? '')
  const [phone, setPhone] = useState(phoneText(m.phone) ?? '')
  const [day, setDay] = useState(bd ? String(Number(bd)) : '')
  const [month, setMonth] = useState(bm ? String(Number(bm)) : '')
  const birthdayBad = (day === '') !== (month === '')

  const submit = () => {
    if (birthdayBad) return
    const patch: MemberPatchIn = {}
    if (name.trim() !== m.first_name) patch.first_name = name.trim()
    if ((email.trim() || null) !== m.email) patch.email = email.trim() || null
    if ((phone.trim() || null) !== phoneText(m.phone)) patch.phone = phone.trim() || null
    const birthday = day && month ? `${day.padStart(2, '0')}-${month.padStart(2, '0')}` : null
    if (birthday !== m.birthday) patch.birthday = birthday
    void onSave(patch)
  }

  return (
    <form
      className="flex flex-col gap-3"
      onSubmit={(e) => {
        e.preventDefault()
        submit()
      }}
    >
      <Field label="Name">
        <Input size="sm" value={name} maxLength={40} onChange={(e) => setName(e.target.value)} />
      </Field>
      <Field label="Email">
        <Input size="sm" type="email" value={email} onChange={(e) => setEmail(e.target.value)} />
      </Field>
      <Field label="Phone">
        <Input size="sm" type="tel" value={phone} onChange={(e) => setPhone(e.target.value)} />
      </Field>
      <div className="grid grid-cols-[4.5rem_1fr] items-start gap-2">
        <Field label="Birthday day" error={birthdayBad && month !== '' ? 'Add the day.' : undefined}>
          <Input size="sm" inputMode="numeric" maxLength={2} placeholder="Day" value={day} onChange={(e) => setDay(e.target.value.replace(/\D/g, ''))} />
        </Field>
        <Field label="Month" error={birthdayBad && day !== '' ? 'Pick the month, or clear the day.' : undefined}>
          <Select size="sm" value={month} onChange={(e) => setMonth(e.target.value)}>
            <option value="">Month</option>
            {MONTHS.map((mo, i) => (
              <option key={mo} value={String(i + 1)}>
                {mo}
              </option>
            ))}
          </Select>
        </Field>
      </div>
      <StatusLine outcome={error ? { kind: 'error', text: error } : null} />
      <div className="flex gap-2">
        <Button variant="primary" type="submit" size="sm" pending={busy} pendingLabel="Saving…" disabled={birthdayBad || name.trim() === ''}>
          Save
        </Button>
        <Button type="button" size="sm" variant="secondary" onClick={onCancel}>
          Cancel
        </Button>
      </div>
    </form>
  )
}

/* --------------------------------------------------------------- notes --- */

const NOTE_OUTCOME: Record<'idle' | 'saving' | 'saved' | 'failed', Outcome | null> = {
  idle: null,
  saving: { kind: 'info', text: 'Saving…' },
  saved: { kind: 'ok', text: 'Saved' },
  failed: { kind: 'error', text: 'Not saved. Click in the box and away to try again.' },
}

function NotesPanel({ m, onSave }: { m: LoyaltyMember; onSave: (notes: string | null) => Promise<boolean> }) {
  const [text, setText] = useState(m.notes ?? '')
  const [state, setState] = useState<'idle' | 'saving' | 'saved' | 'failed'>('idle')
  const save = async () => {
    const next = text.trim() || null
    if (next === (m.notes ?? null)) return
    setState('saving')
    setState((await onSave(next)) ? 'saved' : 'failed')
  }
  return (
    <Panel
      title="Notes"
      right={<StatusLine outcome={NOTE_OUTCOME[state]} />}
    >
      <Textarea
        aria-label="Notes about this member"
        rows={3}
        maxLength={1000}
        value={text}
        placeholder="Oat flat white, no lid. Comes in Tuesdays."
        onChange={(e) => {
          setText(e.target.value)
          if (state !== 'saving') setState('idle')
        }}
        onBlur={() => void save()}
      />
      <p className="mt-1 text-xs text-ink-2">Only staff see this. Saved when you click away.</p>
    </Panel>
  )
}

/* -------------------------------------------------------------- wallet --- */

function walletText(m: LoyaltyMember): string {
  if (m.wallet === 'apple') return 'Card is in Apple Wallet. Stamps and messages update on their lock screen.'
  if (m.wallet === 'google') return 'Card is in Google Wallet. Stamps and messages update on their lock screen.'
  const by = m.email ? 'by email' : m.phone ? 'by text' : 'nowhere yet'
  if (m.wallet === 'web') return `Opened the web card but has not added it to a wallet yet. Messages still arrive ${by}.`
  return 'Has not opened the card yet. Send them the link so it lands in their wallet.'
}

function WalletPanel({ m, memberId }: { m: LoyaltyMember; memberId: number }) {
  const [pending, setPending] = useState(false)
  const [result, setResult] = useState<SendLinkOut | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [copied, setCopied] = useState(false)
  const send = async () => {
    setPending(true)
    setError(null)
    setCopied(false)
    const r = await loyaltyApi.sendLink(memberId)
    setPending(false)
    if (r.kind !== 'ok') setError(r.message)
    else setResult(r.data)
  }
  return (
    <Panel title="Wallet">
      <p className="text-base text-ink-2">{walletText(m)}</p>
      <Button variant="secondary" className="mt-3 w-full" pending={pending} pendingLabel="Sending…" onClick={() => void send()}>
        Send the card link
      </Button>
      <StatusLine
        className="mt-2"
        outcome={error ? { kind: 'error', text: error } : result ? { kind: 'ok', text: copied ? `${result.message} Link copied.` : result.message } : null}
      />
      {result && (
        <div className="mt-1 text-sm">
          {result.delivery === 'none' && (
            <div className="mt-1.5 flex items-center gap-2">
              <code className="min-w-0 flex-1 truncate rounded-control bg-wash px-2 py-1 text-xs">{result.url}</code>
              <Button
                size="sm"
                variant="secondary"
                onClick={() => {
                  void navigator.clipboard?.writeText(result.url).then(() => setCopied(true))
                }}
              >
                {copied ? 'Copied' : 'Copy'}
              </Button>
            </div>
          )}
        </div>
      )}
    </Panel>
  )
}

/* -------------------------------------------------------------- delete --- */

function DeleteRow({
  m,
  memberId,
  pending,
  error,
  onErase,
}: {
  m: LoyaltyMember
  memberId: number
  pending: boolean
  error: string | null
  onErase: () => Promise<void>
}) {
  const [dlError, setDlError] = useState<string | null>(null)
  return (
    <div className="flex flex-col items-center gap-1 pt-1 text-center">
      <ConfirmTwiceButton
        variant="ghost"
        className="font-bold text-alert! hover:bg-alert-wash"
        armedLabel="Tap again to delete their card and details"
        pending={pending}
        pendingLabel="Deleting…"
        onConfirm={() => void onErase()}
      >
        Delete member
      </ConfirmTwiceButton>
      <Button
        variant="link"
        onClick={async () => {
          setDlError(await downloadMemberData(memberId, m.first_name))
        }}
      >
        Download their data
      </Button>
      <p className="max-w-[30ch] text-xs text-ink-2">
        Deleting erases their name and contact and voids the card. Stamp counts stay in the totals, with no name.
      </p>
      <StatusLine outcome={(error ?? dlError) ? { kind: 'error', text: error ?? dlError } : null} />
    </div>
  )
}
