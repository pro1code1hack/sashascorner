/**
 * Count history for one ingredient.
 *
 * This is where the drift sparkline went after the self-review (DESIGN.md §7).
 * As a 40px decoration in a table row it conveyed nothing; here it is a dated
 * column chart with the ±10% tuning band and the ±15% refusal line drawn as
 * labelled rules, so the shape answers an actual question — which counts crossed
 * which gate, and in which direction — rather than looking analytical.
 */
import { useQuery } from '@tanstack/react-query'
import { api } from '../lib/api'
import { isZero, mustDec } from '../lib/dec'
import { dayShort, pct, trimQty } from '../lib/format'
import { Label } from '../components/prim'

const SCALE = 25 // percent at the top and bottom of the plot
const H = 132

function y(pctValue: number): number {
  const clamped = Math.max(-SCALE, Math.min(SCALE, pctValue))
  return H / 2 - (clamped / SCALE) * (H / 2)
}

export function DriftHistory({ ingredientId }: { ingredientId: number }) {
  const q = useQuery({
    queryKey: ['stock-detail', ingredientId],
    queryFn: () => api.stockDetail(ingredientId),
    retry: false,
  })

  if (q.isPending) return <p className="text-ink-3 mt-2">Reading count history&hellip;</p>
  if (q.isError || !q.data) {
    return (
      <p className="text-ink-3 mt-2 max-w-[70ch] leading-[20px]">
        {(q.error as Error | undefined)?.message ?? 'Count history could not be read.'}
      </p>
    )
  }

  const { drift_history: history, suggested_waste_factor, row, templates_using } = q.data
  const obs = [...history].reverse() // oldest first, left to right
  const width = Math.max(obs.length * 46, 240)

  return (
    <div className="border-line-2 mt-3 border-t pt-3">
      <div className="flex flex-wrap items-baseline gap-x-4">
        <Label>
          {obs.length} counts · waste factor {trimQty(row.waste_factor)}
          {suggested_waste_factor !== null && (
            <> · suggested {trimQty(suggested_waste_factor)}</>
          )}
        </Label>
        <Label>
          used by {templates_using.length}{' '}
          {templates_using.length === 1 ? 'template' : 'templates'}
        </Label>
      </div>

      <div className="mt-2 overflow-x-auto">
        <svg
          width={width}
          height={H + 34}
          viewBox={`0 0 ${width} ${H + 34}`}
          role="img"
          aria-label={`Drift at each of the last ${obs.length} counts, against the 10 per cent tuning band and the 15 per cent refusal line`}
        >
          {/* the gate bands, as labelled rules — the reason the chart exists */}
          {[10, -10].map((v) => (
            <line
              key={`t${v}`}
              x1="0"
              x2={width}
              y1={y(v)}
              y2={y(v)}
              stroke="var(--color-line-2)"
              strokeDasharray="2 3"
            />
          ))}
          {[15, -15].map((v) => (
            <line
              key={`r${v}`}
              x1="0"
              x2={width}
              y1={y(v)}
              y2={y(v)}
              stroke="var(--color-bad-ink)"
              strokeDasharray="5 3"
            />
          ))}
          <line x1="0" x2={width} y1={y(0)} y2={y(0)} stroke="var(--color-ink)" />

          <text
            x={width - 2}
            y={y(15) - 3}
            textAnchor="end"
            className="fig"
            fontSize="10"
            fill="var(--color-bad-ink)"
          >
            15% auto-ordering refused
          </text>
          <text
            x={width - 2}
            y={y(10) - 3}
            textAnchor="end"
            className="fig"
            fontSize="10"
            fill="var(--color-ink-3)"
          >
            10% tuning band
          </text>

          {obs.map((o, i) => {
            const x = i * 46 + 10
            const top = Math.min(y(o.drift_pct), y(0))
            const h = Math.abs(y(o.drift_pct) - y(0))
            const over = Math.abs(o.drift_pct) >= 10
            const expired = !isZero(mustDec(o.expired_qty_in_window))
            return (
              <g key={o.observed_at}>
                <rect
                  x={x}
                  y={top}
                  width="22"
                  height={Math.max(h, 1)}
                  fill={over ? 'var(--color-bad-ink)' : 'var(--color-line-2)'}
                />
                {/* a count whose window contained expired stock is ticked, the
                    same form the attribution bars use for expiry */}
                {expired && (
                  <rect
                    x={x}
                    y={top}
                    width="22"
                    height={Math.max(h, 1)}
                    fill="url(#ticks)"
                    opacity="0.75"
                  />
                )}
                <text
                  x={x + 11}
                  y={H + 12}
                  textAnchor="middle"
                  className="fig"
                  fontSize="10"
                  fill="var(--color-ink-3)"
                >
                  {dayShort(o.observed_at)}
                </text>
                <text
                  x={x + 11}
                  y={H + 26}
                  textAnchor="middle"
                  className="fig"
                  fontSize="10"
                  fill={over ? 'var(--color-bad-ink)' : 'var(--color-ink-3)'}
                >
                  {pct(o.drift_pct, { sign: true })}
                </text>
              </g>
            )
          })}

          <defs>
            <pattern id="ticks" width="6" height="6" patternTransform="rotate(115)" patternUnits="userSpaceOnUse">
              <rect width="3" height="6" fill="var(--color-canvas)" />
            </pattern>
          </defs>
        </svg>
      </div>

      <p className="text-ink-3 mt-1 max-w-[74ch] leading-[20px]">
        A ticked column is a count whose window contained expired stock — the same form the
        attribution bars use, so expiry reads the same way everywhere. Counts above the
        zero rule found less than the ledger expected; counts below found more.
      </p>
    </div>
  )
}
