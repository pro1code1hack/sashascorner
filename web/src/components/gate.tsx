/**
 * The commit gate. The impact preview is MANDATORY before every commit and has
 * to be read before a destructive-feeling action.
 *
 * It is not a modal. It replaces the component grid in place, on a raised
 * surface inside a brand-coloured border — the only brand-bordered panel in the
 * application, which is what carries the meaning nothing else carries: stop and
 * read this. There is no backdrop to dismiss by accident and no scroll-lock to
 * get wrong at 375px.
 *
 * "Apply from today" is the only option, and why is stated in the gate itself at
 * body size, not in a tooltip (invariant 3).
 */
import { useEffect, useRef, useState } from 'react'
import type { PreviewResult } from '../lib/api'
import { applyFromToday } from '../lib/api'
import { money, moneySigned, pct } from '../lib/format'
import type { PreviewResponse } from '../lib/types'
import { Badge, Button, ScrollX, Table, Td, Th } from './ui'
import { Fig, Label, PenceExact } from './prim'

/** The size ladder, not the alphabet. Object key order would put M before S. */
const SIZE_ORDER = ['S', 'M', 'XL', 'ONE']
const bySize = (a: string, b: string): number => {
  const ia = SIZE_ORDER.indexOf(a)
  const ib = SIZE_ORDER.indexOf(b)
  return (ia === -1 ? 99 : ia) - (ib === -1 ? 99 : ib) || a.localeCompare(b)
}

const PROSE = 'mt-2 max-w-[62ch] text-[0.875rem] text-ink-2 leading-relaxed'

function Row({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <div className="grid grid-cols-1 sm:grid-cols-[minmax(9rem,11rem)_minmax(0,1fr)] items-baseline gap-x-4 py-1 border-b border-line last:border-b-0">
      <Label>{label}</Label>
      <span className="min-w-0">{children}</span>
    </div>
  )
}

function Deltas({ p }: { p: PreviewResponse }) {
  const pv = p.preview
  const lb = p.labour
  return (
    <div className="mt-1">
      <Row label="affects">
        <Fig weight="medium">{pv.affected_item_count}</Fig>{' '}
        <Label>menu items</Label>
      </Row>

      <Row label="cost per item">
        {pv.cost_delta_pence_per_item !== null ? (
          <PenceExact value={pv.cost_delta_pence_per_item} signed weight="medium" />
        ) : pv.cost_delta_pence_range ? (
          <span className="inline-flex flex-wrap items-baseline gap-x-1.5">
            <PenceExact value={pv.cost_delta_pence_range[0]} signed weight="medium" />
            <Label>to</Label>
            <PenceExact value={pv.cost_delta_pence_range[1]} signed weight="medium" />
            <Badge tone="warn">not uniform</Badge>
          </span>
        ) : (
          <Fig missing="not given" />
        )}
      </Row>

      <Row label={`COGS, last ${p.window_days} days`}>
        {pv.monthly_cogs_delta_pence !== null ? (
          <Fig weight="medium">{moneySigned(pv.monthly_cogs_delta_pence)}</Fig>
        ) : (
          <Fig missing="not given" />
        )}
      </Row>

      {lb.true_margin_delta_pence_range && (
        <Row label="margin after labour">
          <span className="inline-flex flex-wrap items-baseline gap-x-1.5">
            <Fig>{moneySigned(lb.true_margin_delta_pence_range[0])}</Fig>
            <Label>to</Label>
            <Fig>{moneySigned(lb.true_margin_delta_pence_range[1])}</Fig>
            <Label>per item</Label>
          </span>
        </Row>
      )}

      {lb.labour_cost_window_pence !== null && (
        <Row label="labour in that window">
          <Fig>{money(lb.labour_cost_window_pence)}</Fig>{' '}
          <Label>unchanged by this edit</Label>
        </Row>
      )}

      {lb.untimed_count > 0 && (
        <Row label="untimed items">
          <Fig weight="medium" tone="warn">
            {lb.untimed_count}
          </Fig>{' '}
          <Label>have no prep time, so no margin after labour</Label>
        </Row>
      )}

      {pv.worst_margin_after && (
        <div className="mt-3 rounded-control border border-line bg-surface px-3 py-2">
          <Label>lowest margin after</Label>
          <div className="mt-1 flex flex-wrap items-baseline gap-x-2">
            <span className="font-semibold text-ink">
              {pv.worst_margin_after.name} {pv.worst_margin_after.size_code}
            </span>
            <Fig weight="medium">{pct(pv.worst_margin_after.margin_pct_before)}</Fig>
            <Label>→</Label>
            <Fig weight="medium" tone="warn">
              {pct(pv.worst_margin_after.margin_pct_after)}
            </Fig>
          </div>
        </div>
      )}
    </div>
  )
}

