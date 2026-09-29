/**
 * One ingredient's stock as a full page, `#/stock/<ingredient id>`, laid out
 * like the menu item page (owner, 2026-09-26: "the stock itself should be
 * same as menu"):
 *
 *   main    on the shelf (worked out vs counted vs runs out: invariant 6 by
 *           label, weight and style), counts with drift, batches
 *   side    record a delivery or a write-off; tier, how long it keeps,
 *           reorder level
 *
 * The main column comes first in the DOM (a phone reads the figures before the
 * forms; the outline runs h1 → main h2s → side h2s); at `xl` the side column
 * sits to the right by grid order alone.
 *
 * The Stock screen stays mounted underneath (same route), so "‹ Stock" returns
 * to the list with its filters as they were.
 */
import { useEffect, useMemo, useRef, useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import { Empty, ErrorBox, LinkButton, Loading } from '../../components/ui'
import { KEYS, stockApi } from '../../lib/stock-api'
import { CountFlow } from './CountFlow'
import { Batches, ChecklistSection, Counts, DeliveryForm, KeepsForm, Overview, Panel, TierPicker, WriteOffForm } from './StockItemSections'
import { StockTrust } from './StockList'
import { IngredientThumb } from '../ingredients/Thumb'

export function StockItemPage({ id, back }: { id: number; back: string }) {
  const q = useQuery({ queryKey: KEYS.stock, queryFn: stockApi.stock, staleTime: 60_000 })
  const [counting, setCounting] = useState(false)
  const row = q.data?.rows.find((r) => r.ingredient_id === id) ?? null
  const byId = useMemo(() => new Map((q.data?.rows ?? []).map((r) => [r.ingredient_id, r])), [q.data])
  // When the count flow closes, focus returns to the button that opened it
  // rather than falling to <body>.
  const countRef = useRef<HTMLButtonElement>(null)
  const wasCounting = useRef(false)
  useEffect(() => {
    if (wasCounting.current && !counting) countRef.current?.focus()
    wasCounting.current = counting
  }, [counting])

  return (
    <>
      <header className="flex flex-none flex-wrap items-center gap-x-3 gap-y-1 border-b border-line px-4 py-3 sm:px-5">
        <a href={back} className="text-base font-bold text-brand-ink no-underline hover:underline">
          ‹ Stock
        </a>
        <span aria-hidden="true" className="text-ink-3">
          /
        </span>
        {row?.photo_url && (
          <IngredientThumb url={row.photo_url} name={row.name} decorative fallback={null} className="size-9 rounded-control" />
        )}
        <h1 className="min-w-0 flex-1 truncate text-2xl font-extrabold tracking-[-.01em]">{row?.name ?? 'Ingredient'}</h1>
        {row && (
          <span className="flex items-center gap-2 text-base text-ink-2">
            <span className="max-sm:hidden">
              {row.category ?? 'Uncategorised'} · Tier {row.tier}
            </span>
            <StockTrust row={row} />
          </span>
        )}
      </header>
      <div className="min-h-0 flex-1 overflow-y-auto bg-canvas">
        <div className="flex flex-col gap-4 px-4 pb-10 pt-4 sm:px-6 2xl:px-8">
          {q.isPending && <Loading what="Working out stock" />}
          {q.isError && <ErrorBox error={q.error} what="stock" />}
          {q.data && !row && (
            <Empty action={<LinkButton href={back}>Back to stock</LinkButton>}>
              This ingredient isn’t on the stock list. It may have been retired, or the link is old.
            </Empty>
          )}
          {row && counting && <CountFlow queue={[row.ingredient_id]} byId={byId} onDone={() => setCounting(false)} />}
          {row && !counting && (
            <div className="grid items-start gap-4 xl:grid-cols-[minmax(0,1fr)_360px] 2xl:grid-cols-[minmax(0,1fr)_420px]">
              <div className="flex min-w-0 flex-col gap-4">
                {row.tier === 'C' ? (
                  <Panel>
                    <ChecklistSection row={row} />
                  </Panel>
                ) : (
                  <>
                    <Panel>
                      <Overview row={row} />
                    </Panel>
                    <Panel>
                      <Counts row={row} onCountNow={() => setCounting(true)} countRef={countRef} />
                    </Panel>
                    <Panel>
                      <Batches row={row} />
                    </Panel>
                  </>
                )}
              </div>
              <aside className="flex flex-col gap-4 xl:sticky xl:top-4">
                {row.tier !== 'C' && (
                  <Panel>
                    <div className="flex flex-col gap-5">
                      <DeliveryForm row={row} />
                      <div className="border-t border-line" />
                      <WriteOffForm row={row} />
                    </div>
                  </Panel>
                )}
                <Panel>
                  <div className="flex flex-col gap-5">
                    <TierPicker row={row} />
                    <div className="border-t border-line" />
                    <KeepsForm row={row} />
                  </div>
                </Panel>
              </aside>
            </div>
          )}
        </div>
      </div>
    </>
  )
}
