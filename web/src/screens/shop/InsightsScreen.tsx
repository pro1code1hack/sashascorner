/**
 * Online orders › Insights (`#/shop/insights`): how online ordering is doing over
 * 7 / 30 / 90 days (`GET /api/shop-admin/insights`, contract §10.B.1). The figures
 * row in the Sales dashboard's style ("vs previous period"), orders per day, by
 * hour, by weekday, top products and options, dining and payment splits, and
 * cancellations. Only COLLECTED orders count towards money; a window with no
 * collected orders says so in words, never £0.
 */
import { useMemo, useState } from 'react'
import type { ReactNode } from 'react'
import { useQuery } from '@tanstack/react-query'
import { Bars, Button, ErrorBox, Loading, PageBody, PageHeader, Segmented, Table, TBody, Td, Th, THead, Tr } from '../../components/ui'
import { LIVE } from '../../lib/api'
import { gbp } from '../../lib/format'
import { SHOP_KEY, shopGet } from '../../lib/shop-api'
import type { ShopInsights } from '../../lib/types/shop'
import { Figures, count } from '../money/filters'
import { hourLabel } from '../money/TillInsights'
import { dayMonth } from '../stock/fmt'
import { buildCsv, csvMoney, downloadCsv } from './csv'
import { Panel, ShopGate, plural } from './shared'

type Days = '7' | '30' | '90'
const WEEKDAYS = ['Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat', 'Sun']

function useShopInsights(days: number) {
  return useQuery({
    queryKey: [...SHOP_KEY, 'insights', days],
    queryFn: () => shopGet<ShopInsights>(`/insights?days=${days}`),
    enabled: LIVE,
    staleTime: 60 * 1000,
    placeholderData: (prev) => prev,
    retry: false,
  })
}

/** "▲ 12% up vs 1–30 Aug", or the honest words when there is nothing to compare. */
function Vs({ pct, label }: { pct: number | null; label: string }) {
  if (pct === null) return <span className="text-ink-3">no orders in the period before</span>
  const p = Math.round(pct)
  const glyph = p > 0 ? '▲' : p < 0 ? '▼' : '='
  return (
    <span className={p < 0 ? 'text-bad-ink' : 'text-ink-2'} title={`Compared with ${label}`}>
      <span aria-hidden="true">{glyph}</span> {p === 0 ? 'level' : `${Math.abs(p)}% ${p > 0 ? 'up' : 'down'}`}
      <span className="text-ink-3"> vs {label}</span>
    </span>
  )
}

export function InsightsScreen() {
  const [days, setDays] = useState<Days>('30')
  const q = useShopInsights(Number(days))
  const d = q.data
  return (
    <>
      <PageHeader
        title="Insights"
        subtitle="How online ordering is doing. Money counts collected orders only."
        actions={
          <span className="flex flex-wrap items-center gap-2">
            <Segmented<Days>
              label="Period"
              value={days}
              onChange={setDays}
              options={[
                { value: '7', label: '7 days' },
                { value: '30', label: '30 days' },
                { value: '90', label: '90 days' },
              ]}
            />
            <Button
              variant="outline"
              size="sm"
              disabled={!d || d.per_day.length === 0}
              onClick={() => {
                if (!d) return
                downloadCsv(
                  `online-orders-by-day_${d.period.from}_${d.period.to}.csv`,
                  buildCsv(
                    ['Date', 'Orders', 'Cancelled or rejected', 'Taken (collected orders)'],
                    // A day with no orders has nothing taken: the cell stays empty rather than £0.00.
                    d.per_day.map((r) => [r.date, String(r.orders), String(r.cancelled), r.orders === 0 ? '' : csvMoney(r.revenue_pence)]),
                  ),
                )
              }}
            >
              Download CSV
            </Button>
          </span>
        }
      />
      <PageBody className="compact:px-5">
        <ShopGate>
          {q.isPending && <Loading what="Reading insights" />}
          {q.isError && <ErrorBox error={q.error} what="online ordering insights" />}
          {d && <Body d={d} />}
        </ShopGate>
      </PageBody>
    </>
  )
}

