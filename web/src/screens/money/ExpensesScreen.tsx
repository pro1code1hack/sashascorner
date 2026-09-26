/**
 * Money → Expenses: everything paid out (finance.md 1.3).
 *
 * Rows edit in place. "Personal" (drawings) rows are mirrored once into the
 * director's account by the server; they never enter the P&L. A deleted row can
 * be undone for five seconds (the design deleted money rows with no way back).
 * "Needs a look" rows can be cleared with "Looks right".
 */
import { useCallback, useEffect, useRef, useState } from 'react'
import {
  Button,
  ErrorBox,
  FilterChip,
  Loading,
  PageBody,
  PageHeader,
  SearchInput,
  Select,
  TBody,
  THead,
  Table,
  Td,
  Th,
  Tr,
  cx,
} from '../../components/ui'
import { poundsToPence } from '../../components/confirm/numbers'
import { useLocation } from '../../lib/router'
import { useOperator } from '../../lib/operator'
import { financeWrite, useExpenses, useFinanceMeta, useInvalidateFinance } from '../../lib/finance-api'
import type { Expense, ExpenseCategory, ExpenseFilters, ExpenseIn, ExpenseKind, ExpenseMethod } from '../../lib/types/finance'
import {
  Caveats,
  DateCell,
  KIND_LABEL,
  METHOD_LABEL,
  MonthBar,
  MoneyCell,
  RemoveButton,
  SaveStatus,
  SelectCell,
  TextCell,
  UndoBar,
  committed,
  fd,
  gbp,
  londonToday,
  useFinancePeriod,
  useSaveStatus,
} from './shared'

const METHODS: ExpenseMethod[] = [
  'CARD',
  'BANK_TRANSFER',
  'DIRECT_DEBIT',
  'STANDING_ORDER',
  'CASH',
  'CASH_WITHDRAWAL',
  'OTHER',
]
const KINDS: ExpenseKind[] = ['OPERATING', 'CAPITAL', 'DRAWINGS']

interface Draft {
  date: string
  category_id: number
  description: string
  amount: string
  method: ExpenseMethod
  kind: ExpenseKind
  has_receipt: boolean
  notes: string
}

