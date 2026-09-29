/**
 * The count flow (§1.3). Replaces the listing; toolbar and info strip stay.
 *
 * Least-trusted first, so stopping halfway still does the useful part. The
 * expected figure is hidden until something is typed, on purpose. The live
 * preview is exact decimal arithmetic (lib/dec); the saved result is the
 * server's: drift, verdict and the gate's sentence come back from
 * `POST /api/stock/{id}/counts` and are shown on the next card.
 */
import { useEffect, useRef, useState } from 'react'
import { useQueryClient } from '@tanstack/react-query'
import { Button, StatusLine, Stepper, cx } from '../../components/ui'
import { add, cmp, fromInt, parseDec, sub, toFixed } from '../../lib/dec'
import type { Dec } from '../../lib/dec'
import { useOperator } from '../../lib/operator'
import { SHELL_QUERY_KEY } from '../../lib/shell-api'
import { KEYS, stockWrites } from '../../lib/stock-api'
import type { StockRow } from '../../lib/types/stock'
import { fmtQ, pctOut, unitWord } from './fmt'

function stepFor(unit: string): Dec {
  if (unit === 'EACH') return fromInt(1)
  if (unit === 'KG' || unit === 'L') return { u: 1n, s: 1 }
  return fromInt(10)
}

function show(d: Dec, unit: string): string {
  const dp = unit === 'EACH' ? 0 : unit === 'KG' || unit === 'L' ? Math.max(1, Math.min(d.s, 3)) : Math.min(d.s, 1)
  return toFixed(d, dp).replace('−', '-')
}

