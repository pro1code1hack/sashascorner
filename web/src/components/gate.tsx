/**
 * The commit gate. Spec §10.1: the impact preview is MANDATORY before every
 * commit and must be read and trusted before a destructive-feeling action.
 *
 * It is not a modal (DESIGN.md §8.2). It replaces the component grid and the
 * consequences pane in place, on an ink ground with paper text — the only
 * inverted surface in the application. There is no backdrop to dismiss by
 * accident and no scroll-lock to get wrong at 375px, and inversion carries a
 * meaning nothing else in the app carries: stop and read this.
 *
 * "Apply from today" is the only option, and why is stated in the gate itself
 * at body size, not in a tooltip (invariant 3).
 */
import { useEffect, useRef, useState } from 'react'
import type { PreviewResult } from '../lib/api'
import { applyFromToday } from '../lib/api'
import { money, moneySigned, pct } from '../lib/format'
import type { PreviewResponse } from '../lib/types'
import { Fig, Label, PenceExact } from './prim'

/** The size ladder, not the alphabet. Object key order would put M before S. */
const SIZE_ORDER = ['S', 'M', 'XL', 'ONE']
const bySize = (a: string, b: string): number => {
  const ia = SIZE_ORDER.indexOf(a)
  const ib = SIZE_ORDER.indexOf(b)
  return (ia === -1 ? 99 : ia) - (ib === -1 ? 99 : ib) || a.localeCompare(b)
}

function Row({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <div className="grid grid-cols-[minmax(9rem,auto)_1fr] items-baseline gap-x-4 py-[3px]">
      <span className="fig text-flag-light/80 text-[length:var(--text-micro)]">{label}</span>
      <span>{children}</span>
    </div>
  )
}

function Deltas({ p }: { p: PreviewResponse }) {
  const pv = p.preview
  const lb = p.labour
  return (
    <>
      <Row label="affects">
        <Fig weight={500}>{pv.affected_item_count}</Fig>{' '}
        <span className="text-paper/70">menu items</span>
      </Row>

      <Row label="cost per item">
        {pv.cost_delta_pence_per_item !== null ? (
          <PenceExact value={pv.cost_delta_pence_per_item} signed weight={500} />
        ) : pv.cost_delta_pence_range ? (
          <>
            <PenceExact value={pv.cost_delta_pence_range[0]} signed weight={500} />
            <span className="text-paper/60"> to </span>
            <PenceExact value={pv.cost_delta_pence_range[1]} signed weight={500} />
            <Label className="text-flag-light ml-2">not uniform</Label>
          </>
        ) : (
          <span className="text-paper/60">not given</span>
        )}
      </Row>

      <Row label={`COGS, last ${p.window_days} days`}>
        {pv.monthly_cogs_delta_pence !== null ? (
          <Fig weight={500}>{moneySigned(pv.monthly_cogs_delta_pence)}</Fig>
        ) : (
          <span className="text-paper/60">not given</span>
        )}
      </Row>

      {lb.true_margin_delta_pence_range && (
        <Row label="margin after labour">
          <Fig>{moneySigned(lb.true_margin_delta_pence_range[0])}</Fig>
          <span className="text-paper/60"> to </span>
          <Fig>{moneySigned(lb.true_margin_delta_pence_range[1])}</Fig>
          <span className="text-paper/60"> per item</span>
        </Row>
      )}

      {lb.labour_cost_window_pence !== null && (
        <Row label="labour in that window">
          <Fig>{money(lb.labour_cost_window_pence)}</Fig>
          <span className="text-paper/60"> — unchanged by this edit</span>
        </Row>
      )}

      {lb.untimed_count > 0 && (
        <Row label="untimed items">
          <Fig className="text-flag-light" weight={500}>
            {lb.untimed_count}
          </Fig>
          <span className="text-paper/70"> have no prep time, so no margin after labour</span>
        </Row>
      )}

      {pv.worst_margin_after && (
        <div className="border-paper/20 mt-3 border-t pt-2">
          <Label className="text-flag-light/80">lowest margin after</Label>
          <div className="mt-1">
            <span className="font-[600]">
              {pv.worst_margin_after.name} {pv.worst_margin_after.size_code}
            </span>{' '}
            <Fig weight={500}>{pct(pv.worst_margin_after.margin_pct_before)}</Fig>
            <span className="text-paper/60"> → </span>
            <Fig weight={500} className="text-flag-light">
              {pct(pv.worst_margin_after.margin_pct_after)}
            </Fig>
          </div>
        </div>
      )}
    </>
  )
}

