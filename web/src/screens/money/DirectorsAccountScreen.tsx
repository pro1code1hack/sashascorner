/**
 * Money → Director's account: money in from you, money out to you
 * (finance.md 1.6, DECISIONS 4).
 *
 * Capital injections are share capital: shown apart, and never part of what
 * the company owes you. The balance is loans in, less repayments and drawings.
 * When it is negative the label says "You owe the company" (the design kept
 * saying "Company owes you −£x").
 *
 * Drawings mirrored from an expense are read-only here apart from the note: the
 * money is recorded once, on the Expenses tab.
 */
import { useCallback, useState } from 'react'
import {
  Button,
  ErrorBox,
  Loading,
  PageBody,
  PageHeader,
  TBody,
  THead,
  Table,
  Td,
  Th,
  Tr,
  cx,
} from '../../components/ui'
import { poundsToPence } from '../../components/confirm/numbers'
import { href } from '../../lib/router'
import { useOperator } from '../../lib/operator'
import { financeWrite, useDirector, useInvalidateFinance } from '../../lib/finance-api'
import type { DirectorEntry, DirectorEntryIn, DirectorType } from '../../lib/types/finance'
import {
  Caveats,
  DateCell,
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
  useSaveStatus,
} from './shared'

const TYPES: { value: DirectorType; label: string; dir: 'in' | 'out' }[] = [
  { value: 'CAPITAL_INJECTION', label: 'Capital injection', dir: 'in' },
  { value: 'LOAN_TO_COMPANY', label: 'Loan to company', dir: 'in' },
  { value: 'DRAWINGS', label: 'Drawings', dir: 'out' },
  { value: 'REPAYMENT', label: 'Repayment', dir: 'out' },
]
const dirOf = (t: DirectorType) => TYPES.find((x) => x.value === t)?.dir ?? 'out'

interface Draft {
  date: string
  type: DirectorType
  description: string
  amount: string
  notes: string
}

