/**
 * Money → Expenses: everything paid out, read-only first.
 *
 * The table only reads. A row opens a drawer to edit it; "+ Add expense" opens
 * the same drawer empty. "Personal" (drawings) rows are mirrored once into the
 * director's account by the server and never enter the P&L. A delete can be
 * undone for ten seconds.
 *
 * Server filters: period, search, category, type, needs-a-look, no receipt.
 * On top of those, in the browser: a custom date range, payee, paid-by and a
 * £ range. Totals are summed from the rows shown, in integer pence.
 */
import { useCallback, useEffect, useMemo, useState } from 'react'
import type { ReactNode } from 'react'
import {
  ActiveFilters,
  Button,
  Checkbox,
  Drawer,
  ErrorBox,
  Field,
  FilterBar,
  FilterSelect,
  FilterToggle,
  Input,
  Loading,
  MoneyInput,
  PageBody,
  PageHeader,
  SearchInput,
  Select,
  StatusLine,
  TBody,
  THead,
  Table,
  Td,
  Textarea,
  Th,
  Tr,
  cx,
} from '../../components/ui'
import type { ActiveFilterChip, Outcome } from '../../components/ui'
import { poundsToPence, penceToPounds } from '../../components/confirm/numbers'
import { useLocation } from '../../lib/router'
import { useOperator } from '../../lib/operator'
import { financeWrite, useExpenses, useFinanceMeta, useInvalidateFinance } from '../../lib/finance-api'
import type { Expense, ExpenseCategory, ExpenseFilters, ExpenseIn, ExpenseKind, ExpenseMethod } from '../../lib/types/finance'
import { AmountRange, Figures, PeriodBar, count, inPence, inRange, isWeekend, shortDate } from './filters'
import type { DateRange, PenceRange } from './filters'
import { KIND_LABEL, METHOD_LABEL, UndoBar, fd, gbp, londonToday, mLabel, useFinancePeriod } from './shared'

const METHODS: ExpenseMethod[] = ['CARD', 'BANK_TRANSFER', 'DIRECT_DEBIT', 'STANDING_ORDER', 'CASH', 'CASH_WITHDRAWAL', 'OTHER']
const KINDS: ExpenseKind[] = ['OPERATING', 'CAPITAL', 'DRAWINGS']

interface Sums {
  n: number
  total: number
  stock: number
  running: number
  capital: number
  personal: number
}

function sum(rows: readonly Expense[]): Sums {
  const s: Sums = { n: rows.length, total: 0, stock: 0, running: 0, capital: 0, personal: 0 }
  for (const e of rows) {
    s.total += e.amount_pence
    if (e.kind === 'CAPITAL') s.capital += e.amount_pence
    else if (e.kind === 'DRAWINGS') s.personal += e.amount_pence
    else if (e.group === 'COGS') s.stock += e.amount_pence
    else s.running += e.amount_pence
  }
  return s
}

type DrawerState = { kind: 'add' } | { kind: 'edit'; id: number } | null

