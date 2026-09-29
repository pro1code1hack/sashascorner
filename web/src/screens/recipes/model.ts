/**
 * The Recipes editor's local draft, and the changeset it turns into.
 *
 * Nothing here is saved: the draft lives in the screen until the server has
 * previewed it and a person presses "Apply from today". Components map 1:1 to
 * backend rows; the per-size packaging row the design shows ("Different item
 * for each size") is a VIEW over several single-size components (`groupRows`),
 * and an edit in one cell goes to that cell's component.
 *
 * Quantities stay strings; money is typed in pounds and converted to integer
 * pence exactly (no parseFloat anywhere).
 */
import type {
  ComponentRole,
  EditorOption,
  RecipeEditor,
  SizeCode,
  TemplateOp,
} from '../../lib/types/menu'
import { penceToPounds, poundsToPence, qtyOut, sameQty } from '../menu/common/figures'

export const ROLES: ComponentRole[] = ['COFFEE', 'MILK', 'BASE', 'FLAVOUR', 'TOPPING', 'PACKAGING', 'SUNDRY']

/** The word on screen for each backend role enum. One map, used by every recipe view. */
export const ROLE_LABEL: Record<ComponentRole, string> = {
  COFFEE: 'Coffee',
  MILK: 'Milk',
  BASE: 'Base',
  FLAVOUR: 'Flavour',
  TOPPING: 'Topping',
  PACKAGING: 'Packaging',
  SUNDRY: 'Sundry',
}

export interface DComp {
  key: string
  id: number | null
  role: ComponentRole
  ingredient_id: number | null
  qty: Partial<Record<SizeCode, string>>
  subst: boolean
  required: boolean
}

export interface DOption {
  key: string
  id: number | null
  axis_id: number
  name: string
  ingredient_id: number | null
  /** null = inherit the slot's per-size quantity. */
  qty: Partial<Record<SizeCode, string>> | null
  extra: string
  season_id: number | null
  active: boolean
  removed: boolean
}

export interface Draft {
  name: string
  comps: DComp[]
  prep: Partial<Record<SizeCode, string>>
  prepEst: boolean
  base: Partial<Record<SizeCode, string>>
  options: DOption[]
}

export function draftFrom(e: RecipeEditor): Draft {
  const prep: Partial<Record<SizeCode, string>> = {}
  const base: Partial<Record<SizeCode, string>> = {}
  for (const s of e.sizes) {
    const p = e.prep_seconds_by_size[s]
    prep[s] = p === undefined ? '' : String(p)
    const b = e.base_price_by_size[s]
    base[s] = b === undefined || b === null ? '' : penceToPounds(b)
  }
  return {
    name: e.name,
    comps: e.components.map((c) => ({
      key: `c${c.component_id}`,
      id: c.component_id,
      role: c.role,
      ingredient_id: c.ingredient_id,
      qty: { ...c.qty_by_size },
      subst: c.is_substitutable,
      required: c.is_required,
    })),
    prep,
    prepEst: e.prep_is_estimate !== false,
    base,
    options: e.axes.flatMap((a) => a.options.map((o) => optionFrom(o))),
  }
}

function optionFrom(o: EditorOption): DOption {
  return {
    key: `o${o.option_id}`,
    id: o.option_id,
    axis_id: o.axis_id,
    name: o.name,
    ingredient_id: o.ingredient_id,
    qty: o.qty_by_size ? { ...o.qty_by_size } : null,
    extra: penceToPounds(o.price_delta_pence),
    season_id: o.season_id,
    active: o.active,
    removed: false,
  }
}

/** A draft row as the editor shows it: one component, or one per-size group. */
export interface Row {
  key: string
  role: ComponentRole
  /** size -> the component key holding that size (grouped rows), else null. */
  bySize: Partial<Record<SizeCode, string>> | null
  comps: DComp[]
}

/**
 * Same-role components that each hold ONE size are shown as one row per slot
 * (cup S / cup M / cup XL), filling the first row that lacks that size.
 */