export function DirectorsAccountScreen() {
  const q = useDirector()
  const save = useSaveStatus()
  const refresh = useInvalidateFinance()
  const [operator] = useOperator()
  const [draft, setDraft] = useState<Draft | null>(null)
  const [draftError, setDraftError] = useState<string | null>(null)
  const [undo, setUndo] = useState<{ text: string; restore: () => void } | null>(null)
  const clearUndo = useCallback(() => setUndo(null), [])

  const patch = (id: number, body: DirectorEntryIn) =>
    committed(save, () => financeWrite.patchDirector(id, { ...body, operator }), refresh)

  async function saveDraft() {
    if (!draft) return
    const amount = poundsToPence(draft.amount)
    if (!draft.description.trim()) return setDraftError('Say what the money was.')
    if (amount.kind !== 'value' || amount.value <= 0) {
      return setDraftError(amount.kind === 'bad' ? amount.message : 'Enter an amount over £0.')
    }
    setDraftError(null)
    const dir = dirOf(draft.type)
    const ok = await committed(
      save,
      () =>
        financeWrite.createDirector({
          date: draft.date,
          type: draft.type,
          description: draft.description.trim(),
          in_pence: dir === 'in' ? amount.value : 0,
          out_pence: dir === 'out' ? amount.value : 0,
          notes: draft.notes.trim() || null,
          operator,
        }),
      refresh,
    )
    if (ok) setDraft(null)
  }

  async function remove(e: DirectorEntry) {
    const ok = await committed(save, () => financeWrite.deleteDirector(e.id, operator), refresh)
    if (!ok) return
    setUndo({
      text: `Deleted ${e.description}`,
      restore: () =>
        void committed(
          save,
          () =>
            financeWrite.createDirector({
              date: e.date,
              type: e.type,
              description: e.description,
              in_pence: e.in_pence,
              out_pence: e.out_pence,
              notes: e.notes,
              operator,
            }),
          async () => {
            setUndo(null)
            await refresh()
          },
        ),
    })
  }

  const d = q.data
  const owed = d?.loan_balance_pence ?? 0
  return (
    <>
      <PageHeader
        title="Director's account"
        subtitle="money in from you, money out to you"
        saved={<SaveStatus state={save.state} />}
        actions={
          <Button
            variant="primary"
            disabled={!d}
            onClick={() => {
              setDraftError(null)
              setDraft({ date: londonToday(), type: 'DRAWINGS', description: '', amount: '', notes: '' })
            }}
          >
            + Add entry
          </Button>
        }
      />
      <PageBody flush>
        {q.isError ? (
          <ErrorBox error={q.error} what="the director's account" />
        ) : !d ? (
          <Loading what="Loading the director's account" />
        ) : (
          <>
            <div className="flex flex-wrap items-baseline gap-x-7 gap-y-3 px-4 pt-4 sm:px-6">
              <Stat label="Capital put in" value={gbp(d.capital_in_pence)} />
              <Stat label="Loans put in" value={gbp(d.loans_in_pence)} />
              <Stat label="Taken out" value={gbp(d.taken_out_pence)} />
              <Stat
                label={owed < 0 ? 'You owe the company' : 'Company owes you'}
                value={gbp(Math.abs(owed))}
                strong
              />
              <p className="max-w-[420px] text-base text-ink-2">
                Loans you made to the business, minus personal spending paid from the business account. Capital is
                share capital: it stays in the company and is not owed back.
              </p>
            </div>
            <div className="px-4 pb-6 pt-3.5 sm:px-6">
              <Caveats items={d.caveats} className="mb-2" />
              {undo && (
                <div className="mb-2 flex">
                  <UndoBar text={undo.text} onUndo={undo.restore} onDone={clearUndo} />
                </div>
              )}
              <Table header="sentence" minWidth={940} label="Director's account entries" className="table-fixed">
                <THead>
                  <tr>
                    <Th width={124}>Date</Th>
                    <Th width={140}>Type</Th>
                    <Th>Description</Th>
                    <Th numeric width={100}>
                      In £
                    </Th>
                    <Th numeric width={100}>
                      Out £
                    </Th>
                    <Th numeric width={100}>
                      Balance
                    </Th>
                    <Th className="w-[18%]">Notes</Th>
                    <Th width={32} className="relative">
                      <span className="sr-only">Delete</span>
                    </Th>
                  </tr>
                </THead>
                <TBody>
                  {draft && (
                    <DraftRows
                      draft={draft}
                      setDraft={setDraft}
                      error={draftError}
                      onSave={() => void saveDraft()}
                      onCancel={() => setDraft(null)}
                    />
                  )}
                  {d.entries.map((e) => {
                    const mirrored = e.expense_id !== null
                    const dir = dirOf(e.type)
                    return (
                      <Tr key={e.id}>
                        <Td>
                          <DateCell value={e.date} label={`Date of ${e.description}`} disabled={mirrored} onCommit={(v) => patch(e.id, { date: v })} />
                        </Td>
                        <Td>
                          <SelectCell
                            value={e.type}
                            label={`Type of ${e.description}`}
                            disabled={mirrored}
                            onChange={(v) => {
                              const t = v as DirectorType
                              const amount = e.in_pence || e.out_pence
                              void patch(e.id, {
                                type: t,
                                in_pence: dirOf(t) === 'in' ? amount : 0,
                                out_pence: dirOf(t) === 'out' ? amount : 0,
                              })
                            }}
                          >
                            {TYPES.map((t) => (
                              <option key={t.value} value={t.value}>
                                {t.label}
                              </option>
                            ))}
                          </SelectCell>
                        </Td>
                        <Td>
                          {mirrored ? (
                            <div className="min-w-0">
                              <div className="truncate" title={e.description}>
                                {e.description}
                              </div>
                              <a href={href('/money/expenses', { month: e.date.slice(0, 7) })} className="text-xs text-ink-2 underline underline-offset-2">
                                from expenses
                              </a>
                            </div>
                          ) : (
                            <TextCell value={e.description} required label="Description" onCommit={(v) => (v === null ? Promise.resolve(false) : patch(e.id, { description: v }))} />
                          )}
                        </Td>
                        <Td>
                          {dir === 'in' ? (
                            <MoneyCell pence={e.in_pence} allowBlank={false} label={`In, ${e.description}`} disabled={mirrored} onCommit={(v) => (v ? patch(e.id, { in_pence: v, out_pence: 0 }) : Promise.resolve(false))} />
                          ) : (
                            <span className="block text-right text-ink-3">—</span>
                          )}
                        </Td>
                        <Td>
                          {dir === 'out' ? (
                            <MoneyCell pence={e.out_pence} allowBlank={false} label={`Out, ${e.description}`} disabled={mirrored} title={mirrored ? 'Change it on the Expenses tab' : undefined} onCommit={(v) => (v ? patch(e.id, { out_pence: v, in_pence: 0 }) : Promise.resolve(false))} />
                          ) : (
                            <span className="block text-right text-ink-3">—</span>
                          )}
                        </Td>
                        <Td numeric className={cx(!e.counts_toward_owed && 'text-ink-2')} title={e.counts_toward_owed ? undefined : 'Capital: not owed back, so the balance does not move'}>
                          {gbp(e.balance_pence)}
                        </Td>
                        <Td>
                          <TextCell value={e.notes} label={`Notes on ${e.description}`} onCommit={(v) => patch(e.id, { notes: v })} />
                        </Td>
                        <Td remove>
                          {mirrored ? null : <RemoveButton label={`Delete ${e.description}, ${fd(e.date)}`} onClick={() => void remove(e)} />}
                        </Td>
                      </Tr>
                    )
                  })}
                  {d.entries.length === 0 && !draft && (
                    <tr>
                      <td colSpan={8} className="p-10 text-center text-md text-ink-2">
                        No entries yet. Money you put in or take out goes here.
                      </td>
                    </tr>
                  )}
                </TBody>
              </Table>
              <p className="mt-2 text-sm text-ink-2">
                The workbook's own balance (everything in, minus everything out, capital included) is{' '}
                {gbp(d.workbook_balance_pence)}. {d.mirrored_count} drawings are recorded once, as expenses.
              </p>
            </div>
          </>
        )}
      </PageBody>
    </>
  )
}

