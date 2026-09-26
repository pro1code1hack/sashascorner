/**
 * A recipe detected in the imported workbook but not confirmed (spec C-16).
 *
 * Read-only: what the pattern holds, where the imported rows disagree, and a
 * preview of what confirming would do (items moved onto the recipe, one-off
 * lines closed, cost changes) — computed by really confirming inside a
 * transaction that is always rolled back, so it is the honest answer.
 * Confirming posts the proposal ID, never the name: two detected recipes can
 * share a name and they are different recipes (ARCHITECTURE §8Q).
 */
import { useQuery } from '@tanstack/react-query'
import { useState } from 'react'
import { Button, Checkbox, EstNote, ErrorBox, Loading, Table, TBody, Td, Th, THead, Tr } from '../../components/ui'
import { OperatorNeeded } from '../../components/shell/Operator'
import { api } from '../../lib/api'
import { recipeApi, useInvalidateMenu } from '../../lib/menu-api'
import { useOperator } from '../../lib/operator'
import type { ProposalPreview } from '../../lib/types/menu'
import { costText, pctText, qtyText, sizeLabel } from '../menu/common/figures'
import { usePreview } from '../menu/common/usePreview'

export function ProposalView({ proposalId, onConfirmed }: { proposalId: string; onConfirmed: (templateId: number | null) => void }) {
  const all = useQuery({ queryKey: ['menu', 'proposals'], queryFn: api.proposals })
  const [allow, setAllow] = useState(false)
  const [operator] = useOperator()
  const [busy, setBusy] = useState(false)
  const [message, setMessage] = useState<string | null>(null)
  const invalidate = useInvalidateMenu()
  const key = JSON.stringify({ proposalId, allow })
  const pv = usePreview<ProposalPreview>(key, () => recipeApi.proposalPreview(proposalId, allow), 0)

  if (all.isLoading) return <Loading what="Loading the detected recipe" />
  if (all.error) return <ErrorBox error={all.error} what="the detected recipes" />
  const p = all.data?.proposals.find((x) => x.proposal_id === proposalId)
  if (!p) return <p className="p-6 text-base text-ink-2">That detected recipe is no longer waiting (it may have been confirmed).</p>

  const preview = pv.key === key ? pv.data : null
  const blocked = preview?.blocked_reason ?? (p.is_hollow ? p.blocked_reason : null)
  return (
    <div className="min-h-0 flex-1 overflow-y-auto px-4 pb-8 pt-5 sm:px-[22px]">
      <h2 className="text-3xl font-extrabold tracking-[-.01em]">{p.name}</h2>
      <p className="mb-4 mt-1 text-base text-ink-2">
        Detected in the imported workbook, not confirmed. {p.menu_item_count} menu items across {p.base_item_names.length} base
        items, sizes {p.sizes.map((s) => sizeLabel(s as never)).join(', ') || '—'}.
        {p.name_is_ambiguous && ' Another detected recipe has the same name; they are different recipes.'}
      </p>

      <h3 className="mb-1.5 text-lg font-extrabold">What goes in, per size</h3>
      {p.components.length === 0 ? (
        <p className="mb-4 text-base text-ink-2">Nothing: the imported rows carry no ingredient lines.</p>
      ) : (
        <div className="mb-5">
          <Table label="Components" minWidth={420}>
            <THead>
              <tr>
                <Th>Role</Th>
                <Th>Ingredient</Th>
                {p.sizes.map((s) => (
                  <Th key={s} numeric>
                    {sizeLabel(s as never)}
                  </Th>
                ))}
              </tr>
            </THead>
            <TBody>
              {p.components.map((c, i) => (
                <Tr key={i}>
                  <Td secondary>{c.role}</Td>
                  <Td>{c.ingredient_name ?? 'filled by a flavour'}</Td>
                  {p.sizes.map((s) => (
                    <Td key={s} numeric>
                      {qtyText(c.qty_by_size[s]) || '—'}
                    </Td>
                  ))}
                </Tr>
              ))}
            </TBody>
          </Table>
        </div>
      )}
      {p.axes.map((a) => (
        <div key={a.name} className="mb-4">
          <h3 className="mb-1 text-lg font-extrabold">
            {a.name} · {a.option_count}
          </h3>
          <p className="text-base text-ink-2">{Object.keys(a.options).sort().join(', ')}</p>
        </div>
      ))}
      {p.conflicts.length > 0 && (
        <div className="mb-4">
          <h3 className="mb-1 text-lg font-extrabold">Where the imported rows disagree</h3>
          <ul className="list-disc pl-5 text-base">
            {p.conflicts.map((c, i) => (
              <li key={i}>{c.describe}</li>
            ))}
          </ul>
          <Checkbox
            className="mt-2"
            checked={allow}
            onChange={setAllow}
            label="Accept the lowest quantity at each size (arbitrary; check it against the real recipe)"
          />
        </div>
      )}

      <section className="max-w-[720px] rounded-card border border-line bg-surface p-3.5" aria-live="polite">
        <h3 className="text-lg font-extrabold">What confirming does</h3>
        {pv.status.kind === 'loading' && <p className="text-base text-ink-2">Working it out…</p>}
        {'message' in pv.status && <p className="text-sm text-bad-ink">{pv.status.message}</p>}
        {blocked && <p className="mt-1 text-sm font-bold text-bad-ink">{blocked}</p>}
        {preview && !preview.blocked_reason && (
          <>
            <dl className="my-2 grid grid-cols-[minmax(0,1fr)_auto] gap-x-2.5 gap-y-1 text-base">
              <dt className="text-ink-2">Menu items moved onto the recipe</dt>
              <dd className="fig text-right font-bold">{preview.would_repoint}</dd>
              <dt className="text-ink-2">One-off recipe lines closed (kept in history)</dt>
              <dd className="fig text-right font-bold">{preview.would_close_manual_lines}</dd>
              <dt className="text-ink-2">Items whose cost changes</dt>
              <dd className="fig text-right font-bold">{preview.cost_changes.length}</dd>
            </dl>
            {preview.cost_changes.length > 0 && (
              <Table label="Cost changes" minWidth={420}>
                <THead>
                  <tr>
                    <Th>Item</Th>
                    <Th numeric>Cost now</Th>
                    <Th numeric>After</Th>
                    <Th numeric>Margin</Th>
                  </tr>
                </THead>
                <TBody>
                  {preview.cost_changes.slice(0, 20).map((c) => (
                    <Tr key={c.menu_item_id}>
                      <Td>
                        {c.name} {c.size_code ? sizeLabel(c.size_code) : ''}
                      </Td>
                      <Td numeric est={c.cost_before.is_estimate}>
                        {costText(c.cost_before)}
                      </Td>
                      <Td numeric est={c.cost_after.is_estimate}>
                        {costText(c.cost_after)}
                      </Td>
                      <Td numeric alert={c.margin_pct_after !== null && c.margin_pct_after < 60}>
                        {pctText(c.margin_pct_before)} → {pctText(c.margin_pct_after)}
                      </Td>
                    </Tr>
                  ))}
                </TBody>
              </Table>
            )}
            {preview.warnings.map((w) => (
              <p key={w} className="mt-1 text-sm text-bad-ink">
                {w}
              </p>
            ))}
            <div className="mt-2">
              <EstNote />
            </div>
          </>
        )}
        <p className="mb-3 mt-2 text-sm text-ink-2">
          Confirming applies from today. Past sales keep the one-off recipes they were sold with.
        </p>
        {message && (
          <p role="alert" className="mb-2 text-sm text-bad-ink">
            {message}
          </p>
        )}
        <Button
          variant="primary"
          size="sm"
          disabled={!preview || Boolean(preview.blocked_reason) || operator === null}
          pending={busy}
          pendingLabel="Confirming…"
          onClick={async () => {
            if (!operator) return
            setBusy(true)
            const r = await recipeApi.proposalConfirm(p.proposal_id, operator, allow)
            setBusy(false)
            if (r.kind === 'ok') {
              await invalidate()
              onConfirmed(r.data.template_id)
            } else setMessage(r.message)
          }}
        >
          Confirm this recipe
        </Button>
        <div className="mt-2">
          <OperatorNeeded what="confirm a recipe" />
        </div>
      </section>
    </div>
  )
}
