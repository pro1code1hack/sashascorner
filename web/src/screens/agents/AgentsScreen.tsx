/**
 * Agents (shell-agents.md §6, DECISIONS §1 and §7, CLAUDE.md §9 / invariant 10).
 *
 * Left: what is waiting for a person (agent proposals, plus orders waiting in
 * Telegram, read-only), what was decided recently, and what agents may do.
 * Right: the run log, from `agent_action_log`, with human decisions interleaved.
 *
 * Accept is a real write through the kind's own service, so nothing here is
 * optimistic: the pressed button reads "Applying…" and the card leaves only
 * when the server says so. A proposal the world moved past comes back
 * SUPERSEDED and stays on screen with the reason until dismissed.
 */
import { useEffect, useState } from 'react'
import { useInfiniteQuery, useQueryClient } from '@tanstack/react-query'
import { Button, Card, ErrorBox, FilterChip, FilterChipRow, Loading, PageHeader, cx } from '../../components/ui'
import { OperatorNeeded } from '../../components/shell/Operator'
import { ago, gbp } from '../../lib/format'
import { useOperator } from '../../lib/operator'
import { href } from '../../lib/router'
import {
  PROPOSALS_QUERY_KEY,
  RUNS_QUERY_KEY,
  SHELL_QUERY_KEY,
  decideProposal,
  fetchRuns,
  goToRoute,
  useProposals,
} from '../../lib/shell-api'
import type { AgentProposal, AgentRunRow, AgentRunsResponse, WaitingOrder } from '../../lib/types/shell'

/* ------------------------------------------------------------ proposals --- */

type Pending = { id: number; which: 'accept' | 'decline' } | null
type Flash = { id: number; text: string; tone: 'plain' | 'alert' }
type Stale = { proposal: AgentProposal; text: string }

function useInvalidateAfterDecision() {
  const qc = useQueryClient()
  return () => {
    for (const key of [SHELL_QUERY_KEY, PROPOSALS_QUERY_KEY, RUNS_QUERY_KEY, ['stock'], ['proposals'], ['templates']]) {
      void qc.invalidateQueries({ queryKey: key })
    }
  }
}

function ProposalCard({
  p,
  operator,
  pending,
  error,
  onDecide,
}: {
  p: AgentProposal
  operator: string | null
  pending: Pending
  error: string | null
  onDecide: (p: AgentProposal, which: 'accept' | 'decline') => void
}) {
  const busy = pending !== null
  const mine = pending?.id === p.id
  const failed = p.status === 'APPLY_FAILED'
  const lastError = failed && p.applied_result ? String(p.applied_result.error ?? '') : ''
  const noName = operator === null

  return (
    <Card className="mb-2.5">
      <div className="flex justify-between gap-2.5 text-sm text-ink-2">
        <span className="min-w-0 truncate">{p.agent_label}</span>
        <span className="flex-none">{ago(p.created_at)}</span>
      </div>
      <h3 className="mb-1 mt-0.5 text-lg font-normal">{p.title}</h3>
      <p className="text-base text-ink-2">{p.body}</p>
      {p.note && <p className="mt-1 text-sm text-ink-2">{p.note}</p>}
      {p.confidence === 'low' && (
        <p className="mt-1 text-sm italic text-ink-2">Low confidence: worth checking before accepting.</p>
      )}
      {failed && (
        <p className="mt-1.5 text-sm text-bad-ink">
          Didn&rsquo;t apply{p.decided_by ? ` when ${p.decided_by} accepted it` : ''}:{' '}
          {lastError || 'no reason recorded'}. Nothing was changed; you can try again.
        </p>
      )}
      {p.accept_mode === 'unavailable' && p.unavailable_reason && (
        <p className="mt-1.5 text-sm text-ink-2">{p.unavailable_reason}</p>
      )}
      <div className="mt-2.5 flex flex-wrap gap-2">
        {p.accept_mode === 'apply' && (
          <Button
            variant="primary"
            size="sm"
            disabled={busy || noName}
            pending={mine && pending?.which === 'accept'}
            pendingLabel="Applying…"
            onClick={() => onDecide(p, 'accept')}
          >
            {failed ? 'Try again' : p.accept_label}
          </Button>
        )}
        {p.accept_mode === 'navigate' && p.navigate_to && (
          <Button variant="outline" size="sm" disabled={busy} onClick={() => goToRoute(p.navigate_to!)}>
            {p.accept_label}
          </Button>
        )}
        {p.accept_mode === 'unavailable' && (
          <Button variant="primary" size="sm" disabled>
            {p.accept_label}
          </Button>
        )}
        <Button
          variant="outline"
          size="sm"
          disabled={busy || noName}
          pending={mine && pending?.which === 'decline'}
          pendingLabel="Declining…"
          onClick={() => onDecide(p, 'decline')}
        >
          {p.decline_label}
        </Button>
        {p.accept_mode === 'navigate' && (
          <Button
            variant="ghost"
            size="sm"
            disabled={busy || noName}
            pending={mine && pending?.which === 'accept'}
            pendingLabel="Saving…"
            onClick={() => onDecide(p, 'accept')}
          >
            Done it
          </Button>
        )}
      </div>
      {mine || error === null ? null : (
        <p className="mt-2 text-sm text-bad-ink" role="alert">
          {error}
        </p>
      )}
    </Card>
  )
}