function Stat({ label, value, strong }: { label: string; value: string; strong?: boolean }) {
  return (
    <div>
      <div className="text-base text-ink-2">{label}</div>
      <div className={cx('fig text-2xl', strong ? 'font-bold' : 'font-normal')}>{value}</div>
    </div>
  )
}

const DRAFT_INPUT =
  'h-7 w-full min-w-0 rounded-control border border-line-strong bg-surface px-1.5 text-base outline-none focus-visible:border-brand focus-visible:ring-3 focus-visible:ring-brand-wash'

function DraftRows({
  draft,
  setDraft,
  error,
  onSave,
  onCancel,
}: {
  draft: Draft
  setDraft: (d: Draft) => void
  error: string | null
  onSave: () => void
  onCancel: () => void
}) {
  const set = (p: Partial<Draft>) => setDraft({ ...draft, ...p })
  const dir = dirOf(draft.type)
  const amount = (
    <input
      aria-label={dir === 'in' ? 'Amount in' : 'Amount out'}
      inputMode="decimal"
      placeholder="0.00"
      value={draft.amount}
      onChange={(e) => set({ amount: e.target.value })}
      onKeyDown={(e) => e.key === 'Enter' && onSave()}
      className={cx(DRAFT_INPUT, 'fig text-right')}
    />
  )
  return (
    <>
      <tr className="border-b border-line bg-brand-wash">
        <td className="py-[6px] pr-2">
          <input type="date" aria-label="Date" value={draft.date} onChange={(e) => set({ date: e.target.value })} className={cx(DRAFT_INPUT, 'text-sm')} />
        </td>
        <td className="px-2 py-[6px]">
          <select aria-label="Type" value={draft.type} onChange={(e) => set({ type: e.target.value as DirectorType })} className={cx(DRAFT_INPUT, 'text-sm')}>
            {TYPES.map((t) => (
              <option key={t.value} value={t.value}>
                {t.label}
              </option>
            ))}
          </select>
        </td>
        <td className="px-2 py-[6px]">
          <input aria-label="Description" autoFocus placeholder="What was it" value={draft.description} onChange={(e) => set({ description: e.target.value })} onKeyDown={(e) => e.key === 'Enter' && onSave()} className={DRAFT_INPUT} />
        </td>
        <td className="px-2 py-[6px]">{dir === 'in' ? amount : null}</td>
        <td className="px-2 py-[6px]">{dir === 'out' ? amount : null}</td>
        <td className="px-2 py-[6px]" />
        <td className="px-2 py-[6px]">
          <input aria-label="Notes" placeholder="—" value={draft.notes} onChange={(e) => set({ notes: e.target.value })} className={DRAFT_INPUT} />
        </td>
        <td className="py-[6px] pl-2">
          <RemoveButton label="Discard this entry" onClick={onCancel} />
        </td>
      </tr>
      <tr className="border-b border-line bg-brand-wash">
        <td colSpan={8} className="pb-2">
          <div className="flex flex-wrap items-center gap-2.5">
            <Button size="sm" variant="primary" onClick={onSave}>
              Add this entry
            </Button>
            <Button size="sm" variant="ghost" onClick={onCancel}>
              Cancel
            </Button>
            {error ? (
              <span role="alert" className="text-sm text-bad-ink">
                {error}
              </span>
            ) : (
              <span className="text-sm text-ink-2">
                Personal spending paid from the business account is better added on Expenses as “Personal”: it then
                appears here once.
              </span>
            )}
          </div>
        </td>
      </tr>
    </>
  )
}
