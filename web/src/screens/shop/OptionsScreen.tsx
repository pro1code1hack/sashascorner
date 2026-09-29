/**
 * Online orders › Option groups (`#/shop/options`): Milk, Extras, Customise… the
 * choices a customer makes on an item page. A list with ↑ ↓ order and a drawer
 * editor: name, prompt, kind, layout, required / min / max, collapsed, the
 * categories it applies to, and the options table (description, price delta,
 * kcal, default, available, the linked ops modifier so stock depletes the right
 * milk, a photo for photo tiles). One PUT saves the whole group, options included.
 */
import { useMemo, useState } from 'react'
import {
  Button,
  Checkbox,
  ConfirmTwiceButton,
  Drawer,
  Empty,
  ErrorBox,
  Field,
  FilterChip,
  FilterChipRow,
  Input,
  Loading,
  MoneyInput,
  PageBody,
  PageHeader,
  Pill,
  Select,
  Toggle,
  cx,
} from '../../components/ui'
import { penceToPounds, poundsToPence } from '../../components/confirm/numbers'
import { gbp } from '../../lib/format'
import { SHOP_KEY, catalogueWrites, useShopCatalogue, useShopModifiers } from '../../lib/shop-api'
import type { CatalogueAdmin, GroupAdmin, GroupIn, ModifierRef, OptionAdmin, OptionIn, OptionKind, OptionLayout } from '../../lib/types/shop'
import { OutcomeLine, useWrite } from '../stock/writes'
import { MoveButtons, domId } from '../website/sitemenu-rows'
import { LIST_HEAD, ShopGate, ShopPhotoSlot, intOrNull, moved, plural } from './shared'

/** Docked: group · kind · rule · order; "Applies to" gets its own column from 1280, and sits under the name below that. */
const COLS = 'compact:grid-cols-[minmax(0,1fr)_130px_110px_80px] wide:grid-cols-[minmax(0,1fr)_140px_110px_minmax(0,1fr)_80px]'

const KIND_WORD: Record<OptionKind, string> = { SINGLE: 'Pick one', MULTI: 'Pick any' }
const LAYOUT_WORD: Record<OptionLayout, string> = { TILES: 'Tiles', PHOTO_TILES: 'Photo tiles', CHECKLIST: 'Checklist' }

export function OptionsScreen() {
  const cat = useShopCatalogue()
  const [open, setOpen] = useState<number | 'new' | null>(null)
  const order = useWrite()
  const groups = useMemo(() => [...(cat.data?.option_groups ?? [])].sort((a, b) => a.sort_order - b.sort_order), [cat.data])
  const current = open === null || open === 'new' ? null : (groups.find((g) => g.id === open) ?? null)
  const move = (from: number, to: number) => {
    if (to < 0 || to >= groups.length) return
    const g = groups[from]
    if (!g) return
    void order.run(() => catalogueWrites.groupsOrder(moved(groups, from, to).map((x) => x.id)), { invalidate: [SHOP_KEY] })
    window.requestAnimationFrame(() => document.getElementById(`${domId('shg', String(g.id))}-${to < from ? 'up' : 'down'}`)?.focus())
  }
  return (
    <>
      <PageHeader
        title="Option groups"
        subtitle="The choices on an item page: milk, extras, customise. Size is not a group; sizes come from Menu items."
        actions={
          <Button variant="primary" onClick={() => setOpen('new')}>
            New group
          </Button>
        }
      />
      <ShopGate>
        {cat.isPending && (
          <PageBody>
            <Loading what="Reading option groups" />
          </PageBody>
        )}
        {cat.isError && (
          <PageBody>
            <ErrorBox error={cat.error} what="option groups" />
          </PageBody>
        )}
        {cat.data && (
          <div className="flex min-h-0 flex-1">
            <PageBody className="compact:px-5">
              <div className="flex flex-col gap-3">
                <p className="text-sm text-ink-2">
                  A group shows on every product in the categories it applies to, plus any product it is attached to on its own (Shop menu › product). Groups appear in this order on the item page.
                </p>
                <OutcomeLine outcome={order.outcome} />
                {groups.length === 0 ? (
                  <Empty action={<Button onClick={() => setOpen('new')}>New group</Button>}>No option groups yet. Milk, Extras and Customise are the usual three.</Empty>
                ) : (
                  <div className="overflow-hidden rounded-card-lg bg-surface shadow-raised">
                    <div className={cx(LIST_HEAD, COLS)}>
                      <span>Group</span>
                      <span>Kind</span>
                      <span>Rule</span>
                      <span className="hidden wide:block">Applies to</span>
                      <span />
                    </div>
                    <ul>
                      {groups.map((g, i) => (
                        <GroupRow key={g.id} g={g} cat={cat.data} open={open === g.id} onOpen={() => setOpen(g.id)} mover={{ enabled: !order.pending, index: i, count: groups.length, move }} />
                      ))}
                    </ul>
                  </div>
                )}
              </div>
            </PageBody>
            <Drawer
              open={open !== null}
              onClose={() => setOpen(null)}
              title={open === 'new' ? 'New option group' : (current?.name ?? '')}
              context={current ? `${KIND_WORD[current.kind]} · ${LAYOUT_WORD[current.layout]} · ${plural(current.options.length, 'option')}` : undefined}
              width={520}
              compactWidth={420}
            >
              {open !== null && (
                <GroupEditor key={open === 'new' ? 'new' : `${current?.id}-${current?.updated_at ?? ''}`} g={current} cat={cat.data} onClose={() => setOpen(null)} />
              )}
            </Drawer>
          </div>
        )}
      </ShopGate>
    </>
  )
}