export function ExpensesScreen() {
  const { period, setPeriod, months } = useFinancePeriod()
  const loc = useLocation()
  const [range, setRange] = useState<DateRange | null>(null)
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
  const [payee, setPayee] = useState('all')
  const [method, setMethod] = useState('all')
  const [amount, setAmount] = useState<PenceRange>({ min: null, max: null })
  const [drawer, setDrawer] = useState<DrawerState>(null)
  const [undo, setUndo] = useState<{ text: string; restore: (() => void) | null } | null>(null)
  const clearUndo = useCallback(() => setUndo(null), [])

  const q = useExpenses(range ? 'all' : period, filters)
  const meta = useFinanceMeta()
  const categories = meta.data?.categories ?? []

  const inWindow = useMemo(() => (q.data?.expenses ?? []).filter((e) => inRange(e.date, range)), [q.data, range])
  const payees = useMemo(() => {
    const n = new Map<string, number>()
    for (const e of inWindow) n.set(e.description, (n.get(e.description) ?? 0) + 1)
    return [...n.entries()].sort((a, b) => b[1] - a[1] || a[0].localeCompare(b[0]))
  }, [inWindow])
  const rows = useMemo(
    () =>
      inWindow
        .filter(
          (e) =>
            (payee === 'all' || e.description === payee) &&
            (method === 'all' || (e.method ?? 'none') === method) &&
            inPence(e.amount_pence, amount),
        )
        .sort((a, b) => (a.date < b.date ? 1 : a.date > b.date ? -1 : b.id - a.id)),
    [inWindow, payee, method, amount],
  )
  const totals = useMemo(() => sum(rows), [rows])
  const multiMonth = useMemo(() => new Set(rows.map((e) => e.date.slice(0, 7))).size > 1, [rows])

  const catLabel = (v: string) =>
    v === 'cogs' ? 'All stock' : (categories.find((c) => String(c.id) === v)?.name ?? v)
  const chips: ActiveFilterChip[] = []
  if (range)
    chips.push({ key: 'range', label: `${shortDate(range.from)} – ${shortDate(range.to)}`, onRemove: () => setRange(null) })
  if (filters.q) chips.push({ key: 'q', label: `“${filters.q}”`, onRemove: () => setSearch('') })
  if (filters.category !== 'all')
    chips.push({ key: 'cat', label: catLabel(filters.category), onRemove: () => setFilters((f) => ({ ...f, category: 'all' })) })
  if (filters.kind !== 'all')
    chips.push({
      key: 'kind',
      label: KIND_LABEL[filters.kind] ?? filters.kind,
      onRemove: () => setFilters((f) => ({ ...f, kind: 'all' })),
    })
  if (payee !== 'all') chips.push({ key: 'payee', label: payee, onRemove: () => setPayee('all') })
  if (method !== 'all')
    chips.push({ key: 'method', label: METHOD_LABEL[method] ?? 'Not recorded', onRemove: () => setMethod('all') })
  if (amount.min !== null || amount.max !== null)
    chips.push({
      key: 'amount',
      label: `${amount.min !== null ? gbp(amount.min) : '£0.00'} – ${amount.max !== null ? gbp(amount.max) : 'any'}`,
      onRemove: () => setAmount({ min: null, max: null }),
    })
  if (filters.needsReview)
    chips.push({ key: 'rev', label: 'Needs a look', onRemove: () => setFilters((f) => ({ ...f, needsReview: false })) })
  if (filters.noReceipt)
    chips.push({ key: 'rcpt', label: 'No receipt', onRemove: () => setFilters((f) => ({ ...f, noReceipt: false })) })
  const clearAll = () => {
    setRange(null)
    setSearch('')
    setFilters({ q: '', category: 'all', kind: 'all', needsReview: false, noReceipt: false })
    setPayee('all')
    setMethod('all')
    setAmount({ min: null, max: null })
  }

  const editing = drawer?.kind === 'edit' ? ((q.data?.expenses ?? []).find((e) => e.id === drawer.id) ?? null) : null

  return (
    <>
      <PageHeader
        title="Expenses"
        subtitle="everything paid out"
        actions={
          <Button variant="primary" onClick={() => setDrawer({ kind: 'add' })} disabled={!meta.data}>
            + Add expense
          </Button>
        }
      />
      <PeriodBar period={period} months={months} onPeriod={setPeriod} range={range} onRange={setRange} />
      <div className="flex flex-none flex-col gap-2 border-b border-line px-4 py-2.5 sm:px-5">
        <FilterBar
          activeCount={chips.length}
          label="Filter expenses"
          search={
            <SearchInput
              label="Search payee or note"
              placeholder="Search payee or note"
              value={search}
              onChange={(e) => setSearch(e.target.value)}
              className="h-[34px]"
            />
          }
        >
          <FilterSelect
            label="Category"
            value={filters.category}
            onChange={(v) => setFilters((f) => ({ ...f, category: v }))}
            options={[
              { value: 'all', label: 'All categories' },
              { value: 'cogs', label: 'All stock' },
              ...categories.map((c) => ({ value: String(c.id), label: c.name })),
            ]}
          />
          <FilterSelect
            label="Payee"
            value={payee}
            onChange={setPayee}
            className="w-[190px]"
            options={[
              { value: 'all', label: 'All payees' },
              ...(payee !== 'all' && !payees.some(([p]) => p === payee) ? [{ value: payee, label: payee }] : []),
              ...payees.map(([p, n]) => ({ value: p, label: `${p} (${n})` })),
            ]}
          />
          <FilterSelect
            label="Type"
            value={filters.kind}
            onChange={(v) => setFilters((f) => ({ ...f, kind: v }))}
            options={[
              { value: 'all', label: 'All types' },
              { value: 'OPERATING', label: 'Running costs' },
              { value: 'CAPITAL', label: 'Equipment / setup' },
              { value: 'DRAWINGS', label: 'Personal (drawings)' },
            ]}
          />
          <FilterSelect
            label="Paid by"
            value={method}
            onChange={setMethod}
            options={[
              { value: 'all', label: 'Any payment' },
              ...METHODS.map((m) => ({ value: m, label: METHOD_LABEL[m] ?? m })),
              { value: 'none', label: 'Not recorded' },
            ]}
          />
          <AmountRange label="Amount" value={amount} onChange={setAmount} />
          <FilterToggle
            active={filters.needsReview}
            onToggle={() => setFilters((f) => ({ ...f, needsReview: !f.needsReview }))}
          >
            Needs a look
          </FilterToggle>
          <FilterToggle active={filters.noReceipt} onToggle={() => setFilters((f) => ({ ...f, noReceipt: !f.noReceipt }))}>
            No receipt
          </FilterToggle>
        </FilterBar>
        <ActiveFilters
          chips={chips}
          onClearAll={clearAll}
          summary={q.data ? `${count(rows.length)} of ${count(inWindow.length)} expenses` : undefined}
        />
      </div>
      <div className="flex min-h-0 flex-1">
        <PageBody flush>
          {q.isError ? (
            <ErrorBox error={q.error} what="expenses" />
          ) : !q.data || !meta.data ? (
            <Loading what="Loading expenses" />
          ) : (
            <div className="px-4 pb-8 sm:px-5 compact:px-6">
              <UndoBar undo={undo} onDone={clearUndo} className="pt-3" />
              <Figures
                items={[
                  {
                    label: 'Total',
                    value: gbp(totals.total),
                    strong: true,
                    sub: `${count(totals.n)} ${totals.n === 1 ? 'expense' : 'expenses'}`,
                  },
                  { label: 'Stock', value: gbp(totals.stock) },
                  { label: 'Running costs', value: gbp(totals.running) },
                  { label: 'Equipment', value: gbp(totals.capital) },
                  { label: 'Personal', value: gbp(totals.personal), sub: totals.personal > 0 ? 'not in the P&L' : undefined },
                ]}
              />
              {q.data.caveats.length > 0 && (
                <ul className="flex flex-col gap-0.5 pt-2 text-sm text-ink-2">
                  {q.data.caveats.map((c) => (
                    <li key={c}>{c}</li>
                  ))}
                </ul>
              )}
              <ExpensesTable
                rows={rows}
                multiMonth={multiMonth}
                total={totals.total}
                selected={editing?.id ?? null}
                onOpen={(id) => setDrawer({ kind: 'edit', id })}
              />
            </div>
          )}
        </PageBody>
        {drawer?.kind === 'add' && meta.data && (
          <ExpenseDrawer expense={null} categories={categories} period={range ? 'all' : period} onClose={() => setDrawer(null)} onUndo={setUndo} />
        )}
        {editing && meta.data && (
          <ExpenseDrawer
            key={editing.id}
            expense={editing}
            categories={categories}
            period={period}
            onClose={() => setDrawer(null)}
            onUndo={setUndo}
          />
        )}
      </div>
    </>
  )
}

