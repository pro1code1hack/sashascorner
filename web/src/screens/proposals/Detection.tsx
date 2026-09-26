/**
 * What detection actually proposes: a per-size component grid, the variant axis
 * that fills the slot the grid leaves empty, and the base items the pattern
 * would replace.
 *
 * The grid is the headline of the whole project made concrete — 66 menu items,
 * ten components, one axis of fifteen flavours. That is one template, and the
 * 66 rows somebody currently maintains by hand are its leaves.
 *
 * Quantities are printed exactly as sent and never parsed to a float, and a
 * blank cell is not a zero: a 12oz cup appears at S and M and does not exist at
 * XL, which the grid says in words rather than leaving to inference.
 */
import { Badge, Cell, Chip, ScrollX, Table, Td, Th } from '../../components/ui'
import { Label, Qty } from '../../components/prim'
import { plural } from '../../lib/format'
import type { Proposal } from '../../lib/types'
import { axisForRole, orderSizes, roleLabel } from './shape'

function NotUsed() {
  return <span className="text-[0.6875rem] text-ink-5">not used</span>
}

export function ComponentGrid({ p }: { p: Proposal }) {
  const sizes = orderSizes(p.sizes)
  if (p.components.length === 0) {
    return (
      <p className="text-[0.75rem] leading-[16px] text-ink-3">
        Detection found no component the items in this group share, so there is nothing to put in
        a grid. That is the finding, not a gap in the screen.
      </p>
    )
  }
  return (
    <ScrollX>
      <Table>
        <thead>
          <tr>
            <Th>Slot</Th>
            {sizes.map((s) => (
              <Th key={s} align="right">
                {s}
              </Th>
            ))}
          </tr>
        </thead>
        <tbody>
          {p.components.map((c, i) => {
            const axis = c.ingredient_name === null ? axisForRole(p, c.role) : undefined
            const top =
              c.ingredient_name ??
              (axis ? `any ${axis.name} option` : `filled by the ${roleLabel(c.role)} slot`)
            return (
              <tr key={`${c.role}-${c.ingredient_name ?? 'axis'}-${i}`}>
                <Td>
                  <div className="min-w-[10rem]">
                    <Cell
                      wrap
                      top={top}
                      sub={
                        axis
                          ? `${roleLabel(c.role)} · the axis chooses the ingredient`
                          : roleLabel(c.role)
                      }
                    />
                  </div>
                </Td>
                {sizes.map((s) => {
                  const q = c.qty_by_size[s]
                  return (
                    <Td key={s} align="right">
                      {q === undefined ? <NotUsed /> : <Qty value={q} />}
                    </Td>
                  )
                })}
              </tr>
            )
          })}
        </tbody>
      </Table>
    </ScrollX>
  )
}

export function Axes({ p }: { p: Proposal }) {
  if (p.axes.length === 0) return null
  return (
    <div className="grid gap-3">
      {p.axes.map((a) => {
        const options = Object.entries(a.options)
        return (
          <div key={`${a.name}-${a.role}`} className="rounded-control border border-line bg-sunk p-3">
            <div className="flex flex-wrap items-baseline gap-x-2 gap-y-1">
              <span className="text-[0.875rem] font-medium text-ink">{a.name}</span>
              <Badge tone="muted">{roleLabel(a.role)} slot</Badge>
              <Label>
                {options.length} {plural(options.length, 'option')}
              </Label>
              {options.length !== a.option_count && (
                <Label tone="warn">
                  the payload counts {a.option_count}; {options.length} are listed
                </Label>
              )}
            </div>
            <ul className="mt-2 grid grid-cols-[minmax(0,1fr)] gap-x-5 gap-y-1 [&>*]:min-w-0 sm:grid-cols-2">
              {options.map(([name, ingredient]) => (
                <li key={name} className="text-[0.75rem] leading-[16px]">
                  <span className="text-ink-2">{name}</span>
                  <span className="text-ink-5" aria-hidden>
                    {' → '}
                  </span>
                  <span className="sr-only">maps to</span>
                  <span className="text-ink-4">{ingredient}</span>
                </li>
              ))}
            </ul>
          </div>
        )
      })}
    </div>
  )
}

export function BaseItems({ names, limit = 10 }: { names: readonly string[]; limit?: number }) {
  if (names.length === 0) return null
  const head = names.slice(0, limit)
  const tail = names.slice(limit)
  return (
    <div>
      <div className="flex flex-wrap gap-1">
        {head.map((n) => (
          <Chip key={n} tone="muted">
            {n}
          </Chip>
        ))}
      </div>
      {tail.length > 0 && (
        <details className="mt-1.5">
          <summary className="cursor-pointer text-[0.6875rem] text-ink-4 hover:text-ink-2">
            and {tail.length} more {plural(tail.length, 'item')}
          </summary>
          <div className="mt-1.5 flex flex-wrap gap-1">
            {tail.map((n) => (
              <Chip key={n} tone="muted">
                {n}
              </Chip>
            ))}
          </div>
        </details>
      )}
    </div>
  )
}