function ruleWord(g: GroupAdmin): string {
  if (g.kind === 'SINGLE') return g.required ? 'Required' : 'Optional'
  const parts: string[] = []
  if (g.required || g.min_select > 0) parts.push(`at least ${Math.max(1, g.min_select)}`)
  if (g.max_select !== null) parts.push(`up to ${g.max_select}`)
  return parts.length ? parts.join(', ') : 'Any number'
}

function GroupRow({ g, cat, open, onOpen, mover }: { g: GroupAdmin; cat: CatalogueAdmin; open: boolean; onOpen: () => void; mover: { enabled: boolean; index: number; count: number; move: (from: number, to: number) => void } }) {
  const names = g.applies_to_categories.map((slug) => cat.categories.find((c) => c.slug === slug)?.name ?? slug)
  const attached = g.product_ids.length
  return (
    <li className={cx('grid grid-cols-[minmax(0,1fr)_auto] items-center gap-x-3 gap-y-1 border-b border-line-row px-3.5 py-2.5 last:border-b-0', COLS, open && 'bg-brand-wash', !g.active && 'opacity-70')}>
      <span className="min-w-0">
        <button type="button" onClick={onOpen} className="block max-w-full truncate text-left text-md font-bold text-ink hover:underline">
          {g.name}
          {!g.active && <span className="font-normal text-ink-2"> · inactive</span>}
        </button>
        <span className="block truncate text-sm text-ink-2">
          {g.options.length === 0 ? 'No options' : g.options.map((o) => `${o.name}${o.price_delta_pence ? ` +${gbp(o.price_delta_pence)}` : ''}`).join(', ')}
        </span>
        <span className="block truncate text-sm text-ink-2 compact:hidden">
          {KIND_WORD[g.kind]} · {ruleWord(g)} · {names.length ? names.join(', ') : attached ? `${plural(attached, 'product')} only` : 'nothing yet'}
        </span>
        <span className="hidden truncate text-sm text-ink-2 compact:block wide:hidden">
          {names.length ? names.join(', ') : attached ? `${plural(attached, 'product')} only` : 'applies to nothing yet'}
          {names.length > 0 && attached > 0 ? ` + ${attached}` : ''}
        </span>
      </span>
      <span className="hidden text-base compact:block">
        {KIND_WORD[g.kind]}
        <span className="block text-sm text-ink-2">{LAYOUT_WORD[g.layout]}{g.collapsed ? ' · folded' : ''}</span>
      </span>
      <span className="hidden text-base compact:block">{ruleWord(g)}</span>
      <span className="hidden min-w-0 truncate text-base wide:block">
        {names.length ? names.join(', ') : <span className="text-ink-2">{attached ? `${plural(attached, 'product')} only` : 'nothing yet'}</span>}
        {names.length > 0 && attached > 0 && <span className="text-ink-2"> + {attached}</span>}
      </span>
      <span className="col-start-2 row-start-1 flex justify-end compact:col-auto compact:row-auto">
        <MoveButtons name={g.name} mover={mover} idBase={domId('shg', String(g.id))} />
      </span>
    </li>
  )
}