/* ----------------------------------------------------------------- table --- */

function ExpensesTable({
  rows,
  multiMonth,
  total,
  selected,
  onOpen,
}: {
  rows: Expense[]
  multiMonth: boolean
  total: number
  selected: number | null
  onOpen: (id: number) => void
}) {
  const body: ReactNode[] = []
  let month = ''
  let bucket: Expense[] = []
  const flush = () => {
    if (!multiMonth || bucket.length === 0) return
    const s = bucket.reduce((a, e) => a + e.amount_pence, 0)
    body.push(
      <tr key={`sub-${month}`} className="border-b border-line-strong bg-canvas-2 text-base font-bold">
        <td colSpan={6} className="py-2 pl-2 pr-2">
          {mLabel(month)}{' '}
          <span className="font-normal text-ink-2">
            · {bucket.length} {bucket.length === 1 ? 'expense' : 'expenses'}
          </span>
        </td>
        <td className="fig px-2 text-right">{gbp(s)}</td>
      </tr>,
    )
  }
  for (const e of rows) {
    const m = e.date.slice(0, 7)
    if (m !== month) {
      // Newest first: the subtotal heads its month.
      month = m
      bucket = rows.filter((r) => r.date.slice(0, 7) === m)
      flush()
    }
    const [wd, day, mon] = fd(e.date).split(' ')
    body.push(
      <Tr
        key={e.id}
        onClick={() => onOpen(e.id)}
        selected={selected === e.id}
        flagged={e.needs_review}
        label={`Open ${e.description}, ${fd(e.date)}`}
      >
        <Td className="whitespace-nowrap pl-2!">
          <span className={cx('inline-block w-9', isWeekend(e.date) ? 'font-bold text-ink' : 'text-ink-2')}>{wd}</span>
          <span className="fig inline-block w-5 text-right">{day}</span> <span className="text-ink-2">{mon}</span>
        </Td>
        <Td className="max-w-0">
          <div className="truncate font-semibold" title={e.description}>
            {e.description}
          </div>
          {(e.notes || e.needs_review) && (
            <div className={cx('truncate text-sm', e.needs_review ? 'text-bad-ink' : 'text-ink-2')} title={e.notes ?? undefined}>
              {e.needs_review && !e.notes ? 'Needs a look' : e.notes}
            </div>
          )}
        </Td>
        <Td secondary title={e.category}>
          {e.category}
        </Td>
        <Td className="whitespace-nowrap text-sm text-ink-2">{e.method ? METHOD_LABEL[e.method] : '—'}</Td>
        <Td className={cx('whitespace-nowrap text-sm', e.kind === 'OPERATING' ? 'text-ink-2' : 'font-bold')}>
          {KIND_LABEL[e.kind]}
        </Td>
        <Td className="text-center text-sm">
          {e.has_receipt ? (
            <span aria-label="Receipt kept" className="text-ink">
              ✓
            </span>
          ) : (
            <span aria-label="No receipt" className="text-ink-3">
              —
            </span>
          )}
        </Td>
        <Td numeric strong>
          {gbp(e.amount_pence)}
        </Td>
      </Tr>,
    )
  }

  return (
    <div className="pt-3">
      <Table header="upper" stickyHeader minWidth={780} label="Expenses">
        <THead>
          <tr>
            <Th width={112} className="pl-2!">
              Date
            </Th>
            <Th>Payee</Th>
            <Th width="18%">Category</Th>
            <Th width={120}>Paid by</Th>
            <Th width={96}>Type</Th>
            <Th width={64} className="text-center!">
              Receipt
            </Th>
            <Th numeric width={100}>
              Amount
            </Th>
          </tr>
        </THead>
        <TBody>
          {body}
          {rows.length === 0 ? (
            <tr>
              <td colSpan={7} className="p-10 text-center text-md text-ink-2">
                No expenses match these filters.
              </td>
            </tr>
          ) : (
            <tr className="border-t-2 border-ink text-md font-extrabold [&>td]:py-2.5">
              <td colSpan={6} className="pl-2">
                {count(rows.length)} {rows.length === 1 ? 'expense' : 'expenses'}
              </td>
              <td className="fig px-2 text-right">{gbp(total)}</td>
            </tr>
          )}
        </TBody>
      </Table>
    </div>
  )
}