export function ExpensesScreen() {
  const { period, setPeriod, months } = useFinancePeriod()
  const loc = useLocation()
  const [filters, setFilters] = useState<ExpenseFilters>({
    q: '',
    category: 'all',
    kind: 'all',
    needsReview: loc.query.get('review') === '1',
    noReceipt: loc.query.get('noreceipt') === '1',
  })
  const reviewQ = loc.query.get('review')
  const receiptQ = loc.query.get('noreceipt')
  useEffect(() => {
    if (reviewQ === '1' || receiptQ === '1') {
      setFilters((f) => ({ ...f, needsReview: reviewQ === '1', noReceipt: receiptQ === '1' }))
    }
  }, [reviewQ, receiptQ])
  const [search, setSearch] = useState('')
  useEffect(() => {
    const t = window.setTimeout(() => setFilters((f) => ({ ...f, q: search })), 250)
    return () => window.clearTimeout(t)
  }, [search])

  const q = useExpenses(period, filters)
  const meta = useFinanceMeta()
  const save = useSaveStatus()
  const refresh = useInvalidateFinance()
  const [operator] = useOperator()
  const [draft, setDraft] = useState<Draft | null>(null)
  const [draftError, setDraftError] = useState<string | null>(null)
  const draftDesc = useRef<HTMLInputElement | null>(null)
  const [undo, setUndo] = useState<{ text: string; restore: () => void } | null>(null)
  const clearUndo = useCallback(() => setUndo(null), [])
  const categories = meta.data?.categories ?? []

  const patch = (id: number, body: ExpenseIn) =>
    committed(save, () => financeWrite.patchExpense(id, { ...body, operator }), refresh)

  function addExpense() {
    const today = londonToday()
    const date = period === 'all' || !period || today.startsWith(period) ? today : `${period}-01`
    const food = categories.find((c) => c.name === 'Stock — food') ?? categories[0]
    if (!food) return
    setFilters({ q: '', category: 'all', kind: 'all', needsReview: false, noReceipt: false })
    setSearch('')
    setDraftError(null)
    setDraft({
      date,
      category_id: food.id,
      description: '',
      amount: '',
      method: 'CARD',
      kind: 'OPERATING',
      has_receipt: false,
      notes: '',
    })
    window.setTimeout(() => draftDesc.current?.focus(), 0)
  }

  async function saveDraft() {
    if (!draft) return
    const amount = poundsToPence(draft.amount)
    if (!draft.description.trim()) return setDraftError('Say who was paid or what for.')
    if (amount.kind !== 'value' || amount.value <= 0) {
      return setDraftError(amount.kind === 'bad' ? amount.message : 'Enter an amount over £0.')
    }
    setDraftError(null)
    const ok = await committed(
      save,
      () =>
        financeWrite.createExpense({
          date: draft.date,
          category_id: draft.category_id,
          description: draft.description.trim(),
          amount_pence: amount.value,
          method: draft.method,
          kind: draft.kind,
          has_receipt: draft.has_receipt,
          notes: draft.notes.trim() || null,
          operator,
        }),
      refresh,
    )
    if (ok) setDraft(null)
  }

  async function remove(e: Expense) {
    const r = await save.run(() => financeWrite.deleteExpense(e.id, operator))
    if (r.kind !== 'ok') return
    await refresh()
    setUndo({
      text: `Deleted ${e.description}`,
      restore: () =>
        void committed(save, () => financeWrite.restoreExpense(e.id, r.data.undo_token, operator), async () => {
          setUndo(null)
          await refresh()
        }),
    })
  }

  return (
    <>
      <PageHeader
        title="Expenses"
        subtitle="everything paid out"
        saved={<SaveStatus state={save.state} />}
        actions={
          <Button variant="primary" onClick={addExpense} disabled={!meta.data}>
            + Add expense
          </Button>
        }
      />
      <MonthBar period={period} months={months} onChange={setPeriod} />
      <div className="flex flex-none flex-wrap items-center gap-2 border-b border-line px-4 py-2.5 sm:px-5">
        <SearchInput
          label="Search vendor or note"
          placeholder="Search vendor or note"
          value={search}
          onChange={(e) => setSearch(e.target.value)}
          className="h-9 w-full sm:w-[210px]"
        />
        <Select
          size="xs"
          aria-label="Category"
          value={filters.category}
          onChange={(e) => setFilters({ ...filters, category: e.target.value })}
          className="h-9 w-auto! max-w-full"
        >
          <option value="all">All categories</option>
          <option value="cogs">All stock</option>
          {categories.map((c) => (
            <option key={c.id} value={String(c.id)}>
              {c.name}
            </option>
          ))}
        </Select>
        <Select
          size="xs"
          aria-label="Type"
          value={filters.kind}
          onChange={(e) => setFilters({ ...filters, kind: e.target.value })}
          className="h-9 w-auto! max-w-full"
        >
          <option value="all">All types</option>
          <option value="OPERATING">Running costs</option>
          <option value="CAPITAL">Equipment / setup</option>
          <option value="DRAWINGS">Personal (drawings)</option>
        </Select>
        <FilterChip
          tone="alert"
          active={filters.needsReview}
          onClick={() => setFilters({ ...filters, needsReview: !filters.needsReview })}
        >
          Needs a look
        </FilterChip>
        <FilterChip active={filters.noReceipt} onClick={() => setFilters({ ...filters, noReceipt: !filters.noReceipt })}>
          No receipt
        </FilterChip>
        <span className="ml-auto text-md">
          {q.data ? (
            <>
              {q.data.count} {q.data.count === 1 ? 'expense' : 'expenses'} ·{' '}
              <strong className="fig">{gbp(q.data.total_pence)}</strong>
            </>
          ) : null}
        </span>
      </div>
      <PageBody flush>
        {q.isError ? (
          <ErrorBox error={q.error} what="expenses" />
        ) : !q.data || !meta.data ? (
          <Loading what="Loading expenses" />
        ) : (
          <div className="px-4 pb-6 pt-2 sm:px-5 compact:px-6">
            {undo && (
              <div className="mb-2 flex">
                <UndoBar text={undo.text} onUndo={undo.restore} onDone={clearUndo} />
              </div>
            )}
            <Caveats items={q.data.caveats} className="mb-2" />
            <Table header="sentence" stickyHeader minWidth={1120} label="Expenses">
              <THead>
                <tr>
                  <Th width={128}>Date</Th>
                  <Th>Category</Th>
                  <Th>Vendor / description</Th>
                  <Th numeric width={96}>
                    £
                  </Th>
                  <Th>Paid by</Th>
                  <Th>Type</Th>
                  <Th width={44}>Rcpt</Th>
                  <Th>Notes</Th>
                  <Th width={32} className="relative">
                    <span className="sr-only">Delete</span>
                  </Th>
                </tr>
              </THead>
              <TBody>
                {draft && (
                  <DraftRow
                    draft={draft}
                    setDraft={setDraft}
                    categories={categories}
                    descRef={draftDesc}
                    error={draftError}
                    onSave={() => void saveDraft()}
                    onCancel={() => setDraft(null)}
                  />
                )}
                {q.data.expenses.map((e) => (
                  <Tr key={e.id} flagged={e.needs_review}>
                    <Td>
                      <DateCell value={e.date} label={`Date of ${e.description}`} onCommit={(v) => patch(e.id, { date: v })} />
                    </Td>
                    <Td>
                      <SelectCell
                        value={String(e.category_id)}
                        label={`Category of ${e.description}`}
                        title={e.category}
                        onChange={(v) => void patch(e.id, { category_id: Number(v) })}
                      >
                        {categories.map((c) => (
                          <option key={c.id} value={String(c.id)}>
                            {c.name}
                          </option>
                        ))}
                      </SelectCell>
                    </Td>
                    <Td>
                      <TextCell
                        value={e.description}
                        required
                        label="Vendor or description"
                        onCommit={(v) => (v === null ? Promise.resolve(false) : patch(e.id, { description: v }))}
                      />
                    </Td>
                    <Td>
                      <MoneyCell
                        pence={e.amount_pence}
                        allowBlank={false}
                        label={`Amount of ${e.description}`}
                        onCommit={(v) => (v === null || v <= 0 ? Promise.resolve(false) : patch(e.id, { amount_pence: v }))}
                      />
                    </Td>
                    <Td>
                      <SelectCell
                        value={e.method ?? ''}
                        label={`Paid by, ${e.description}`}
                        onChange={(v) => void patch(e.id, { method: v === '' ? null : (v as ExpenseMethod) })}
                      >
                        {e.method === null && <option value="">—</option>}
                        {METHODS.map((m) => (
                          <option key={m} value={m}>
                            {METHOD_LABEL[m]}
                          </option>
                        ))}
                      </SelectCell>
                    </Td>
                    <Td>
                      <SelectCell
                        value={e.kind}
                        label={`Type of ${e.description}`}
                        title={e.kind === 'DRAWINGS' ? "Also shown on the director's account" : undefined}
                        onChange={(v) => void patch(e.id, { kind: v as ExpenseKind })}
                      >
                        {KINDS.map((k) => (
                          <option key={k} value={k}>
                            {KIND_LABEL[k]}
                          </option>
                        ))}
                      </SelectCell>
                    </Td>
                    <Td>
                      <ReceiptBox
                        checked={e.has_receipt}
                        label={`Receipt for ${e.description}`}
                        onToggle={() => void patch(e.id, { has_receipt: !e.has_receipt })}
                      />
                    </Td>
                    <Td>
                      <div className="flex items-center gap-1.5">
                        <div className="min-w-0 flex-1">
                          <TextCell
                            value={e.notes}
                            alert={e.needs_review}
                            label={`Notes on ${e.description}`}
                            onCommit={(v) => patch(e.id, { notes: v })}
                          />
                        </div>
                        {e.needs_review && (
                          <button
                            type="button"
                            onClick={() => void patch(e.id, { needs_review: false })}
                            className="flex-none whitespace-nowrap rounded-control px-1.5 py-1 text-sm font-bold text-ink-2 hover:bg-surface hover:text-ink"
                          >
                            Looks right
                          </button>
                        )}
                      </div>
                    </Td>
                    <Td remove>
                      <RemoveButton label={`Delete ${e.description}, ${fd(e.date)}`} onClick={() => void remove(e)} />
                    </Td>
                  </Tr>
                ))}
                {q.data.expenses.length === 0 && !draft && (
                  <tr>
                    <td colSpan={9} className="p-10 text-center text-md text-ink-2">
                      No expenses match these filters.
                    </td>
                  </tr>
                )}
              </TBody>
            </Table>
          </div>
        )}
      </PageBody>
    </>
  )
}