function StaleCard({ s, onDismiss }: { s: Stale; onDismiss: () => void }) {
  return (
    <Card className="mb-2.5">
      <div className="flex justify-between gap-2.5 text-sm text-ink-2">
        <span className="min-w-0 truncate">{s.proposal.agent_label}</span>
        <span className="flex-none">out of date</span>
      </div>
      <h3 className="mb-1 mt-0.5 text-lg font-normal">{s.proposal.title}</h3>
      <p className="text-sm text-ink-2">{s.text}</p>
      <div className="mt-2.5">
        <Button variant="outline" size="sm" onClick={onDismiss}>
          Dismiss
        </Button>
      </div>
    </Card>
  )
}

function OrderCard({ o }: { o: WaitingOrder }) {
  return (
    <Card className="mb-2.5">
      <div className="flex justify-between gap-2.5 text-sm text-ink-2">
        <span>Orders</span>
        <span>{ago(o.created_at)}</span>
      </div>
      <h3 className="mb-1 mt-0.5 text-lg font-normal">
        {o.supplier} order, <span className="fig">{gbp(o.total_pence)}</span>: waiting in Telegram
      </h3>
      <p className="text-base text-ink-2">{o.note}</p>
      <a href={href(`/orders/${o.po_id}`)} className="mt-1 inline-block text-base font-bold text-brand-ink">
        Open the order ›
      </a>
    </Card>
  )
}

const STATUS_WORD: Record<AgentProposal['status'], { word: string; cls: string }> = {
  WAITING: { word: 'waiting', cls: 'text-ink-2' },
  ACCEPTED: { word: 'accepted', cls: 'text-ink' },
  DECLINED: { word: 'declined', cls: 'text-ink-2' },
  SUPERSEDED: { word: 'out of date', cls: 'text-ink-2' },
  APPLY_FAILED: { word: 'didn’t apply', cls: 'text-bad-ink' },
}