/* ---------------------------------------------------------------- drawer --- */

interface Draft {
  date: string
  category_id: number
  description: string
  amount: string
  method: ExpenseMethod | ''
  kind: ExpenseKind
  has_receipt: boolean
  notes: string
  needs_review: boolean
}

function draftOf(e: Expense | null, categories: ExpenseCategory[], period: string | null): Draft {
  if (e)
    return {
      date: e.date,
      category_id: e.category_id,
      description: e.description,
      amount: penceToPounds(e.amount_pence),
      method: e.method ?? '',
      kind: e.kind,
      has_receipt: e.has_receipt,
      notes: e.notes ?? '',
      needs_review: e.needs_review,
    }
  const today = londonToday()
  const food = categories.find((c) => c.name === 'Stock — food') ?? categories[0]
  return {
    date: !period || period === 'all' || today.startsWith(period) ? today : `${period}-01`,
    category_id: food?.id ?? 0,
    description: '',
    amount: '',
    method: 'CARD',
    kind: 'OPERATING',
    has_receipt: false,
    notes: '',
    needs_review: false,
  }
}

function ExpenseDrawer({
  expense,
  categories,
  period,
  onClose,
  onUndo,
}: {
  expense: Expense | null
  categories: ExpenseCategory[]
  period: string | null
  onClose: () => void
  onUndo: (u: { text: string; restore: (() => void) | null } | null) => void
}) {
  const [operator] = useOperator()
  const refresh = useInvalidateFinance()
  const [d, setD] = useState<Draft>(() => draftOf(expense, categories, period))
  // What the server said about the last write; validation lives on the fields.
  const [outcome, setOutcome] = useState<Outcome | null>(null)
  const [missing, setMissing] = useState<{ description?: string; amount?: string; date?: string }>({})
  const [busy, setBusy] = useState(false)
  const set = (p: Partial<Draft>) => setD((x) => ({ ...x, ...p }))
  const amountParsed = poundsToPence(d.amount)
  const amountBad = amountParsed.kind === 'bad' ? amountParsed.message : null

  async function save() {
    const problems: typeof missing = {}
    if (!d.description.trim()) problems.description = 'Enter who was paid or what for.'
    if (amountParsed.kind !== 'value' || amountParsed.value <= 0)
      problems.amount = amountParsed.kind === 'bad' ? amountParsed.message : 'Enter an amount over £0.'
    if (!d.date) problems.date = 'Pick a date.'
    setMissing(problems)
    if (Object.keys(problems).length > 0 || amountParsed.kind !== 'value') return
    setOutcome(null)
    const full: ExpenseIn = {
      date: d.date,
      category_id: d.category_id,
      description: d.description.trim(),
      amount_pence: amountParsed.value,
      method: d.method === '' ? null : d.method,
      kind: d.kind,
      has_receipt: d.has_receipt,
      notes: d.notes.trim() || null,
      needs_review: d.needs_review,
    }
    setBusy(true)
    let r
    if (expense === null) {
      r = await financeWrite.createExpense({ ...full, operator })
    } else {
      // Send only what changed: a mirrored field (drawings) refuses a no-op rewrite.
      const before = draftOf(expense, categories, period)
      const body: ExpenseIn = {}
      if (d.date !== before.date) body.date = full.date
      if (d.category_id !== before.category_id) body.category_id = full.category_id
      if (full.description !== expense.description) body.description = full.description
      if (full.amount_pence !== expense.amount_pence) body.amount_pence = full.amount_pence
      if (d.method !== before.method) body.method = full.method
      if (d.kind !== before.kind) body.kind = full.kind
      if (d.has_receipt !== before.has_receipt) body.has_receipt = full.has_receipt
      if ((full.notes ?? null) !== expense.notes) body.notes = full.notes
      if (d.needs_review !== before.needs_review) body.needs_review = full.needs_review
      if (Object.keys(body).length === 0) {
        setBusy(false)
        return onClose()
      }
      r = await financeWrite.patchExpense(expense.id, { ...body, operator })
    }
    setBusy(false)
    if (r.kind !== 'ok') return setOutcome({ kind: 'error', text: r.message })
    await refresh()
    onClose()
  }

  async function remove() {
    if (!expense) return
    setBusy(true)
    const r = await financeWrite.deleteExpense(expense.id, operator)
    setBusy(false)
    if (r.kind !== 'ok') return setOutcome({ kind: 'error', text: r.message })
    await refresh()
    onClose()
    const token = r.data.undo_token
    onUndo({
      text: `Deleted ${expense.description} · ${gbp(expense.amount_pence)}`,
      restore: () =>
        void financeWrite.restoreExpense(expense.id, token, operator).then(async (res) => {
          if (res.kind !== 'ok') {
            onUndo({ text: `Couldn't restore ${expense.description}: ${res.message}`, restore: null })
            return
          }
          onUndo(null)
          await refresh()
        }),
    })
  }

  return (
    <Drawer
      open
      onClose={onClose}
      title={expense ? expense.description : 'Add expense'}
      context={expense ? `${fd(expense.date)} · ${gbp(expense.amount_pence)}` : 'Money paid out'}
      footer={
        <>
          {expense && (
            <Button variant="ghost" onClick={() => void remove()} disabled={busy} className="text-bad-ink">
              Delete
            </Button>
          )}
          <Button variant="ghost" className="flex-1" onClick={onClose}>
            Cancel
          </Button>
          <Button variant="primary" className="flex-1" onClick={() => void save()} disabled={busy}>
            {busy ? 'Saving…' : expense ? 'Save' : 'Add expense'}
          </Button>
        </>
      }
    >
      {expense?.needs_review && (
        <div className="flex items-center gap-3 rounded-button bg-alert-wash px-3 py-2 text-base">
          <span className="flex-1">{d.needs_review ? "Flagged: needs a look." : "Will be marked as looked at when you save."}</span>
          <Button size="sm" variant="outline" onClick={() => set({ needs_review: !d.needs_review })}>
            {d.needs_review ? 'Looks right' : 'Keep flagged'}
          </Button>
        </div>
      )}
      <div className="grid grid-cols-2 gap-3">
        <Field label={<Required>Date</Required>} error={missing.date}>
          <Input type="date" required value={d.date} onChange={(e) => set({ date: e.target.value })} />
        </Field>
        <Field label={<Required>Amount</Required>} error={missing.amount ?? amountBad}>
          <MoneyInput required value={d.amount} placeholder="0.00" onChange={(e) => set({ amount: e.target.value })} />
        </Field>
      </div>
      <Field label={<Required>Payee or description</Required>} error={missing.description}>
        <Input required value={d.description} placeholder="Who was paid" onChange={(e) => set({ description: e.target.value })} />
      </Field>
      <Field label="Category">
        <Select value={String(d.category_id)} onChange={(e) => set({ category_id: Number(e.target.value) })}>
          {categories.map((c) => (
            <option key={c.id} value={String(c.id)}>
              {c.name}
            </option>
          ))}
        </Select>
      </Field>
      <div className="grid grid-cols-2 gap-3">
        <Field label="Paid by">
          <Select value={d.method} onChange={(e) => set({ method: e.target.value as ExpenseMethod | '' })}>
            <option value="">Not recorded</option>
            {METHODS.map((m) => (
              <option key={m} value={m}>
                {METHOD_LABEL[m]}
              </option>
            ))}
          </Select>
        </Field>
        <Field label="Type" hint={d.kind === 'DRAWINGS' ? "Also shown on the director's account" : undefined}>
          <Select value={d.kind} onChange={(e) => set({ kind: e.target.value as ExpenseKind })}>
            {KINDS.map((k) => (
              <option key={k} value={k}>
                {KIND_LABEL[k]}
              </option>
            ))}
          </Select>
        </Field>
      </div>
      <Checkbox checked={d.has_receipt} onChange={(v) => set({ has_receipt: v })} label="Receipt kept" />
      <Field label="Notes" hint="Optional">
        <Textarea rows={3} value={d.notes} onChange={(e) => set({ notes: e.target.value })} />
      </Field>
      {expense && expense.source !== 'MANUAL' && (
        <p className="text-sm text-ink-2">
          Imported from {expense.source === 'LEGACY_WORKBOOK' ? 'the finance workbook' : expense.source}
          {expense.source_ref ? ` · ${expense.source_ref}` : ''}
          {expense.updated_by ? ` · last edited by ${expense.updated_by}` : ''}
        </p>
      )}
      <StatusLine outcome={outcome} />
    </Drawer>
  )
}

/** A field label with the "required" cue said in words, not only by an asterisk. */
function Required({ children }: { children: ReactNode }) {
  return (
    <>
      {children} <span className="font-normal text-ink-2">(required)</span>
    </>
  )
}
