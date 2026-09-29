/**
 * Website › Messages: the contact form's inbox (site/web/src/pages/admin/messages.astro),
 * moved into the back office (owner, 2026-09-28). New / Handled / Archived.
 *
 * One read of every message (`status=all`) so the three chips can show their counts;
 * each message opens in place with Reply by email, Call (when the text carries a UK
 * number) and the moves between the three piles, each with Undo.
 */
import { useEffect, useRef, useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import { Button, Empty, ErrorBox, FilterChip, FilterChipRow, LinkButton, Loading, PageBody, PageHeader, Pill, StatusLine } from '../../components/ui'
import type { Outcome } from '../../components/ui'
import { ago, stamp } from '../../lib/format'
import { navigate, useLocation } from '../../lib/router'
import type { Message, MessageStatus } from '../../lib/types/website'
import { WEBSITE_KEY, siteGet, siteWrite, useInvalidateWebsite } from '../../lib/website-api'
import { WebsiteGate } from './shared'

/* ------------------------------------------------------------- words --- */

const TOPIC: Record<string, string> = {
  general: 'General',
  order: 'Cake order',
  events: 'Events',
  feedback: 'Feedback',
  press: 'Press',
  jobs: 'Jobs',
}
const topicLabel = (t: string) => TOPIC[t] ?? t.charAt(0).toUpperCase() + t.slice(1)

const TABS: { id: MessageStatus; label: string }[] = [
  { id: 'new', label: 'New' },
  { id: 'handled', label: 'Handled' },
  { id: 'archived', label: 'Archived' },
]

const EMPTY: Record<MessageStatus, string> = {
  new: 'No new messages. You are all caught up.',
  handled: 'Nothing handled yet.',
  archived: 'Nothing archived.',
}

/** UK-looking phone numbers in free text: 07…, +44 7…, 01382 … */
function phoneIn(text: string): string | null {
  const m = text.match(/(?:\+44\s?\(?0?\)?\s?|\b0)\d(?:[\s-]?\d){8,9}\b/)
  return m ? m[0].trim() : null
}
const telHref = (p: string) => `tel:${p.replace(/[^\d+]/g, '')}`

/** The moves each pile offers: [to, button, in progress, done]. */
const MOVES: Record<MessageStatus, [MessageStatus, string, string, string][]> = {
  new: [
    ['handled', 'Mark handled', 'Saving…', 'Marked handled.'],
    ['archived', 'Archive', 'Archiving…', 'Archived.'],
  ],
  handled: [
    ['archived', 'Archive', 'Archiving…', 'Archived.'],
    ['new', 'Mark as new', 'Saving…', 'Back in New.'],
  ],
  archived: [['new', 'Move back to New', 'Saving…', 'Back in New.']],
}

/* ------------------------------------------------------------ screen --- */

export function MessagesScreen() {
  return (
    <>
      <PageHeader title="Messages" subtitle="from the contact form on the website" />
      <PageBody>
        <WebsiteGate>
          <Inbox />
        </WebsiteGate>
      </PageBody>
    </>
  )
}

function Inbox() {
  const loc = useLocation()
  const q = loc.query.get('tab')
  const tab: MessageStatus = q === 'handled' || q === 'archived' ? q : 'new'
  const setTab = (t: MessageStatus) => navigate(loc.path, { replace: true, query: t === 'new' ? undefined : { tab: t } })

  const messages = useQuery({
    queryKey: [...WEBSITE_KEY, 'messages', 'all'],
    queryFn: () => siteGet<Message[]>('/messages?status=all'),
  })
  const invalidate = useInvalidateWebsite()
  const [open, setOpen] = useState<Set<number>>(() => new Set())
  const [pending, setPending] = useState<{ id: number; to: MessageStatus } | null>(null)
  const [outcome, setOutcome] = useState<Outcome | null>(null)
  const [rowError, setRowError] = useState<{ id: number; text: string } | null>(null)
  const listRef = useRef<HTMLUListElement>(null)
  const chipsRef = useRef<HTMLDivElement>(null)
  const refocus = useRef(false)

  // After a message leaves the pile, keep keyboard focus on the page: the next
  // message, or the current chip when the pile is now empty.
  useEffect(() => {
    if (!refocus.current || messages.isFetching) return
    refocus.current = false
    const next = listRef.current?.querySelector<HTMLElement>('button[aria-expanded]')
    const chip = chipsRef.current?.querySelector<HTMLElement>('button[aria-pressed="true"]')
    ;(next ?? chip)?.focus()
  })

  if (messages.error) {
    return (
      <div className="flex flex-col items-start gap-3">
        <ErrorBox error={messages.error} what="messages" />
        <Button pending={messages.isFetching} pendingLabel="Loading…" onClick={() => void messages.refetch()}>
          Try again
        </Button>
      </div>
    )
  }
  if (!messages.data) return <Loading what="Loading messages" />

  const all = messages.data
  const counts: Record<MessageStatus, number> = { new: 0, handled: 0, archived: 0 }
  for (const m of all) counts[m.status]++
  const list = all.filter((m) => m.status === tab).sort((a, b) => b.created_at.localeCompare(a.created_at))

  function toggle(id: number) {
    setOpen((prev) => {
      const next = new Set(prev)
      if (next.has(id)) next.delete(id)
      else next.add(id)
      return next
    })
  }

  async function move(m: Message, to: MessageStatus, done: string) {
    const from = m.status
    setPending({ id: m.id, to })
    setRowError(null)
    setOutcome(null)
    const r = await siteWrite<Message>(`/messages/${m.id}`, { status: to }, 'PATCH')
    setPending(null)
    if (r.kind !== 'ok') {
      setRowError({ id: m.id, text: r.message })
      return
    }
    setOpen((prev) => {
      const next = new Set(prev)
      next.delete(m.id)
      return next
    })
    refocus.current = true
    await invalidate()
    setOutcome({
      kind: 'ok',
      text: `${m.name}: ${done}`,
      action: {
        label: 'Undo',
        onClick: () => {
          setOutcome(null)
          void siteWrite<Message>(`/messages/${m.id}`, { status: from }, 'PATCH').then(async (u) => {
            if (u.kind === 'ok') {
              await invalidate()
              setOutcome({ kind: 'ok', text: `${m.name}: undone.` })
            } else setOutcome({ kind: 'error', text: u.message })
          })
        },
      },
    })
  }

  return (
    <div className="mx-auto flex w-full min-w-0 max-w-[1100px] flex-col gap-3.5">
      <div ref={chipsRef}>
        <FilterChipRow label="Messages">
          {TABS.map((t) => (
            <FilterChip key={t.id} active={tab === t.id} onClick={() => setTab(t.id)} count={counts[t.id] > 0 ? counts[t.id] : undefined}>
              {t.label}
            </FilterChip>
          ))}
        </FilterChipRow>
      </div>

      <StatusLine outcome={outcome} />

      {list.length === 0 ? (
        <div className="border-t border-line">
          <Empty>{EMPTY[tab]}</Empty>
        </div>
      ) : (
        <ul ref={listRef} aria-label={`${TABS.find((t) => t.id === tab)?.label ?? ''} messages`} className="border-t border-line">
          {list.map((m) => (
            <MessageItem
              key={m.id}
              m={m}
              expanded={open.has(m.id)}
              onToggle={() => toggle(m.id)}
              pendingTo={pending?.id === m.id ? pending.to : null}
              error={rowError?.id === m.id ? rowError.text : null}
              onMove={(to, done) => void move(m, to, done)}
            />
          ))}
        </ul>
      )}
    </div>
  )
}

function MessageItem({
  m,
  expanded,
  onToggle,
  pendingTo,
  error,
  onMove,
}: {
  m: Message
  expanded: boolean
  onToggle: () => void
  pendingTo: MessageStatus | null
  error: string | null
  onMove: (to: MessageStatus, done: string) => void
}) {
  const bodyId = `msg-${m.id}`
  const phone = phoneIn(m.message)
  const subject = `Re: your message to Sasha's Corner${m.topic && m.topic !== 'general' ? ` (${topicLabel(m.topic).toLowerCase()})` : ''}`
  const quoted = `\n\n---\nOn ${stamp(m.created_at)}, ${m.name} wrote:\n${m.message}`
  const from = `${m.name} · ${m.email || 'no email given'} · ${stamp(m.created_at)}${m.handled_at && m.status !== 'new' ? ` · dealt with ${ago(m.handled_at)}` : ''}`

  return (
    <li className="border-b border-line">
      <button
        type="button"
        aria-expanded={expanded}
        aria-controls={bodyId}
        onClick={onToggle}
        className="grid min-h-16 w-full grid-cols-[minmax(0,1fr)_auto] gap-x-3 gap-y-0.5 rounded-control px-2 py-4 text-left transition-colors hover:bg-canvas-2"
      >
        <span className="flex min-w-0 flex-wrap items-center gap-2">
          <span className="text-lg font-bold break-words text-ink [overflow-wrap:anywhere]">{m.name}</span>
          <Pill>{topicLabel(m.topic)}</Pill>
        </span>
        <span className="text-sm whitespace-nowrap text-ink-2" title={stamp(m.created_at)}>
          {ago(m.created_at)}
        </span>
        {!expanded && <span className="col-span-2 truncate text-md text-ink-2">{m.message}</span>}
      </button>
      <div id={bodyId} hidden={!expanded} className="flex flex-col gap-3 px-1 pb-4">
        <p className="max-w-[68ch] text-lg leading-relaxed whitespace-pre-wrap text-ink [overflow-wrap:anywhere]">{m.message}</p>
        <p className="text-sm text-ink-2 [overflow-wrap:anywhere]">{from}</p>
        <div className="flex flex-wrap gap-2 max-sm:[&>*]:flex-[1_1_calc(50%-4px)]">
          {m.email && (
            <LinkButton className="max-sm:min-h-11" href={`mailto:${m.email}?subject=${encodeURIComponent(subject)}&body=${encodeURIComponent(quoted)}`}>
              Reply by email
            </LinkButton>
          )}
          {phone && <LinkButton className="max-sm:min-h-11" href={telHref(phone)}>Call {phone}</LinkButton>}
          {MOVES[m.status].map(([to, label, doing, done]) => (
            <Button
              key={to}
              variant="outline"
              size="md"
              className="max-sm:min-h-11"
              pending={pendingTo === to}
              pendingLabel={doing}
              disabled={pendingTo !== null && pendingTo !== to}
              onClick={() => onMove(to, done)}
            >
              {label}
            </Button>
          ))}
        </div>
        <StatusLine outcome={error ? { kind: 'error', text: error } : null} />
      </div>
    </li>
  )
}