function LeftColumn() {
  const q = useProposals()
  const [operator] = useOperator()
  const invalidate = useInvalidateAfterDecision()
  const [pending, setPending] = useState<Pending>(null)
  const [errors, setErrors] = useState<Record<number, string>>({})
  const [flash, setFlash] = useState<Flash[]>([])
  const [stale, setStale] = useState<Stale[]>([])

  useEffect(() => {
    if (flash.length === 0) return
    const t = setTimeout(() => setFlash((f) => f.slice(1)), 4000)
    return () => clearTimeout(t)
  }, [flash])

  const decide = async (p: AgentProposal, which: 'accept' | 'decline') => {
    if (operator === null) return
    setPending({ id: p.id, which })
    setErrors((e) => {
      const n = { ...e }
      delete n[p.id]
      return n
    })
    const r = await decideProposal(p.id, which, { decided_by: operator })
    setPending(null)
    if (r.kind !== 'ok') {
      setErrors((e) => ({ ...e, [p.id]: r.message }))
      invalidate()
      return
    }
    const d = r.data
    if (d.outcome === 'superseded') {
      setStale((s) => [...s, { proposal: d.proposal, text: d.message }])
    } else if (d.outcome === 'failed') {
      setErrors((e) => ({ ...e, [p.id]: d.message }))
    } else {
      setFlash((f) => [...f, { id: p.id, text: d.message, tone: 'plain' }])
    }
    invalidate()
  }

  if (q.isPending) return <Loading what="Loading proposals" />
  if (q.isError) return <ErrorBox error={q.error} what="agent proposals" />
  const data = q.data
  const staleIds = new Set(stale.map((s) => s.proposal.id))
  const waiting = data.waiting.filter((p) => !staleIds.has(p.id))
  const count = waiting.filter((p) => p.status === 'WAITING').length + data.orders_waiting.length

  return (
    <>
      <h2 className="mb-2 text-xl font-extrabold tracking-[-.01em]">
        Waiting for you · <span className="fig">{count}</span>
      </h2>
      {operator === null && (waiting.length > 0 || stale.length > 0) && (
        <div className="mb-2.5">
          <OperatorNeeded what="accept or decline a proposal" />
        </div>
      )}
      <div aria-live="polite">
        {flash.map((f) => (
          <p key={`${f.id}-${f.text}`} className="mb-2.5 rounded-button bg-canvas px-3 py-2 text-sm text-ink-2">
            {f.text}
          </p>
        ))}
      </div>
      {stale.map((s) => (
        <StaleCard
          key={`stale-${s.proposal.id}`}
          s={s}
          onDismiss={() => setStale((all) => all.filter((x) => x !== s))}
        />
      ))}
      {waiting.map((p) => (
        <ProposalCard
          key={p.id}
          p={p}
          operator={operator}
          pending={pending}
          error={errors[p.id] ?? null}
          onDecide={(pp, which) => void decide(pp, which)}
        />
      ))}
      {data.orders_waiting.map((o) => (
        <OrderCard key={`po-${o.po_id}`} o={o} />
      ))}
      {waiting.length === 0 && data.orders_waiting.length === 0 && stale.length === 0 && (
        <Card className="text-base text-ink-2">Nothing waiting. Order confirmations happen in Telegram.</Card>
      )}

      <h2 className="mb-1.5 mt-4.5 text-lg font-extrabold tracking-[-.01em]">Decided recently</h2>
      {data.decided.length === 0 ? (
        <p className="text-base text-ink-2">Nothing decided yet.</p>
      ) : (
        <ul>
          {data.decided.map((p) => {
            const s = STATUS_WORD[p.status]
            return (
              <li key={p.id} className="flex gap-2.5 border-b border-line py-1 text-base">
                <span className={cx('w-[76px] flex-none', s.cls)}>{s.word}</span>
                <span className="min-w-0 flex-1">
                  {p.title}
                  {p.decided_by && <span className="text-ink-2"> · {p.decided_by}</span>}
                </span>
                <span className="flex-none text-ink-2">{p.decided_at ? ago(p.decided_at) : ''}</span>
              </li>
            )
          })}
        </ul>
      )}

      <Card className="mt-5 text-base">
        <h2 className="mb-1 text-lg font-extrabold tracking-[-.01em]">What agents may do</h2>
        <p>
          Read stock, sales, prices and uploads. Pull reports from sites with no API (Deliveroo, Just Eat). Explain
          numbers in plain words. Propose changes.
        </p>
        <h2 className="mb-1 mt-2 text-lg font-extrabold tracking-[-.01em]">What they never do</h2>
        <p>
          Order, pay, or change stock, orders or recipes themselves. Anything that spends money stops at a person. Every
          run is logged with what it read and what it produced.
        </p>
      </Card>
    </>
  )
}

/* -------------------------------------------------------------- run log --- */

const GRID = 'compact:grid compact:grid-cols-[96px_minmax(0,1fr)_minmax(0,1.4fr)_110px] compact:gap-2.5'

function RunRowView({ r, open, onToggle }: { r: AgentRunRow; open: boolean; onToggle: () => void }) {
  const resultCls = r.result_tone === 'alert' ? 'text-alert' : 'text-ink-2'
  const body = (
    <>
      {/* Phone: When · Result on line 1, Agent · tool, then Produced. */}
      <div className="flex justify-between gap-2 compact:contents">
        <span className="text-ink-2">{ago(r.finished_at)}</span>
        <span className={cx('text-right compact:hidden', resultCls)}>{r.result}</span>
      </div>
      <span className="block min-w-0">
        {r.agent_label}
        <br />
        <span className="text-xs text-ink-3">{r.tool_label}</span>
      </span>
      <span className="block min-w-0 break-words">{r.produced}</span>
      <span className={cx('hidden compact:block', resultCls)}>{r.result}</span>
    </>
  )
  if (r.is_decision) {
    return (
      <li className={cx('border-b border-line py-[7px] text-base text-ink-2', GRID, 'flex flex-col gap-0.5')}>
        {body}
      </li>
    )
  }
  return (
    <li className="border-b border-line">
      <button
        type="button"
        aria-expanded={open}
        onClick={onToggle}
        className={cx('w-full py-[7px] text-left text-base', GRID, 'flex flex-col gap-0.5 compact:items-start')}
      >
        {body}
      </button>
      {open && (
        <div className="mb-2 border-l border-line px-2.5 py-0.5 text-sm text-ink-2 compact:ml-[106px]">
          <div>Read: {r.read_summary}</div>
          <div>Wrote to: {r.wrote_summary}</div>
          {r.tools
            .filter((t) => t.refusal_reason)
            .map((t) => (
              <div key={`${t.tool_name}-${t.refusal_reason}`}>
                Refused ({t.label}): {t.refusal_reason}
              </div>
            ))}
          {r.model && <div>Model: {r.model}</div>}
        </div>
      )}
    </li>
  )
}