/* -------------------------------------------------------------- editor --- */

interface OptionDraft {
  key: string
  id?: number
  name: string
  description: string
  price: string
  kcal: string
  is_default: boolean
  available: boolean
  modifier_id: string
  photo_url: string | null
}

function draftOf(o: OptionAdmin): OptionDraft {
  return {
    key: `o${o.id}`,
    id: o.id,
    name: o.name,
    description: o.description ?? '',
    price: o.price_delta_pence === 0 ? '' : penceToPounds(o.price_delta_pence),
    kcal: o.kcal === null ? '' : String(o.kcal),
    is_default: o.is_default,
    available: o.available,
    modifier_id: o.modifier_id === null ? '' : String(o.modifier_id),
    photo_url: o.photo_url,
  }
}

let seq = 0
function blankOption(): OptionDraft {
  seq += 1
  return { key: `n${seq}`, name: '', description: '', price: '', kcal: '', is_default: false, available: true, modifier_id: '', photo_url: null }
}

function GroupEditor({ g, cat, onClose }: { g: GroupAdmin | null; cat: CatalogueAdmin; onClose: () => void }) {
  const w = useWrite()
  const mods = useShopModifiers()
  const [name, setName] = useState(g?.name ?? '')
  const [prompt, setPrompt] = useState(g?.prompt ?? '')
  const [kind, setKind] = useState<OptionKind>(g?.kind ?? 'SINGLE')
  const [layout, setLayout] = useState<OptionLayout>(g?.layout ?? 'TILES')
  const [required, setRequired] = useState(g?.required ?? false)
  const [minSel, setMinSel] = useState(String(g?.min_select ?? 0))
  const [maxSel, setMaxSel] = useState(g?.max_select === null || g === null ? '' : String(g.max_select))
  const [collapsed, setCollapsed] = useState(g?.collapsed ?? false)
  const [active, setActive] = useState(g?.active ?? true)
  const [cats, setCats] = useState<string[]>(g?.applies_to_categories ?? [])
  const [options, setOptions] = useState<OptionDraft[]>(() => (g ? [...g.options].sort((a, b) => a.sort_order - b.sort_order).map(draftOf) : [blankOption()]))
  const [error, setError] = useState<string | null>(null)

  const patch = (key: string, p: Partial<OptionDraft>) => setOptions((os) => os.map((o) => (o.key === key ? { ...o, ...p } : o)))
  const setDefault = (key: string, on: boolean) =>
    setOptions((os) => os.map((o) => (o.key === key ? { ...o, is_default: on } : kind === 'SINGLE' && on ? { ...o, is_default: false } : o)))

  const save = () => {
    if (name.trim() === '') {
      setError('The group needs a name.')
      return
    }
    const minV = intOrNull(minSel) ?? 0
    const maxV = intOrNull(maxSel)
    if (minV === undefined || maxV === undefined) {
      setError('Minimum and maximum are whole numbers.')
      return
    }
    const outs: OptionIn[] = []
    for (const [i, o] of options.entries()) {
      if (o.name.trim() === '') {
        setError(`Option ${i + 1} needs a name.`)
        return
      }
      const price = poundsToPence(o.price)
      if (price.kind === 'bad') {
        setError(`${o.name}: ${price.message}`)
        return
      }
      const kcal = intOrNull(o.kcal)
      if (kcal === undefined) {
        setError(`${o.name}: kcal is a whole number.`)
        return
      }
      outs.push({
        ...(o.id !== undefined ? { id: o.id } : {}),
        name: o.name.trim(),
        description: o.description.trim() || null,
        price_delta_pence: price.kind === 'value' ? price.value : 0,
        kcal,
        is_default: o.is_default,
        available: o.available,
        modifier_id: o.modifier_id === '' ? null : Number(o.modifier_id),
        sort_order: i,
      })
    }
    setError(null)
    const body: GroupIn = {
      name: name.trim(),
      prompt: prompt.trim() || null,
      kind,
      layout,
      required,
      min_select: minV,
      max_select: maxV,
      collapsed,
      applies_to_categories: cats,
      active,
      options: outs,
    }
    void w.run(() => (g ? catalogueWrites.groupUpdate(g.id, body) : catalogueWrites.groupCreate(body)), {
      invalidate: [SHOP_KEY],
      ok: () => (g ? 'Saved.' : 'Group created.'),
      after: () => {
        if (!g) onClose()
      },
    })
  }

  const remove = () => {
    if (!g) return
    void w.run(() => catalogueWrites.groupDelete(g.id), { invalidate: [SHOP_KEY], ok: () => 'Deleted.', after: onClose })
  }

  return (
    <>
      <div className="flex flex-wrap gap-x-4 gap-y-2">
        <Toggle checked={active} onChange={setActive} label={active ? 'Active' : 'Inactive (not shown)'} />
        <Toggle checked={collapsed} onChange={setCollapsed} label="Starts folded" />
      </div>
      <Field label="Name">
        <Input value={name} maxLength={80} onChange={(e) => setName(e.target.value)} placeholder="Milk" />
      </Field>
      <Field label="Prompt" hint="Under the name: “Choose your milk”.">
        <Input value={prompt} maxLength={160} onChange={(e) => setPrompt(e.target.value)} />
      </Field>
      <div className="grid grid-cols-2 gap-3">
        <Field label="Kind">
          <Select value={kind} onChange={(e) => setKind(e.target.value as OptionKind)}>
            <option value="SINGLE">Pick one</option>
            <option value="MULTI">Pick any</option>
          </Select>
        </Field>
        <Field label="Layout">
          <Select value={layout} onChange={(e) => setLayout(e.target.value as OptionLayout)}>
            <option value="TILES">Tiles (name, price, kcal)</option>
            <option value="PHOTO_TILES">Photo tiles</option>
            <option value="CHECKLIST">Checklist</option>
          </Select>
        </Field>
      </div>
      <div className="flex flex-wrap items-end gap-3">
        <Checkbox checked={required} onChange={setRequired} label="Required" className="h-10" />
        {kind === 'MULTI' && (
          <>
            <Field label="At least" className="w-24">
              <Input size="sm" numeric inputMode="numeric" value={minSel} onChange={(e) => setMinSel(e.target.value)} />
            </Field>
            <Field label="At most" hint="Blank: no limit." className="w-24">
              <Input size="sm" numeric inputMode="numeric" value={maxSel} onChange={(e) => setMaxSel(e.target.value)} />
            </Field>
          </>
        )}
      </div>

      <FilterChipRow label="Applies to categories">
        <span className="w-full text-xs font-bold text-ink-2">Applies to every product in</span>
        {cat.categories.length === 0 && <span className="text-sm text-ink-2">No categories yet.</span>}
        {cat.categories.map((c) => (
          <FilterChip key={c.slug} active={cats.includes(c.slug)} onClick={() => setCats((s) => (s.includes(c.slug) ? s.filter((x) => x !== c.slug) : [...s, c.slug]))}>
            {c.name}
          </FilterChip>
        ))}
      </FilterChipRow>

      <div className="flex flex-col gap-2">
        <div className="flex items-center justify-between">
          <span className="text-xs font-bold text-ink-2">Options</span>
          <Button variant="add" size="sm" onClick={() => setOptions((os) => [...os, blankOption()])}>
            + Add option
          </Button>
        </div>
        {options.map((o, i) => (
          <OptionEditor
            key={o.key}
            o={o}
            index={i}
            count={options.length}
            kind={kind}
            layout={layout}
            mods={mods.data ?? []}
            onChange={(p) => patch(o.key, p)}
            onDefault={(on) => setDefault(o.key, on)}
            onMove={(to) => setOptions((os) => moved(os, i, to))}
            onRemove={() => setOptions((os) => os.filter((x) => x.key !== o.key))}
          />
        ))}
      </div>

      <div className="mt-auto flex flex-col gap-2">
        {error && (
          <p role="alert" className="text-sm text-bad-ink">
            {error}
          </p>
        )}
        <OutcomeLine outcome={w.outcome} />
        <div className="flex flex-wrap gap-2">
          {g && (
            <ConfirmTwiceButton armedLabel="Tap again to delete" onConfirm={remove} pending={w.pending} pendingLabel="Deleting…">
              Delete group
            </ConfirmTwiceButton>
          )}
          <Button className="ml-auto" onClick={onClose}>
            Close
          </Button>
          <Button variant="primary" pending={w.pending} pendingLabel="Saving…" onClick={save}>
            {g ? 'Save' : 'Create group'}
          </Button>
        </div>
      </div>
    </>
  )
}