function PerItem({ p }: { p: PreviewResponse }) {
  const [all, setAll] = useState(false)
  const items = all ? p.preview.items : p.preview.items.slice(0, 3)
  const labourBy = new Map(p.labour.items.map((l) => [l.menu_item_id, l]))
  return (
    <div className="border-paper/20 mt-3 border-t pt-2">
      <div className="grid grid-cols-[1fr_auto_auto] gap-x-4 pb-1 md:grid-cols-[1fr_9rem_7rem_7rem]">
        <Label className="text-paper/50">per item</Label>
        <Label className="text-paper/50 text-right">cost</Label>
        <Label className="text-paper/50 text-right">margin</Label>
        <Label className="text-paper/50 hidden text-right md:block">per minute</Label>
      </div>
      {items.map((it) => {
        const l = labourBy.get(it.menu_item_id)
        return (
          <div
            key={it.menu_item_id}
            className="border-paper/10 grid grid-cols-[1fr_auto_auto] gap-x-4 border-t py-[3px] md:grid-cols-[1fr_9rem_7rem_7rem]"
          >
            <span>
              {it.name} <span className="text-paper/60">{it.size_code}</span>
            </span>
            <span className="text-right">
              {it.cost_delta_pence !== null ? (
                <PenceExact value={it.cost_delta_pence} signed size="sub" />
              ) : (
                <Label>—</Label>
              )}
            </span>
            <span className="text-right">
              <Fig size="sub">{pct(it.margin_pct_before)}</Fig>
              <span className="text-paper/50"> → </span>
              <Fig size="sub" weight={500}>
                {pct(it.margin_pct_after)}
              </Fig>
            </span>
            <span className="hidden text-right md:block">
              {l?.margin_per_minute_delta_pence ? (
                <PenceExact value={l.margin_per_minute_delta_pence} signed size="sub" />
              ) : (
                <Label>—</Label>
              )}
            </span>
          </div>
        )
      })}
      {p.preview.items.length > 3 && (
        <button
          type="button"
          className="fig text-paper/60 hover:text-paper mt-1 text-[length:var(--text-micro)] underline underline-offset-2"
          onClick={() => setAll(!all)}
        >
          {all ? 'show fewer' : `… ${p.preview.items.length - 3} more`}
        </button>
      )}
    </div>
  )
}