function ReceiptBox({ checked, label, onToggle }: { checked: boolean; label: string; onToggle: () => void }) {
  return (
    <button
      type="button"
      role="checkbox"
      aria-checked={checked}
      aria-label={label}
      onClick={onToggle}
      className={cx(
        'flex size-[18px] items-center justify-center rounded-xs border text-xs',
        checked ? 'border-brand bg-brand-wash text-brand-ink' : 'border-line-strong bg-surface',
      )}
    >
      {checked && <span aria-hidden="true">✓</span>}
    </button>
  )
}

const DRAFT_INPUT =
  'h-7 w-full min-w-0 rounded-control border border-line-strong bg-surface px-1.5 text-base outline-none focus-visible:border-brand focus-visible:ring-3 focus-visible:ring-brand-wash'

function DraftRow({
  draft,
  setDraft,
  categories,
  descRef,
  error,
  onSave,
  onCancel,
}: {
  draft: Draft
  setDraft: (d: Draft) => void
  categories: ExpenseCategory[]
  descRef: React.MutableRefObject<HTMLInputElement | null>
  error: string | null
  onSave: () => void
  onCancel: () => void
}) {
  const set = (p: Partial<Draft>) => setDraft({ ...draft, ...p })
  const enter = (e: React.KeyboardEvent) => e.key === 'Enter' && onSave()
  return (
    <>
      <tr className="border-b border-line bg-brand-wash">
        <td className="px-2 py-[6px] first:pl-0">
          <input type="date" aria-label="Date" value={draft.date} onChange={(e) => set({ date: e.target.value })} className={cx(DRAFT_INPUT, 'text-sm')} />
        </td>
        <td className="px-2 py-[6px]">
          <select aria-label="Category" value={draft.category_id} onChange={(e) => set({ category_id: Number(e.target.value) })} className={cx(DRAFT_INPUT, 'text-sm')}>
            {categories.map((c) => (
              <option key={c.id} value={c.id}>
                {c.name}
              </option>
            ))}
          </select>
        </td>
        <td className="px-2 py-[6px]">
          <input ref={descRef} aria-label="Vendor or description" placeholder="Who was paid" value={draft.description} onKeyDown={enter} onChange={(e) => set({ description: e.target.value })} className={DRAFT_INPUT} />
        </td>
        <td className="px-2 py-[6px]">
          <input aria-label="Amount in pounds" inputMode="decimal" placeholder="0.00" value={draft.amount} onKeyDown={enter} onChange={(e) => set({ amount: e.target.value })} className={cx(DRAFT_INPUT, 'fig text-right')} />
        </td>
        <td className="px-2 py-[6px]">
          <select aria-label="Paid by" value={draft.method} onChange={(e) => set({ method: e.target.value as ExpenseMethod })} className={cx(DRAFT_INPUT, 'text-sm')}>
            {METHODS.map((m) => (
              <option key={m} value={m}>
                {METHOD_LABEL[m]}
              </option>
            ))}
          </select>
        </td>
        <td className="px-2 py-[6px]">
          <select aria-label="Type" value={draft.kind} onChange={(e) => set({ kind: e.target.value as ExpenseKind })} className={cx(DRAFT_INPUT, 'text-sm')}>
            {KINDS.map((k) => (
              <option key={k} value={k}>
                {KIND_LABEL[k]}
              </option>
            ))}
          </select>
        </td>
        <td className="px-2 py-[6px]">
          <ReceiptBox checked={draft.has_receipt} label="Receipt" onToggle={() => set({ has_receipt: !draft.has_receipt })} />
        </td>
        <td className="px-2 py-[6px]">
          <input aria-label="Notes" placeholder="—" value={draft.notes} onKeyDown={enter} onChange={(e) => set({ notes: e.target.value })} className={DRAFT_INPUT} />
        </td>
        <td className="py-[6px] pl-2 last:pr-0">
          <RemoveButton label="Discard this expense" onClick={onCancel} />
        </td>
      </tr>
      <tr className="border-b border-line bg-brand-wash">
        <td colSpan={9} className="px-0 pb-2">
          <div className="flex flex-wrap items-center gap-2.5">
            <Button size="sm" variant="primary" onClick={onSave}>
              Add this expense
            </Button>
            <Button size="sm" variant="ghost" onClick={onCancel}>
              Cancel
            </Button>
            {error ? (
              <span role="alert" className="text-sm text-bad-ink">
                {error}
              </span>
            ) : (
              <span className="text-sm text-ink-2">Nothing is saved until it has a description and an amount.</span>
            )}
          </div>
        </td>
      </tr>
    </>
  )
}