function PerItem({ p }: { p: PreviewResponse }) {
  const [all, setAll] = useState(false)
  const items = all ? p.preview.items : p.preview.items.slice(0, 3)
  const labourBy = new Map(p.labour.items.map((l) => [l.menu_item_id, l]))
  return (
    <div className="mt-4">
      <ScrollX>
        <Table>
          <thead>
            <tr>
              <Th>Per item</Th>
              <Th align="right">Cost</Th>
              <Th align="right">Margin</Th>
              <Th align="right">Per minute</Th>
            </tr>
          </thead>
          <tbody>
            {items.map((it) => {
              const l = labourBy.get(it.menu_item_id)
              return (
                <tr key={it.menu_item_id}>
                  <Td>
                    <span className="whitespace-nowrap">
                      {it.name} <Label>{it.size_code}</Label>
                    </span>
                  </Td>
                  <Td align="right">
                    {it.cost_delta_pence !== null ? (
                      <PenceExact value={it.cost_delta_pence} signed size="sm" />
                    ) : (
                      <Fig missing="no price" />
                    )}
                  </Td>
                  <Td align="right">
                    <span className="whitespace-nowrap">
                      <Fig size="sm">{pct(it.margin_pct_before)}</Fig>
                      <Label> → </Label>
                      <Fig size="sm" weight="medium">
                        {pct(it.margin_pct_after)}
                      </Fig>
                    </span>
                  </Td>
                  <Td align="right">
                    {l?.margin_per_minute_delta_pence ? (
                      <PenceExact value={l.margin_per_minute_delta_pence} signed size="sm" />
                    ) : (
                      <Fig missing="not given" />
                    )}
                  </Td>
                </tr>
              )
            })}
          </tbody>
        </Table>
      </ScrollX>
      {p.preview.items.length > 3 && (
        <div className="mt-2">
          <Button variant="ghost" onClick={() => setAll(!all)}>
            {all ? 'Show fewer' : `Show ${p.preview.items.length - 3} more`}
          </Button>
        </div>
      )}
    </div>
  )
}

