/**
 * Recipes (recipes-menu-ingredients.md §V1): set a recipe once; every item
 * made from it follows.
 *
 * Rail: confirmed recipes by category, then the recipes detected in the
 * import but not confirmed (spec C-16), then the one-off items. Selection is in
 * the URL (`#/recipes?t=1`, `?p=<proposal id>`, `?one=1`) so a reload keeps it.
 * Leaving a recipe with unsaved changes asks first (spec C-15) — inline, not a
 * modal.
 */
import { useCallback, useState } from 'react'
import { useQueryClient } from '@tanstack/react-query'
import { Button, ErrorBox, Loading, PageHeader } from '../../components/ui'
import { href, navigate, useLocation } from '../../lib/router'
import { MENU_KEYS, useRecipeEditor, useRecipesRail } from '../../lib/menu-api'
import type { RecipesRail } from '../../lib/types/menu'
import { RailChips, RailColumn } from '../menu/common/Rail'
import type { RailItem } from '../menu/common/Rail'
import { RecipeEditorView } from './Editor'
import { ProposalView } from './ProposalView'

type Sel = { kind: 'template'; id: number } | { kind: 'proposal'; id: string } | { kind: 'one' } | { kind: 'none' }

function selection(q: URLSearchParams, rail: RecipesRail | undefined): Sel {
  const t = q.get('t')
  if (t && /^\d+$/.test(t)) return { kind: 'template', id: Number(t) }
  const p = q.get('p')
  if (p) return { kind: 'proposal', id: p }
  if (q.get('one')) return { kind: 'one' }
  const first = rail?.templates.find((x) => x.name === 'Flavoured Latte') ?? rail?.templates[0]
  if (first) return { kind: 'template', id: first.template_id }
  return { kind: 'none' }
}

export function RecipesScreen() {
  const loc = useLocation()
  const rail = useRecipesRail()
  const sel = selection(loc.query, rail.data)
  const editor = useRecipeEditor(sel.kind === 'template' ? sel.id : null)
  const qc = useQueryClient()
  const [dirty, setDirty] = useState(false)
  const [pending, setPending] = useState<Record<string, string> | null>(null)
  const onDirty = useCallback((d: boolean) => setDirty(d), [])

  const go = (query: Record<string, string>) => {
    if (dirty) {
      setPending(query)
      return
    }
    navigate('/recipes', { query })
  }

  const items: RailItem[] = []
  if (rail.data) {
    const cats: string[] = []
    for (const t of rail.data.templates) if (!cats.includes(t.category ?? '')) cats.push(t.category ?? '')
    for (const c of cats) {
      items.push({ kind: 'head', label: (c || 'Recipes').replace(' / flavoured latte', '') })
      for (const t of rail.data.templates.filter((x) => (x.category ?? '') === c)) {
        items.push({
          kind: 'row',
          key: `t${t.template_id}`,
          label: t.name,
          count: t.item_count,
          active: sel.kind === 'template' && sel.id === t.template_id,
          onSelect: () => go({ t: String(t.template_id) }),
        })
      }
    }
    if (rail.data.proposals.length) {
      items.push({ kind: 'head', label: 'Detected, not confirmed' })
      for (const p of rail.data.proposals) {
        items.push({
          kind: 'row',
          key: `p${p.proposal_id}`,
          label: p.name,
          count: p.menu_item_count,
          active: sel.kind === 'proposal' && sel.id === p.proposal_id,
          onSelect: () => go({ p: p.proposal_id }),
        })
      }
    }
    items.push({ kind: 'head', label: 'Not in a recipe' })
    items.push({
      kind: 'row',
      key: 'one',
      label: 'One-off items',
      count: rail.data.one_offs.length,
      active: sel.kind === 'one',
      onSelect: () => go({ one: '1' }),
    })
  }

  const firstProposal = rail.data?.proposals.find((p) => !p.blocked_reason) ?? rail.data?.proposals[0]
  return (
    <>
      <PageHeader
        title="Recipes"
        subtitle="set a recipe once; every item made from it follows"
        saved={dirty ? 'Unsaved changes' : rail.data ? 'Saved' : 'Loading…'}
        actions={
          <Button
            variant="primary"
            className="rounded-[18px] px-[18px] text-lg"
            disabled={!firstProposal}
            title="New recipes start from a pattern detected in the workbook"
            onClick={() => firstProposal && go({ p: firstProposal.proposal_id })}
          >
            + New recipe
          </Button>
        }
      />
      <RailChips items={items} label="Recipes" />
      {pending && (
        <div
          role="alert"
          className="flex flex-none flex-wrap items-center gap-3 border-b border-line bg-alert-wash px-5 py-2.5 text-base"
        >
          <span className="font-bold">You have unsaved recipe changes. Discard them?</span>
          <Button
            variant="danger"
            size="sm"
            onClick={() => {
              const q = pending
              setPending(null)
              setDirty(false)
              navigate('/recipes', { query: q })
            }}
          >
            Discard
          </Button>
          <Button variant="outline" size="sm" onClick={() => setPending(null)}>
            Keep editing
          </Button>
        </div>
      )}
      <div className="flex min-h-0 flex-1">
        <RailColumn items={items} label="Recipes" />
        {rail.error ? (
          <ErrorBox error={rail.error} what="recipes" />
        ) : sel.kind === 'template' ? (
          editor.isLoading ? (
            <Loading what="Loading the recipe" />
          ) : editor.error ? (
            <ErrorBox error={editor.error} what="this recipe" />
          ) : editor.data ? (
            <RecipeEditorView
              key={editor.data.template_id}
              editor={editor.data}
              onDirtyChange={onDirty}
              onReload={() => qc.invalidateQueries({ queryKey: MENU_KEYS.editor(sel.id) })}
            />
          ) : null
        ) : sel.kind === 'proposal' ? (
          <ProposalView
            proposalId={sel.id}
            onConfirmed={(id) => navigate('/recipes', { query: id ? { t: String(id) } : {} })}
          />
        ) : sel.kind === 'one' && rail.data ? (
          <div className="min-h-0 flex-1 overflow-y-auto bg-surface px-4 pb-8 pt-5 sm:px-[22px]">
            <h2 className="text-2xl font-extrabold tracking-[-.01em]">One-off recipes</h2>
            <p className="mb-3.5 mt-1 text-base text-ink-2">
              {rail.data.one_offs.length} items have their own hand-written recipe (cakes, bottled drinks, toasties, meal
              deals…). That is correct for them. Tap one to edit it in Menu items.
            </p>
            <div className="flex flex-wrap gap-1.5">
              {rail.data.one_offs.map((o) => (
                <a
                  key={o.menu_item_id}
                  href={href('/menu', { item: o.menu_item_id })}
                  className="rounded-button border border-line-strong px-3.5 py-1.5 text-base no-underline hover:bg-canvas"
                >
                  {o.name}
                </a>
              ))}
            </div>
          </div>
        ) : rail.isLoading ? (
          <Loading what="Loading recipes" />
        ) : null}
      </div>
    </>
  )
}