export function groupRows(comps: DComp[], sizes: SizeCode[]): Row[] {
  const rows: Row[] = []
  for (const c of comps) {
    const keys = Object.keys(c.qty).filter((k) => (c.qty as Record<string, string>)[k] !== undefined) as SizeCode[]
    if (sizes.length > 1 && keys.length === 1 && c.id !== null) {
      const size = keys[0] as SizeCode
      const slot = rows.find((r) => r.role === c.role && r.bySize !== null && r.bySize[size] === undefined)
      if (slot && slot.bySize) {
        slot.bySize[size] = c.key
        slot.comps.push(c)
        continue
      }
      rows.push({ key: `g${c.key}`, role: c.role, bySize: { [size]: c.key }, comps: [c] })
      continue
    }
    rows.push({ key: c.key, role: c.role, bySize: null, comps: [c] })
  }
  return rows
}

export interface Built {
  ops: TemplateOp[]
  errors: string[]
}

function qtyChanges(
  sizes: SizeCode[],
  before: Partial<Record<SizeCode, string>>,
  after: Partial<Record<SizeCode, string>>,
  errors: string[],
  label: string,
): Partial<Record<SizeCode, string>> {
  const out: Partial<Record<SizeCode, string>> = {}
  for (const s of sizes) {
    const a = after[s]
    const b = before[s]
    if (a === undefined) continue
    const typed = a.trim() === '' ? '0' : a
    const v = qtyOut(typed)
    if (v === null) {
      errors.push(`${label} ${s}: "${a}" is not a quantity`)
      continue
    }
    if (b === undefined ? v !== '0' : !sameQty(b, v)) out[s] = v
  }
  return out
}

function penceMap(
  sizes: SizeCode[],
  m: Partial<Record<SizeCode, string>>,
  errors: string[],
  label: string,
): Partial<Record<SizeCode, number>> {
  const out: Partial<Record<SizeCode, number>> = {}
  for (const s of sizes) {
    const t = m[s] ?? ''
    if (t.trim() === '') continue
    const p = poundsToPence(t)
    if (p === null) errors.push(`${label} ${s}: "${t}" is not an amount of money`)
    else out[s] = p
  }
  return out
}

