/**
 * Suppliers (stock-orders-suppliers.md §3): who we buy from, and on what terms.
 *
 * The selected supplier is in the hash (`#/suppliers/3`), so a reload keeps it.
 * Profile fields save on blur (C20). Terms save only together, through
 * "Confirm these terms" (C3). "Delete" archives (C12). The header's saved
 * label shows the last write's result, or the server's refusal verbatim.
 */
import { useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import { OperatorNeeded } from '../../components/shell/Operator'
import { Button, Empty, ErrorBox, Input, Loading, PageHeader, Select } from '../../components/ui'
import { useOperator } from '../../lib/operator'
import { navigate, useLocation } from '../../lib/router'
import { KEYS, stockApi, supplierWrites } from '../../lib/stock-api'
import type { OrderChannel } from '../../lib/types/stock'
import { RailChips, RailColumn } from '../stock/Rail'
import type { RailItem } from '../stock/Rail'
import { OutcomeLine, useWrite } from '../stock/writes'
import { SupplierPage } from './SupplierPage'
import { CHANNELS } from './vocab'

export type Saved = { tone: 'ok' | 'bad'; text: string } | null

export function SuppliersScreen() {
  const loc = useLocation()
  const q = useQuery({ queryKey: KEYS.suppliers, queryFn: stockApi.suppliers, staleTime: 60_000 })
  const [adding, setAdding] = useState(false)
  const [saved, setSaved] = useState<Saved>(null)
  const list = q.data ?? []
  const wanted = Number(loc.segments[1])
  const selected = Number.isInteger(wanted) && list.some((s) => s.supplier_id === wanted) ? wanted : (list[0]?.supplier_id ?? null)

  const items: RailItem[] = [
    { kind: 'head', label: 'Suppliers' },
    ...list.map(
      (s): RailItem => ({
        kind: 'row',
        key: String(s.supplier_id),
        label: s.name,
        count: s.product_count ?? undefined,
        active: !adding && s.supplier_id === selected,
        onSelect: () => {
          setAdding(false)
          setSaved(null)
          navigate(`/suppliers/${s.supplier_id}`)
        },
      }),
    ),
  ]

  return (
    <>
      <PageHeader
        title="Suppliers"
        subtitle="who we buy from, and on what terms"
        saved={
          saved && (
            <span role={saved.tone === 'bad' ? 'alert' : 'status'} className={saved.tone === 'bad' ? 'text-bad-ink' : 'text-ink-2'}>
              {saved.text}
            </span>
          )
        }
        actions={
          <Button variant="primary" onClick={() => setAdding(true)}>
            + Add supplier
          </Button>
        }
      />
      <div className="flex min-h-0 min-w-0 flex-1">
        <RailColumn items={items} label="Suppliers" />
        <div className="flex min-h-0 min-w-0 flex-1 flex-col">
          <RailChips items={items} label="Suppliers" />
          <div className="min-h-0 min-w-0 flex-1 overflow-y-auto px-4 py-4.5 sm:px-5.5">
            {q.isPending ? (
              <Loading what="Reading suppliers" />
            ) : q.isError ? (
              <ErrorBox error={q.error} what="suppliers" />
            ) : adding ? (
              <AddSupplier
                onDone={(id) => {
                  setAdding(false)
                  if (id !== null) navigate(`/suppliers/${id}`)
                }}
              />
            ) : selected === null ? (
              <Empty roomy>Pick a supplier, or add one.</Empty>
            ) : (
              <SupplierPage key={selected} supplierId={selected} onSaved={setSaved} />
            )}
          </div>
        </div>
      </div>
    </>
  )
}

function AddSupplier({ onDone }: { onDone: (id: number | null) => void }) {
  const [operator] = useOperator()
  const [name, setName] = useState('')
  const [channel, setChannel] = useState<OrderChannel>('MANUAL')
  const w = useWrite()
  const submit = async () => {
    if (operator === null || name.trim() === '') return
    await w.run(() => supplierWrites.create({ name: name.trim(), created_by: operator, order_channel: channel }), {
      invalidate: [KEYS.suppliers, KEYS.draft],
      after: (r) => onDone(r.supplier.supplier_id),
    })
  }
  return (
    <div className="flex max-w-[520px] flex-col gap-3">
      <h2 className="text-2xl font-extrabold">Add a supplier</h2>
      <p className="text-base text-ink-2">
        A new supplier starts with its terms marked as a guess. Confirm them with the supplier on its page before
        trusting any order built on them.
      </p>
      <label className="flex flex-col gap-1 text-base text-ink-2">
        Name
        <Input autoFocus value={name} onChange={(e) => setName(e.target.value)} placeholder="e.g. Highland Dairies" />
      </label>
      <label className="flex flex-col gap-1 text-base text-ink-2">
        How we order
        <Select value={channel} onChange={(e) => setChannel(e.target.value as OrderChannel)}>
          {CHANNELS.map((c) => (
            <option key={c.value} value={c.value}>
              {c.label}
            </option>
          ))}
        </Select>
      </label>
      {operator === null && <OperatorNeeded what="add a supplier" />}
      <div className="flex gap-2">
        <Button
          variant="primary"
          disabled={operator === null || name.trim() === ''}
          pending={w.pending}
          pendingLabel="Adding…"
          onClick={submit}
        >
          Add supplier
        </Button>
        <Button variant="ghost" onClick={() => onDone(null)}>
          Cancel
        </Button>
      </div>
      <OutcomeLine outcome={w.outcome} />
    </div>
  )
}