export function CountFlow({
  queue,
  byId,
  onDone,
}: {
  queue: number[]
  byId: Map<number, StockRow>
  onDone: () => void
}) {
  const qc = useQueryClient()
  const [operator] = useOperator()
  const [i, setI] = useState(0)
  const [val, setVal] = useState('')
  const [counted, setCounted] = useState(0)
  const [pending, setPending] = useState(false)
  const [refusal, setRefusal] = useState<string | null>(null)
  const [last, setLast] = useState<string | null>(null)

  const finish = async () => {
    await qc.invalidateQueries({ queryKey: KEYS.stock })
    await qc.invalidateQueries({ queryKey: ['stock-v2-detail'] })
    await qc.invalidateQueries({ queryKey: KEYS.draft })
    await qc.invalidateQueries({ queryKey: SHELL_QUERY_KEY })
    onDone()
  }

  const id = queue[i]
  const row = id === undefined ? undefined : byId.get(id)
  // When the queue runs out the input that had focus unmounts; focus moves to
  // "Back to stock" instead of falling to <body>.
  const doneRef = useRef<HTMLButtonElement>(null)
  const finished = row === undefined
  useEffect(() => {
    if (finished) doneRef.current?.focus()
  }, [finished])

  if (row === undefined) {
    return (
      <div className="min-h-0 flex-1 overflow-y-auto p-6">
        <div className="flex flex-col items-center gap-3 px-4 py-15 text-center">
          <p role="status" className="text-lg">
            Count finished: {counted} {counted === 1 ? 'ingredient' : 'ingredients'} counted.
          </p>
          {last && <p className="text-sm text-ink-2">{last}</p>}
          <Button ref={doneRef} variant="primary" onClick={finish}>
            Back to stock
          </Button>
        </div>
      </div>
    )
  }

  const unit = row.unit
  const step = stepFor(unit)
  const v = parseDec(val)
  const est = parseDec(row.on_hand.qty)
  let msg: { text: string; tone: 'ink' | 'ink-2' | 'alert' } | null = null
  if (v !== null && val.trim() !== '') {
    if (!row.on_hand.has_count_basis || est === null) {
      msg = { text: 'First count for this one; it becomes the starting point.', tone: 'ink' }
    } else {
      const p = pctOut(est, v)
      const n = Number(p)
      const expected = fmtQ(row.on_hand.qty, unit)
      if (n < 10) msg = { text: `Expected about ${expected}. ${p}% out: close enough.`, tone: 'ink' }
      else if (n <= 15)
        msg = {
          text: `Expected about ${expected}. ${p}% out: worth a second look, stays on manual ordering.`,
          tone: 'ink-2',
        }
      else
        msg = {
          text: `Expected about ${expected}. ${p}% out: over 15%, so this comes off auto-ordering until it settles.`,
          tone: 'alert',
        }
    }
  }

  const next = () => {
    setI((n) => n + 1)
    setVal('')
    setRefusal(null)
  }

  const save = async () => {
    if (v === null || val.trim() === '' || v.u < 0n) return
    setPending(true)
    setRefusal(null)
    try {
      const r = await stockWrites.count(row.ingredient_id, { counted_qty: val.trim(), counted_by: operator })
      if (r.kind === 'ok') {
        setCounted((c) => c + 1)
        setLast(`Saved ${r.data.ingredient_name}: ${r.data.gate_reason}`)
        next()
      } else {
        setRefusal(r.message)
      }
    } finally {
      setPending(false)
    }
  }

  const upNext = queue
    .slice(i + 1, i + 6)
    .map((n) => byId.get(n)?.name)
    .filter((n): n is string => n !== undefined)
  const canDown = v !== null && cmp(v, fromInt(0)) > 0

  return (
    <div className="min-h-0 flex-1 overflow-y-auto px-4 py-6 sm:px-6">
      <div className="mx-auto max-w-[560px] rounded-button border border-line px-4 py-5 sm:px-6">
        <div className="flex justify-between gap-3 text-base text-ink-2">
          <span>
            Counting · {i + 1} of {queue.length}
          </span>
          <Button variant="link" className="min-h-10" onClick={finish}>
            Stop for now
          </Button>
        </div>
        <h2 className="mt-1 text-3xl font-extrabold tracking-[-.01em]">{row.name}</h2>
        <p className="mb-4 text-base text-ink-2">
          {unit === 'EACH'
            ? 'Count every one, opened packs too.'
            : `Everything you can see, opened ones included. In ${unitWord(unit)}.`}
        </p>
        <div className="flex items-center gap-2.5">
          <Stepper
            size="lg"
            label={unitWord(unit)}
            value={val}
            canDecrement={canDown}
            onDecrement={() => {
              if (v === null) return
              const n = sub(v, step)
              setVal(show(cmp(n, fromInt(0)) < 0 ? fromInt(0) : n, unit))
            }}
            onIncrement={() => setVal(show(v === null ? step : add(v, step), unit))}
          >
            <input
              key={row.ingredient_id}
              autoFocus
              aria-label={`${row.name}, what you can see, in ${unitWord(unit)}`}
              inputMode="decimal"
              value={val}
              onChange={(e) => setVal(e.target.value)}
              onKeyDown={(e) => {
                if (e.key === 'Enter') void save()
              }}
              placeholder="What you can see"
              className="fig h-[50px] w-full min-w-0 flex-1 rounded-button border border-line px-2.5 text-center text-2xl outline-none placeholder:text-ink-3 focus-visible:edge-brand"
            />
          </Stepper>
          <span className="text-lg text-ink-2">{unitWord(unit)}</span>
        </div>
        <div
          aria-live="polite"
          className={cx(
            'mt-3.5 min-h-[52px] text-md',
            msg?.tone === 'alert' ? 'text-alert' : msg?.tone === 'ink-2' ? 'text-ink-2' : 'text-ink',
          )}
        >
          {msg?.text}
        </div>
        {/* Always mounted: the refusal (assertive) or the last save (polite). */}
        <StatusLine
          className="mb-3.5"
          outcome={refusal !== null ? { kind: 'error', text: refusal } : last ? { kind: 'ok', text: last } : null}
        />
        <div className="flex gap-2.5">
          <Button size="lg" className="flex-1" onClick={next}>
            Skip
          </Button>
          <Button
            variant="primary"
            size="lg"
            className="flex-[2]"
            disabled={v === null || val.trim() === ''}
            pending={pending}
            pendingLabel="Saving…"
            onClick={save}
          >
            Save · next
          </Button>
        </div>
        <p className="mt-3 text-sm text-ink-2">
          Least-trusted first, so stopping halfway still does the useful part. The expected figure shows only after you
          type.
        </p>
      </div>
      {upNext.length > 0 && (
        <div className="mx-auto mt-3.5 flex max-w-[560px] flex-wrap items-center gap-1.5">
          <span className="text-sm text-ink-2">Up next:</span>
          {upNext.map((n) => (
            <span key={n} className="rounded-card border border-line-strong px-2 text-sm">
              {n}
            </span>
          ))}
        </div>
      )}
    </div>
  )
}