export function Gate({
  result,
  templateId,
  componentId,
  qtyBySize,
  onCancel,
  onApplied,
}: {
  result: PreviewResult
  templateId: number
  componentId: number
  qtyBySize: Record<string, string>
  onCancel: () => void
  onApplied: () => void
}) {
  const ref = useRef<HTMLDivElement>(null)
  const [applying, setApplying] = useState(false)
  const [outcome, setOutcome] = useState<{ ok: boolean; message: string } | null>(null)

  useEffect(() => {
    ref.current?.focus()
  }, [])

  return (
    <div
      ref={ref}
      tabIndex={-1}
      className="on-ink bg-ink text-paper animate-[settle_120ms_ease-out] px-4 py-4 sm:px-6 sm:py-5"
      role="group"
      aria-label="Impact preview"
      onKeyDown={(e) => {
        if (e.key === 'Escape') onCancel()
      }}
    >
      {result.kind === 'unpriced' && (
        <>
          <h3 className="text-[length:var(--text-title)] font-[500]">
            This edit has not been priced
          </h3>
          <p className="text-paper/80 mt-2 max-w-[62ch] leading-relaxed">
            Fixture mode can only show a preview the API actually answered, and inventing one
            would defeat the point of the gate. The recorded example is slot{' '}
            <Fig>{result.recordedComponentId}</Fig> at{' '}
            <Fig>
              {Object.entries(result.recordedQty)
                .sort(([a], [b]) => bySize(a, b))
                .map(([k, v]) => `${k} ${v}`)
                .join(' · ')}
            </Fig>
            . Set <Fig size="sub">VITE_API_BASE</Fig> to price any other change.
          </p>
        </>
      )}

      {result.kind === 'superseded' && (
        <>
          <h3 className="text-flag-light text-[length:var(--text-title)] font-[500]">
            This slot was edited elsewhere
          </h3>
          <p className="text-paper/80 mt-2 max-w-[62ch] leading-relaxed">
            {result.message}
            {result.currentComponentId !== null && (
              <>
                {' '}
                The live slot is now <Fig weight={500}>{result.currentComponentId}</Fig>.
              </>
            )}{' '}
            Reload the template before editing again. A preview against the closed row would
            have answered &ldquo;0 items affected, no warnings&rdquo; — which reads as{' '}
            <em>this edit is harmless</em>, and it is not.
          </p>
        </>
      )}

      {result.kind === 'failed' && (
        <>
          <h3 className="text-flag-light text-[length:var(--text-title)] font-[500]">
            The preview did not come back
          </h3>
          <p className="text-paper/80 mt-2 max-w-[62ch] leading-relaxed">
            {result.message}. Nothing has been changed, and nothing can be applied without a
            preview.
          </p>
        </>
      )}

      {result.kind === 'ok' && (
        <>
          <h3 className="text-[length:var(--text-title)] leading-tight font-[500]">
            Before you change the {result.data.ingredient_name ?? result.data.component_role}{' '}
            slot
          </h3>

          <div className="border-paper/20 mt-3 border-t pt-2">
            {Object.keys(result.data.qty_by_size_after)
              .sort(bySize)
              .map((size) => {
                const before = result.data.qty_by_size_before[size]
                const after = result.data.qty_by_size_after[size]
                if (before === undefined || after === undefined) return null
                // Printed exactly as sent, NOT trimmed: "0.18 → 0.20" lines its
                // decimals up and "0.18 → 0.2" does not, and the API's string
                // is the authoritative one either way.
                return (
                  <div key={size} className="grid grid-cols-[4rem_5rem_1.5rem_5rem] items-baseline py-[2px]">
                    <Label className="text-paper/50">size {size}</Label>
                    <Fig className="text-right">{before}</Fig>
                    <span className="text-paper/60 text-center"> → </span>
                    <Fig weight={500} className="text-right">
                      {after}
                    </Fig>
                  </div>
                )
              })}
          </div>

          <div className="mt-3">
            <Deltas p={result.data} />
          </div>

          {result.data.preview.warnings.map((w) => (
            <p
              key={w}
              className="border-flag-light/60 text-paper/85 mt-3 max-w-[70ch] border-l-2 pl-3 leading-relaxed"
            >
              {w}
            </p>
          ))}
          {result.data.labour.warnings.map((w) => (
            <p
              key={w}
              className="border-flag-light/60 text-paper/85 mt-2 max-w-[70ch] border-l-2 pl-3 leading-relaxed"
            >
              {w}
            </p>
          ))}

          <PerItem p={result.data} />

          <p className="text-paper/60 mt-3">
            {result.data.writes_nothing
              ? 'This preview wrote nothing.'
              : 'This preview reports that it may have written.'}
          </p>
        </>
      )}

      {/* ------------------------------------------------------ the decision */}
      <div className="border-paper/25 mt-5 border-t pt-3">
        <div className="flex flex-wrap items-center gap-3">
          {result.kind === 'ok' && (
            <button
              type="button"
              disabled={applying}
              className="border-paper bg-paper text-ink hover:bg-flag-light border px-4 py-[6px] font-[600] disabled:opacity-60"
              onClick={async () => {
                setApplying(true)
                setOutcome(await applyFromToday(templateId, componentId, qtyBySize))
                setApplying(false)
              }}
            >
              {applying ? 'Applying…' : 'Apply from today'}
            </button>
          )}
          <button
            type="button"
            className="border-paper/50 text-paper/90 hover:border-paper border px-4 py-[6px]"
            onClick={result.kind === 'ok' && outcome?.ok ? onApplied : onCancel}
          >
            {outcome?.ok ? 'Close' : 'Cancel'}
          </button>
        </div>

        {/* Invariant 3, stated once and plainly. Not a tooltip. */}
        {result.kind === 'ok' && (
          <p className="text-paper/70 mt-3 max-w-[70ch] leading-relaxed">
            <span className="text-paper font-[600]">Apply from today is the only option.</span>{' '}
            Recipe edits are effective-dated: today forward changes and history does not.
            Nothing you do here alters what last month&rsquo;s coffees cost or what margin
            they made — which is the only reason those figures can be trusted.
          </p>
        )}

        {outcome && (
          <p
            className={`mt-3 max-w-[70ch] leading-relaxed ${
              outcome.ok ? 'text-paper' : 'text-flag-light'
            }`}
          >
            {outcome.message}
          </p>
        )}
      </div>
    </div>
  )
}
