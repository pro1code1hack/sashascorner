/**
 * The template list. The sidebar idiom from the reference: the active row is a
 * filled rounded rect rather than a tinted stripe, the name sits over its
 * quieter category, and the counts that explain the whole model — "9 items, 10
 * components" — sit under both.
 */
import { plural } from '../../lib/format'
import type { TemplateSummary } from '../../lib/types'
import { Cell, Note, SectionLabel } from '../../components/ui'

export function TemplateList({
  templates,
  chosen,
  itemCount,
  onChoose,
}: {
  templates: TemplateSummary[]
  chosen: number | null
  /** Items driven by the template currently open, for the note beneath. */
  itemCount: number
  onChoose: (id: number) => void
}) {
  return (
    <aside className="min-w-0">
      <SectionLabel right={`${templates.length} ${plural(templates.length, 'template')}`}>
        Templates
      </SectionLabel>

      <div className="grid gap-1">
        {templates.map((t) => {
          const on = t.id === chosen
          return (
            <button
              key={t.id}
              type="button"
              aria-current={on ? 'true' : undefined}
              onClick={() => onChoose(t.id)}
              className={`w-full min-w-0 rounded-control px-2.5 py-2 text-left transition-colors ${
                on ? 'bg-raised' : 'hover:bg-raised/50'
              }`}
            >
              <Cell top={t.name} sub={t.category} />
              <div className="fig mt-1 truncate text-[0.6875rem] text-ink-5">
                {t.item_count} {plural(t.item_count, 'item')} · {t.component_count} components ·{' '}
                {t.sizes.join('/')}
              </div>
            </button>
          )
        })}
      </div>

      <div className="mt-4 max-w-[30ch]">
        <Note>
          One template drives {itemCount} sellable items. That is the whole model: the menu is a
          handful of templates crossed with an axis and a size ladder, not{' '}
          {itemCount > 0 ? 'three hundred' : 'many'} separate recipes. The rest of the proposals
          are still in the importer.
        </Note>
      </div>
    </aside>
  )
}
