/**
 * "What this change does": the mandatory impact preview before any recipe,
 * price or line edit is applied (CLAUDE.md §5.6; spec §V1.9).
 *
 * The Apply button exists only while a preview for exactly the current draft
 * is on screen; the caller passes `ready` for that. Every figure comes from
 * the server's preview (it resolves the recipe; the screen never computes a
 * cost). Estimates are italic; an unknown cost is left out and said so.
 */
import type { ReactNode } from 'react'
import { Button, StatusLine, cx } from '../../../components/ui'
import type { Outcome } from '../../../components/ui'
import { useOperator } from '../../../lib/operator'
import type { ChangeImpact } from '../../../lib/types/menu'
import { gbpDelta3, gbpSigned, pctText, sizeLabel } from './figures'

export type PreviewStatus =
  | { kind: 'idle' }
  | { kind: 'loading' }
  | { kind: 'ready' }
  | { kind: 'refused'; message: string }
  | { kind: 'conflict'; message: string }
  | { kind: 'offline'; message: string }
  | { kind: 'failed'; message: string }

export interface Figure {
  label: string
  value: ReactNode
  alert?: boolean
  est?: boolean
  quiet?: boolean
}

export function impactFigures(impact: ChangeImpact): Figure[] {
  const est = impact.estimated_count > 0
  const perItem =
    impact.cost_delta_pence_range !== null
      ? `${gbpDelta3(impact.cost_delta_pence_range[0])} to ${gbpDelta3(impact.cost_delta_pence_range[1])}`
      : impact.cost_delta_pence_per_item === null
        ? 'unknown'
        : /^-?0(\.0*)?$/.test(impact.cost_delta_pence_per_item)
          ? 'no change'
          : gbpDelta3(impact.cost_delta_pence_per_item)
  const w = impact.worst_margin_after
  const month = impact.window_days === 30 ? 'Over a month' : `Over ${impact.window_days} days`
  const out: Figure[] = [
    { label: 'Menu items affected', value: impact.affected_item_count },
    { label: 'Cost per item', value: perItem, est },
    {
      label: 'Thinnest margin after',
      value: w
        ? `${w.name}${w.size_code ? ` ${sizeLabel(w.size_code)}` : ''}, ${pctText(w.margin_pct_before, 1)} → ${pctText(w.margin_pct_after, 1)}`
        : '—',
      alert: w !== null && w.margin_pct_after !== null && w.margin_pct_after < 60,
      est,
    },
    impact.monthly_cogs_delta_pence === null
      ? { label: `${month}, cost`, value: 'needs item sales from Lightspeed', quiet: true }
      : { label: `${month}, cost`, value: gbpSigned(impact.monthly_cogs_delta_pence), est },
  ]
  if (impact.revenue_delta_pence !== null) {
    out.push({ label: `${month}, takings`, value: gbpSigned(impact.revenue_delta_pence) })
  }
  return out
}

export function ImpactPanel({
  title = 'What this change does',
  status,
  diff,
  figures,
  warnings,
  refusals = [],
  posActions = [],
  note = 'Changes apply from today. Past sales keep the recipe they were sold with; history is never rewritten.',
  applyLabel = 'Apply from today',
  onDiscard,
  onApply,
  applying,
  ready,
  children,
}: {
  title?: string
  status: PreviewStatus
  diff: string[]
  figures: Figure[]
  warnings: string[]
  refusals?: string[]
  posActions?: string[]
  note?: string
  applyLabel?: string
  onDiscard: () => void
  onApply: () => void
  applying: boolean
  /** A preview for exactly the current draft is on screen, with no refusals. */
  ready: boolean
  /** Kept for callers; the operator prompt is gone (owner, 2026-09-26). */
  operatorWhat?: string
  children?: ReactNode
}) {
  const [operator] = useOperator()
  const shown = diff.slice(0, 8)
  // One always-mounted status line carries the preview's state. The panel is
  // not a live region: that re-announced its buttons on every keystroke.
  const outcome: Outcome | null =
    status.kind === 'loading'
      ? { kind: 'info', text: 'Working it out…' }
      : status.kind === 'ready'
        ? { kind: 'ok', text: 'Preview ready.' }
        : 'message' in status
          ? { kind: 'error', text: status.message }
          : null
  return (
    <section className="rounded-card border border-line bg-surface p-3.5" aria-busy={status.kind === 'loading' || undefined}>
      <h3 className="text-lg font-extrabold tracking-[-.01em]">{title}</h3>
      <StatusLine outcome={outcome} className="mt-1" />
      {shown.length > 0 && (
        <ul className="mt-1">
          {shown.map((line, i) => (
            <li key={i} className="py-0.5 text-base">
              {line}
            </li>
          ))}
          {diff.length > 8 && <li className="py-0.5 text-base text-ink-2">…and {diff.length - 8} more</li>}
        </ul>
      )}
      {figures.length > 0 && (
        <dl
          className={cx(
            'my-2.5 grid grid-cols-[auto_minmax(0,1fr)] gap-x-2.5 gap-y-1 border-t border-line pt-2 text-base',
            status.kind === 'loading' && 'opacity-60',
          )}
        >
          {figures.map((f) => (
            <div key={f.label} className="contents">
              <dt className="whitespace-nowrap text-ink-2">{f.label}</dt>
              <dd
                className={cx(
                  'fig text-right font-bold',
                  f.alert && 'text-alert',
                  f.est && 'italic',
                  f.quiet && 'font-normal text-ink-2',
                )}
              >
                {f.value}
              </dd>
            </div>
          ))}
        </dl>
      )}
      {children}
      {refusals.map((r, i) => (
        <p key={`r${i}`} className="mt-1 text-sm font-bold text-bad-ink">
          {r}
        </p>
      ))}
      {warnings.map((w, i) => (
        <p key={`w${i}`} className="mt-1 text-sm text-bad-ink">
          {w}
        </p>
      ))}
      {posActions.map((p, i) => (
        <p key={`p${i}`} className="mt-1 text-sm font-semibold text-ink">
          {p}
        </p>
      ))}
      <p className="mb-3 mt-2 text-sm text-ink-2">{note}</p>
      <div className="flex gap-2">
        <Button variant="outline" className="flex-1 rounded-card-lg" onClick={onDiscard} disabled={applying}>
          Discard
        </Button>
        <Button
          variant="primary"
          size="sm"
          className="flex-[2]"
          disabled={!ready || operator === null}
          pending={applying}
          pendingLabel="Applying…"
          onClick={onApply}
        >
          {applyLabel}
        </Button>
      </div>
    </section>
  )
}