function Warning({ children }: { children: React.ReactNode }) {
  return (
    <p className="mt-2 max-w-[70ch] border-l-2 border-warn/60 bg-warn-wash/50 pl-3 py-1.5 text-[0.75rem] text-ink-2 leading-[16px]">
      {children}
    </p>
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
      className="rounded-card border border-brand/45 bg-raised shadow-lift p-5 overflow-hidden"
      role="group"
      aria-label="Impact preview"
      onKeyDown={(e) => {
        if (e.key === 'Escape') onCancel()
      }}
    >
      <div className="mb-3">
        <Badge tone="warn">Impact preview · nothing is written yet</Badge>
      </div>

      {result.kind === 'unpriced' && (
        <>
          <h2 className="text-[1rem] font-semibold text-ink leading-[16px]">
            This edit has not been priced
          </h2>
          <p className={PROSE}>
              Fixture mode can only show a preview the API actually answered, and inventing one
              would defeat the point of the gate. The recorded example is slot{' '}
              <Fig size="sm">{result.recordedComponentId}</Fig> at{' '}
              <Fig size="sm">
                {Object.entries(result.recordedQty)
                  .sort(([a], [b]) => bySize(a, b))
                  .map(([k, v]) => `${k} ${v}`)
                  .join(' · ')}
              </Fig>
              . Set <code className="fig text-[0.75rem] text-ink-2">VITE_API_BASE</code> to price
              any other change.
          </p>
        </>
      )}

      {result.kind === 'superseded' && (
        <>
          <h2 className="text-[1rem] font-semibold text-bad leading-[16px]">
            This slot was edited elsewhere
          </h2>
          <p className={PROSE}>
              {result.message}
              {result.currentComponentId !== null && (
                <>
                  {' '}
                  The live slot is now <Fig weight="medium">{result.currentComponentId}</Fig>.
                </>
              )}{' '}
              Reload the template before editing again. A preview against the closed row would have
              answered &ldquo;0 items affected, no warnings&rdquo; — which reads as{' '}
              <em>this edit is harmless</em>, and it is not.
          </p>
        </>
      )}

      {result.kind === 'failed' && (
        <>
          <h2 className="text-[1rem] font-semibold text-bad leading-[16px]">
            The preview did not come back
          </h2>
          <p className={PROSE}>
              {result.message}. Nothing has been changed, and nothing can be applied without a
              preview.
          </p>
        </>
      )}

      {result.kind === 'ok' && (
        <>
          <h2 className="text-[1rem] font-semibold text-ink leading-[16px]">
            Before you change the {result.data.ingredient_name ?? result.data.component_role} slot
          </h2>

          <div className="mt-3 rounded-control border border-line bg-surface px-3 py-2">
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
                  <div
                    key={size}
                    className="grid grid-cols-[3.5rem_5rem_1.5rem_5rem] items-baseline py-[2px]"
                  >
                    <Label>size {size}</Label>
                    <Fig className="text-right">{before}</Fig>
                    <Label className="text-center">→</Label>
                    <Fig weight="medium" className="text-right">
                      {after}
                    </Fig>
                  </div>
                )
              })}
          </div>

          <Deltas p={result.data} />

          {result.data.preview.warnings.map((w) => (
            <Warning key={w}>{w}</Warning>
          ))}
          {result.data.labour.warnings.map((w) => (
            <Warning key={w}>{w}</Warning>
          ))}

          <PerItem p={result.data} />

          <p className="mt-3 text-[0.75rem] text-ink-3">
            {result.data.writes_nothing
              ? 'This preview wrote nothing.'
              : 'This preview reports that it may have written.'}
          </p>
        </>
      )}

      {/* ------------------------------------------------------ the decision */}
      <div className="mt-5 border-t border-line pt-4">
        <div className="flex flex-wrap items-center gap-3">
          {result.kind === 'ok' && (
            <Button
              variant="primary"
              disabled={applying}
              onClick={() => {
                setApplying(true)
                void applyFromToday(templateId, componentId, qtyBySize).then((o) => {
                  setOutcome(o)
                  setApplying(false)
                })
              }}
            >
              {applying ? 'Applying…' : 'Apply from today'}
            </Button>
          )}
          <Button onClick={result.kind === 'ok' && outcome?.ok ? onApplied : onCancel}>
            {outcome?.ok ? 'Close' : 'Cancel'}
          </Button>
        </div>

        {/* Invariant 3, stated once and plainly. Not a tooltip. */}
        {result.kind === 'ok' && (
          <p className="mt-3 max-w-[70ch] text-[0.75rem] text-ink-3 leading-[16px]">
            <span className="text-ink font-semibold">Apply from today is the only option.</span>{' '}
            Recipe edits are effective-dated: today forward changes and history does not. Nothing
            you do here alters what last month&rsquo;s coffees cost or what margin they made —
            which is the only reason those figures can be trusted.
          </p>
        )}

        {outcome && (
          <p
            className={`mt-3 max-w-[70ch] text-[0.75rem] leading-[16px] ${
              outcome.ok ? 'text-ok' : 'text-warn'
            }`}
          >
            {outcome.message}
          </p>
        )}
      </div>
    </div>
  )
}
