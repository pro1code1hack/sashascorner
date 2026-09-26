/**
 * One ingredient's stock as a full page, `#/stock/<ingredient id>` (owner,
 * 2026-09-26: "one separate page, not a slider on the right"). Replaces the
 * 400px drawer; the sections are the drawer's own (StockDrawer.tsx), laid out
 * like the menu item and order pages:
 *
 *   top     what we think is there (italic, worked out) beside the last count
 *           (upright, dated), drift and trust: invariant 6 by position and weight
 *   main    batches, count history (+ count it now)
 *   side    tier, record a delivery or a write-off, how long it keeps
 *
 * The Stock screen stays mounted underneath (same route), so "‹ Stock" returns
 * to the list with its filters as they were.
 */
import { useState } from 'react'
import type { ReactNode } from 'react'
import { useQuery } from '@tanstack/react-query'
import { ErrorBox, Loading, TierBadge } from '../../components/ui'
import { href } from '../../lib/router'
import { KEYS, stockApi } from '../../lib/stock-api'
import { CountFlow } from './CountFlow'
import { Batches, ChecklistCard, CountHistory, DeliveryRow, EstimateCard, Keeps, LastCountCard, TierRow, WriteOffRow } from './StockDrawer'
import { StockTrust } from './StockList'
import { runsOutCell } from './model'

export function StockItemPage({ id, back }: { id: number; back: string }) {
  const q = useQuery({ queryKey: KEYS.stock, queryFn: stockApi.stock, staleTime: 60_000 })
  const [counting, setCounting] = useState(false)
  const row = q.data?.rows.find((r) => r.ingredient_id === id) ?? null
  const byId = new Map((q.data?.rows ?? []).map((r) => [r.ingredient_id, r]))
  const ro = row ? runsOutCell(row) : null

  return (
    <>
      <header className="flex flex-none flex-wrap items-center gap-x-3 gap-y-1 border-b border-line px-4 py-3 sm:px-5">
        <a href={back} className="text-base font-bold text-brand-ink no-underline hover:underline">
          ‹ Stock
        </a>
        <span aria-hidden="true" className="text-ink-3">
          /
        </span>
        <h1 className="min-w-0 flex-1 truncate text-2xl font-extrabold tracking-[-.01em]">{row?.name ?? 'Ingredient'}</h1>
        {row && (
          <span className="flex items-center gap-2 text-base text-ink-2">
            <TierBadge tier={row.tier} />
            {row.category ?? 'Uncategorised'}
            <StockTrust row={row} />
          </span>
        )}
      </header>
      <div className="min-h-0 flex-1 overflow-y-auto bg-canvas">
        <div className="flex flex-col gap-4 px-4 pb-10 pt-4 sm:px-6 2xl:px-8">
          {q.isPending && <Loading what="Working out stock" />}
          {q.isError && <ErrorBox error={q.error} what="stock" />}
          {q.data && !row && <p className="text-base text-ink-2">No ingredient {id} in stock.</p>}
          {row && counting && <CountFlow queue={[row.ingredient_id]} byId={byId} onDone={() => setCounting(false)} />}
          {row && !counting && (
            <div className="grid items-start gap-4 xl:grid-cols-[minmax(0,1fr)_380px] 2xl:grid-cols-[minmax(0,1fr)_440px]">
              <aside className="flex flex-col gap-4 xl:sticky xl:top-4 xl:order-last">
                <Panel>
                  <TierRow row={row} />
                </Panel>
                {row.tier !== 'C' && (
                  <Panel title="Record">
                    <div className="flex flex-col gap-3">
                      <DeliveryRow row={row} />
                      <WriteOffRow row={row} />
                    </div>
                  </Panel>
                )}
                <Panel>
                  <Keeps row={row} />
                </Panel>
              </aside>
              <div className="flex min-w-0 flex-col gap-4">
                {row.tier === 'C' ? (
                  <Panel>
                    <ChecklistCard row={row} />
                  </Panel>
                ) : (
                  <>
                    <div className="grid gap-4 md:grid-cols-2">
                      <Bare>
                        <EstimateCard row={row} />
                      </Bare>
                      <Bare>
                        <LastCountCard row={row} />
                      </Bare>
                    </div>
                    {ro?.text && (
                      <Panel>
                        <div className="flex flex-wrap items-baseline gap-x-3">
                          <span className="text-sm font-bold text-ink-2">Runs out</span>
                          <span className={ro.alert ? 'text-lg font-bold text-bad-ink' : 'text-lg font-bold'} title={ro.title}>
                            {ro.text}
                          </span>
                          <span className="flex-1" />
                          <a href={href('/stock', { tab: 'buy' })} className="text-base font-bold text-brand-ink">
                            What to buy ›
                          </a>
                        </div>
                      </Panel>
                    )}
                    <Panel>
                      <Batches row={row} />
                    </Panel>
                    <Panel>
                      <CountHistory row={row} onCountNow={() => setCounting(true)} />
                    </Panel>
                  </>
                )}
              </div>
            </div>
          )}
        </div>
      </div>
    </>
  )
}

function Panel({ title, children }: { title?: string; children: ReactNode }) {
  return (
    <div className="rounded-card-lg bg-surface p-4 shadow-raised sm:p-5">
      {title && <h2 className="mb-3 text-lg font-extrabold">{title}</h2>}
      {children}
    </div>
  )
}

/** The drawer's two bordered cards, made into page panels without a double border. */
function Bare({ children }: { children: ReactNode }) {
  return <div className="h-full rounded-card-lg bg-surface shadow-raised [&>section]:h-full [&>section]:border-0 [&>section]:p-5">{children}</div>
}