export function buildOps(saved: Draft, draft: Draft, sizes: SizeCode[]): Built {
  const ops: TemplateOp[] = []
  const errors: string[] = []

  if (draft.name.trim() !== saved.name && draft.name.trim() !== '') ops.push({ op: 'template.rename', name: draft.name.trim() })

  const savedComps = new Map(saved.comps.map((c) => [c.key, c]))
  const draftKeys = new Set(draft.comps.map((c) => c.key))
  for (const c of saved.comps) {
    if (!draftKeys.has(c.key) && c.id !== null) ops.push({ op: 'component.remove', component_id: c.id })
  }
  for (const c of draft.comps) {
    const was = savedComps.get(c.key)
    if (!was || c.id === null) {
      const qty: Partial<Record<SizeCode, string>> = {}
      for (const s of sizes) {
        const t = c.qty[s]
        if (t === undefined || t.trim() === '') continue
        const v = qtyOut(t)
        if (v === null) errors.push(`new ${c.role.toLowerCase()} ${s}: "${t}" is not a quantity`)
        else qty[s] = v
      }
      if (c.ingredient_id === null && c.role !== 'FLAVOUR') errors.push(`pick an ingredient for the new ${c.role.toLowerCase()} line`)
      ops.push({
        op: 'component.add',
        role: c.role,
        ingredient_id: c.ingredient_id,
        qty_by_size: qty,
        is_substitutable: c.subst,
        is_required: c.required,
      })
      continue
    }
    const q = qtyChanges(sizes, was.qty, c.qty, errors, c.role.toLowerCase())
    if (Object.keys(q).length) ops.push({ op: 'component.qty', component_id: c.id, qty_by_size: q })
    const set: Extract<TemplateOp, { op: 'component.set' }> = { op: 'component.set', component_id: c.id }
    let any = false
    if (c.role !== was.role) {
      set.role = c.role
      any = true
    }
    if (c.ingredient_id !== was.ingredient_id) {
      set.ingredient_id = c.ingredient_id
      any = true
    }
    if (c.subst !== was.subst) {
      set.is_substitutable = c.subst
      any = true
    }
    if (c.required !== was.required) {
      set.is_required = c.required
      any = true
    }
    if (any) ops.push(set)
  }

  // Prep time: the whole map when anything in it (or its timed flag) moved.
  const prepChanged =
    draft.prepEst !== saved.prepEst || sizes.some((s) => (draft.prep[s] ?? '').trim() !== (saved.prep[s] ?? '').trim())
  if (prepChanged) {
    const prep: Partial<Record<SizeCode, number>> = {}
    for (const s of sizes) {
      const t = (draft.prep[s] ?? '').trim()
      if (t === '') continue
      if (!/^\d+$/.test(t) || Number(t) <= 0) errors.push(`time to make ${s}: "${t}" is not a number of seconds`)
      else prep[s] = Number(t)
    }
    ops.push({ op: 'prep.set', prep_seconds_by_size: prep, is_estimate: draft.prepEst })
  }

  const baseNew = penceMap(sizes, draft.base, errors, 'base price')
  const baseOld = penceMap(sizes, saved.base, [], 'base price')
  const baseChanged: Partial<Record<SizeCode, number>> = {}
  for (const s of sizes) {
    const n = baseNew[s]
    if (n !== undefined && n !== baseOld[s]) baseChanged[s] = n
  }
  if (Object.keys(baseChanged).length) ops.push({ op: 'price.base', base_price_pence_by_size: baseChanged })

  const savedOpts = new Map(saved.options.map((o) => [o.key, o]))
  for (const o of draft.options) {
    const was = savedOpts.get(o.key)
    const extra = poundsToPence(o.extra.replace(/^-/, '')) ?? (o.extra.trim() === '' ? 0 : null)
    const extraSigned = extra === null ? null : o.extra.trim().startsWith('-') ? -extra : extra
    if (extraSigned === null) errors.push(`${o.name} extra: "${o.extra}" is not an amount of money`)
    const qty = o.qty === null ? null : cleanQty(o.qty, sizes, errors, o.name)
    if (!was || o.id === null) {
      if (o.removed) continue
      ops.push({
        op: 'option.add',
        axis_id: o.axis_id,
        name: o.name.trim(),
        ingredient_id: o.ingredient_id,
        qty_by_size: qty,
        price_delta_pence: extraSigned ?? 0,
        season_id: o.season_id,
      })
      continue
    }
    if (o.removed) {
      ops.push({ op: 'option.remove', option_id: o.id })
      continue
    }
    const set: Extract<TemplateOp, { op: 'option.set' }> = { op: 'option.set', option_id: o.id }
    let any = false
    if (o.name.trim() !== was.name) {
      set.name = o.name.trim()
      any = true
    }
    if (o.ingredient_id !== was.ingredient_id) {
      set.ingredient_id = o.ingredient_id
      any = true
    }
    if (!sameMap(was.qty, qty)) {
      set.qty_by_size = qty
      any = true
    }
    const wasExtra = poundsToPence(was.extra.replace(/^-/, '')) ?? 0
    const wasSigned = was.extra.trim().startsWith('-') ? -wasExtra : wasExtra
    if (extraSigned !== null && extraSigned !== wasSigned) {
      set.price_delta_pence = extraSigned
      any = true
    }
    if (o.season_id !== was.season_id) {
      set.season_id = o.season_id
      any = true
    }
    if (any) ops.push(set)
    if (o.active !== was.active) ops.push({ op: 'option.active', option_id: o.id, active: o.active })
  }
  return { ops, errors }
}

function cleanQty(
  m: Partial<Record<SizeCode, string>>,
  sizes: SizeCode[],
  errors: string[],
  label: string,
): Partial<Record<SizeCode, string>> | null {
  const out: Partial<Record<SizeCode, string>> = {}
  for (const s of sizes) {
    const t = m[s]
    if (t === undefined || t.trim() === '') continue
    const v = qtyOut(t)
    if (v === null) errors.push(`${label} ${s}: "${t}" is not a quantity`)
    else out[s] = v
  }
  return Object.keys(out).length ? out : null
}

function sameMap(
  a: Partial<Record<SizeCode, string>> | null,
  b: Partial<Record<SizeCode, string>> | null,
): boolean {
  if (a === null || b === null) return (a === null) === (b === null)
  const keys = new Set([...Object.keys(a), ...Object.keys(b)]) as Set<SizeCode>
  for (const k of keys) if (!sameQty(a[k] ?? null, b[k] ?? null)) return false
  return true
}

/** Was this cell changed from the saved recipe? (red cell, spec §V1.5) */
export function cellChanged(saved: Draft, c: DComp, size: SizeCode): boolean {
  if (c.id === null) return true
  const was = saved.comps.find((x) => x.key === c.key)
  if (!was) return true
  const a = c.qty[size]
  const b = was.qty[size]
  if (a === undefined && b === undefined) return false
  return !sameQty(a ?? '0', b ?? '0') || c.ingredient_id !== was.ingredient_id
}