function RunLog() {
  const [agent, setAgent] = useState<string | null>(null)
  const [open, setOpen] = useState<string | null>(null)
  const q = useInfiniteQuery({
    queryKey: [...RUNS_QUERY_KEY, agent ?? 'all'],
    queryFn: ({ pageParam }) => fetchRuns({ agent, before: pageParam, limit: 30 }),
    initialPageParam: null as string | null,
    getNextPageParam: (last: AgentRunsResponse) => (last.has_more ? last.runs.at(-1)?.finished_at : undefined),
    staleTime: 30 * 1000,
  })
  const [agents, setAgents] = useState<AgentRunsResponse['agents']>([])
  const first = q.data?.pages[0]
  useEffect(() => {
    // Keep the chip list from the unfiltered view so filtering does not hide the others.
    if (first && agent === null) setAgents(first.agents)
  }, [first, agent])

  const rows = q.data?.pages.flatMap((p) => p.runs) ?? []

  return (
    <>
      <div className="mb-2.5 flex flex-wrap items-center gap-2">
        <h2 className="mr-2 text-xl font-extrabold tracking-[-.01em]">Run log</h2>
        <FilterChipRow label="Filter the run log by agent">
          <FilterChip active={agent === null} onClick={() => setAgent(null)}>
            All
          </FilterChip>
          {agents.map((a) => (
            <FilterChip key={a.agent} active={agent === a.agent} onClick={() => setAgent(a.agent)}>
              {a.label}
            </FilterChip>
          ))}
        </FilterChipRow>
      </div>
      {q.isPending ? (
        <Loading what="Loading the run log" />
      ) : q.isError ? (
        <ErrorBox error={q.error} what="the run log" />
      ) : rows.length === 0 ? (
        <p className="text-base text-ink-2">No agent has run yet. Agents run from the command line for now.</p>
      ) : (
        <>
          <div className={cx('hidden border-b border-line py-1 text-sm text-ink-2', GRID)} aria-hidden="true">
            <span>When</span>
            <span>Agent · tool</span>
            <span>Produced</span>
            <span>Result</span>
          </div>
          <ul>
            {rows.map((r) => (
              <RunRowView
                key={r.key}
                r={r}
                open={open === r.key}
                onToggle={() => setOpen(open === r.key ? null : r.key)}
              />
            ))}
          </ul>
          {q.hasNextPage && (
            <div className="mt-3 flex justify-center">
              <Button
                variant="outline"
                size="sm"
                pending={q.isFetchingNextPage}
                pendingLabel="Loading…"
                onClick={() => void q.fetchNextPage()}
              >
                Show older
              </Button>
            </div>
          )}
        </>
      )}
    </>
  )
}

/* --------------------------------------------------------------- screen --- */

export function AgentsScreen() {
  return (
    <>
      <PageHeader title="Agents" subtitle="they read, work things out and propose; a person confirms" />
      <div className="min-h-0 min-w-0 flex-1 overflow-y-auto compact:grid compact:grid-cols-[minmax(0,1fr)_minmax(0,1.35fr)] compact:overflow-hidden">
        <section
          aria-label="Waiting for you"
          className="min-w-0 border-b border-line px-4 py-4 sm:px-5 compact:overflow-y-auto compact:border-b-0 compact:border-r"
        >
          <LeftColumn />
        </section>
        <section aria-label="Run log" className="min-w-0 px-4 py-4 sm:px-5 compact:overflow-y-auto">
          <RunLog />
        </section>
      </div>
    </>
  )
}