function OptionEditor({
  o,
  index,
  count,
  kind,
  layout,
  mods,
  onChange,
  onDefault,
  onMove,
  onRemove,
}: {
  o: OptionDraft
  index: number
  count: number
  kind: OptionKind
  layout: OptionLayout
  mods: ModifierRef[]
  onChange: (p: Partial<OptionDraft>) => void
  onDefault: (on: boolean) => void
  onMove: (to: number) => void
  onRemove: () => void
}) {
  const name = o.name || `Option ${index + 1}`
  return (
    <div className="flex flex-col gap-2 rounded-card border border-line-soft bg-canvas-2 px-3 py-2.5">
      <div className="flex items-start gap-2">
        <Field label={`Option ${index + 1}`} className="flex-1">
          <Input size="sm" value={o.name} maxLength={80} placeholder="Oat milk" onChange={(e) => onChange({ name: e.target.value })} />
        </Field>
        <MoveButtons name={name} mover={{ enabled: true, index, count, move: (_from, to) => onMove(to) }} idBase={domId('sho', o.key)} />
        <Button variant="ghost" size="sm" aria-label={`Remove ${name}`} onClick={onRemove} className="mt-[18px]">
          <span aria-hidden="true">×</span>
        </Button>
      </div>
      <div className="grid grid-cols-2 gap-2 sm:grid-cols-3">
        <Field label="Extra price">
          <MoneyInput size="sm" value={o.price} placeholder="0.00" onChange={(e) => onChange({ price: e.target.value })} />
        </Field>
        <Field label="Kcal">
          <Input size="sm" numeric inputMode="numeric" value={o.kcal} onChange={(e) => onChange({ kcal: e.target.value })} />
        </Field>
        <Field label="Depletes stock as" hint={mods.length === 0 ? 'No modifiers in Menu items.' : undefined} className="col-span-2 sm:col-span-1">
          <Select size="sm" value={o.modifier_id} onChange={(e) => onChange({ modifier_id: e.target.value })}>
            <option value="">Nothing extra</option>
            {mods.map((m) => (
              <option key={m.id} value={String(m.id)}>
                {m.name}
                {m.price_pence ? ` (${gbp(m.price_pence)})` : ''}
              </option>
            ))}
          </Select>
        </Field>
      </div>
      <Field label="Tagline" hint="Under a tile: “Double the caffeine!”">
        <Input size="sm" value={o.description} maxLength={120} onChange={(e) => onChange({ description: e.target.value })} />
      </Field>
      <div className="flex flex-wrap items-center gap-x-4 gap-y-1">
        <Checkbox checked={o.is_default} onChange={onDefault} label={kind === 'SINGLE' ? 'Picked by default' : 'Ticked by default'} />
        <Toggle checked={o.available} onChange={(on) => onChange({ available: on })} label={<span className="text-sm">{o.available ? 'Available' : 'Unavailable'}</span>} />
        {o.photo_url && <Pill tone="neutral">Has photo</Pill>}
      </div>
      {layout === 'PHOTO_TILES' && (
        <ShopPhotoSlot
          url={o.photo_url}
          name={name}
          aspect="wide"
          disabledReason={o.id === undefined ? 'Save the group first, then add this option’s photo.' : undefined}
          upload={(blob, by) => catalogueWrites.optionPhoto(o.id ?? 0, blob, by)}
          clear={() => catalogueWrites.optionPhotoClear(o.id ?? 0)}
        />
      )}
    </div>
  )
}