function Body({ d }: { d: ShopInsights }) {
  const f = d.figures
  const prevLabel = `${dayMonth(d.previous.from)} – ${dayMonth(d.previous.to)}`
  const none = f.orders === 0
  const noneCollected = f.revenue_pence === 0 && f.avg_basket_pence === null
  const busiest = useMemo(() => d.by_hour.reduce<ShopInsights['by_hour'][number] | null>((a, h) => (h.orders > 0 && (a === null || h.orders > a.orders) ? h : a), null), [d.by_hour])
  const words = (n: number, what: string) => <span className="text-lg font-bold text-ink-2">{n === 0 ? `no ${what}` : count(n)}</span>
  // A missing figure is said in words where the number would go (invariant 9), never 0 or a dash.
  const none_ = (text: string) => <span className="text-lg font-bold text-ink-2">{text}</span>

  const figures: { label: string; value: ReactNode; sub?: ReactNode; strong?: boolean }[] = [
    { label: 'Orders', value: none ? words(0, 'orders') : count(f.orders), strong: true, sub: none ? `${dayMonth(d.period.from)} – ${dayMonth(d.period.to)}` : <Vs pct={f.vs_previous.orders_pct} label={prevLabel} /> },
    { label: 'Taken', value: noneCollected ? none_('nothing collected yet') : gbp(f.revenue_pence), sub: noneCollected ? 'money counts once collected' : <Vs pct={f.vs_previous.revenue_pct} label={prevLabel} /> },
    { label: 'Avg basket', value: f.avg_basket_pence === null ? none_('no collected orders') : gbp(f.avg_basket_pence), sub: f.avg_basket_pence === null ? 'per collected order' : <Vs pct={f.vs_previous.avg_basket_pct} label={prevLabel} /> },
    { label: 'Items', value: noneCollected ? none_('none collected') : count(f.items), sub: 'on collected orders' },
    { label: 'Members', value: f.members_share === null ? none_('no orders') : `${Math.round(f.members_share)}%`, sub: 'of orders from a Rewards member' },
    { label: 'Paid online', value: f.online_paid_share === null ? none_('no orders') : `${Math.round(f.online_paid_share)}%`, sub: 'the rest pay at the counter' },
    { label: 'To ready', value: f.avg_minutes_to_ready === null ? none_('nothing made yet') : `${Math.round(f.avg_minutes_to_ready)} min`, sub: 'placed to ready, average' },
    { label: 'Busiest hour', value: busiest ? `${hourLabel(busiest.hour)}–${hourLabel(busiest.hour + 1)}` : none_('no orders'), sub: busiest ? `${plural(busiest.orders, 'order')}` : 'by collection time' },
  ]

  const cancelled = f.cancelled + f.rejected
  const perDay = d.per_day.map((r) => ({
    key: r.date,
    label: d.period.days > 14 ? (r.date.endsWith('01') || r.date === d.period.from ? dayMonth(r.date) : '') : dayMonth(r.date).replace(/ .*/, ''),
    value: r.orders === 0 ? null : r.orders,
    valueLabel: r.orders === 0 ? undefined : String(r.orders),
    alert: r.cancelled > 0 && r.cancelled >= r.orders,
  }))

  return (
    <div className="flex flex-col gap-4">
      <Figures items={figures} />
      {d.caveats.length > 0 && (
        <ul className="list-disc pl-5 text-sm text-ink-2">
          {d.caveats.map((c) => (
            <li key={c}>{c}</li>
          ))}
        </ul>
      )}

      <Panel title="Orders per day">
        {none ? (
          <p className="text-base text-ink-2">No orders in this period. Days with none are drawn as dashed outlines, not as zero.</p>
        ) : (
          <Bars label="Orders per day" bars={perDay} height={140} />
        )}
        <p className="mt-2 text-sm text-ink-2">A red bar is a day where every order was cancelled or rejected.</p>
      </Panel>

      <div className="grid gap-4 wide:grid-cols-2">
        <Panel title="By hour">
          {none ? (
            <p className="text-base text-ink-2">No orders yet, so no hours to show.</p>
          ) : (
            <Bars
              label="Orders by hour of collection"
              height={120}
              bars={d.by_hour
                .filter((h) => h.hour >= 6 && h.hour <= 21)
                .map((h) => ({ key: String(h.hour), label: h.hour % 3 === 0 ? hourLabel(h.hour) : '', value: h.orders === 0 ? null : h.orders, valueLabel: h.orders ? String(h.orders) : undefined }))}
            />
          )}
          <p className="mt-2 text-sm text-ink-2">By the hour the order was due, 6am to 10pm.</p>
        </Panel>
        <Panel title="By weekday">
          {none ? (
            <p className="text-base text-ink-2">No orders yet, so no weekdays to show.</p>
          ) : (
            <Bars
              label="Orders by weekday"
              height={120}
              bars={d.by_weekday.map((w) => ({ key: String(w.weekday), label: WEEKDAYS[w.weekday] ?? String(w.weekday), value: w.orders === 0 ? null : w.orders, valueLabel: w.orders ? `${w.orders} · ${gbp(w.revenue_pence)}` : undefined }))}
            />
          )}
        </Panel>
      </div>

      <div className="grid gap-4 wide:grid-cols-2">
        <Panel title="Top products">
          {d.top_products.length === 0 ? (
            <p className="text-base text-ink-2">No collected orders yet, so nothing has sold online.</p>
          ) : (
            <Table density="dense" label="Top products by revenue">
              <THead>
                <tr>
                  <Th>Product</Th>
                  <Th numeric>Sold</Th>
                  <Th numeric>Taken</Th>
                </tr>
              </THead>
              <TBody>
                {d.top_products.map((p, i) => (
                  <Tr key={`${p.product_id ?? 'x'}-${i}`}>
                    <Td>{p.name}</Td>
                    <Td numeric>{count(p.qty)}</Td>
                    <Td numeric>{gbp(p.revenue_pence)}</Td>
                  </Tr>
                ))}
              </TBody>
            </Table>
          )}
        </Panel>
        <Panel title="Top options">
          {d.top_options.length === 0 ? (
            <p className="text-base text-ink-2">No options chosen on collected orders yet.</p>
          ) : (
            <Table density="dense" label="Top options by quantity">
              <THead>
                <tr>
                  <Th>Option</Th>
                  <Th>Group</Th>
                  <Th numeric>Times</Th>
                </tr>
              </THead>
              <TBody>
                {d.top_options.map((o, i) => (
                  <Tr key={`${o.group}-${o.name}-${i}`}>
                    <Td>{o.name}</Td>
                    <Td secondary>{o.group}</Td>
                    <Td numeric>{count(o.qty)}</Td>
                  </Tr>
                ))}
              </TBody>
            </Table>
          )}
        </Panel>
      </div>

      <div className="grid gap-4 sm:grid-cols-2 wide:grid-cols-3">
        <Panel title="Dining">
          {none ? (
            <p className="text-base text-ink-2">No orders yet.</p>
          ) : (
            <Bars
              label="Takeaway against eat in"
              height={90}
              bars={[
                { key: 'takeaway', label: 'Takeaway', value: d.dining.takeaway || null, valueLabel: d.dining.takeaway ? String(d.dining.takeaway) : undefined },
                { key: 'eat_in', label: 'Eat in', value: d.dining.eat_in || null, valueLabel: d.dining.eat_in ? String(d.dining.eat_in) : undefined },
              ]}
            />
          )}
        </Panel>
        <Panel title="Payment">
          {none ? (
            <p className="text-base text-ink-2">No orders yet.</p>
          ) : (
            <Bars
              label="Paid at the counter against online"
              height={90}
              bars={[
                { key: 'counter', label: 'At the counter', value: d.payment.counter || null, valueLabel: d.payment.counter ? String(d.payment.counter) : undefined },
                { key: 'online', label: 'Online', value: d.payment.online || null, valueLabel: d.payment.online ? String(d.payment.online) : undefined },
              ]}
            />
          )}
        </Panel>
        <Panel title="Cancellations">
          {cancelled === 0 ? (
            <p className="text-base text-ink-2">{none ? 'No orders yet.' : 'None: every order in this period went through.'}</p>
          ) : (
            <dl className="flex flex-col gap-1 text-base">
              <div className="flex justify-between border-b border-dashed border-line py-1">
                <dt>Cancelled</dt>
                <dd className="fig">{count(f.cancelled)}</dd>
              </div>
              <div className="flex justify-between border-b border-dashed border-line py-1">
                <dt>Rejected by the counter</dt>
                <dd className="fig">{count(f.rejected)}</dd>
              </div>
              <div className="flex justify-between py-1 font-bold">
                <dt>Of {plural(f.orders, 'order')}</dt>
                <dd className="fig">{f.orders ? `${Math.round((cancelled / f.orders) * 100)}%` : 'no orders'}</dd>
              </div>
            </dl>
          )}
          <p className="mt-2 text-sm text-ink-2">
            Right now: {d.status_now.new} new · {d.status_now.accepted} accepted · {d.status_now.preparing} preparing · {d.status_now.ready} ready.
          </p>
        </Panel>
      </div>
    </div>
  )
}
