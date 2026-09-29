/**
 * Suppliers (stock-orders-suppliers.md §3): who we buy from, and on what terms.
 *
 * Filters sit on top (owner, 2026-09-26): search, type, how we order, terms
 * confirmed or a guess, delivery day. Under them, the matching suppliers as a
 * strip of tabs, each showing the facts you scan for (delivery days, product
 * count, whether the terms are a guess). The selected one opens below.
 *
 * The selected supplier is in the hash (`#/suppliers/3`), so a reload keeps it.
 * The header's saved label shows the last write's result, or the server's
 * refusal verbatim.
 */
import { useEffect, useMemo, useRef, useState } from 'react'
import type { ReactNode } from 'react'
import { useQuery } from '@tanstack/react-query'
import {
  ActiveFilters,
  Button,
  Checkbox,
  Empty,
  ErrorBox,
  Field,
  FilterBar,
  FilterSelect,
  Input,
  Loading,
  PageHeader,
  SearchInput,
  Select,
  StatusLine,
  Textarea,
  cx,
} from '../../components/ui'
import type { ActiveFilterChip, Outcome } from '../../components/ui'
import { useOperator } from '../../lib/operator'
import { navigate, useLocation } from '../../lib/router'
import { KEYS, stockApi, supplierWrites } from '../../lib/stock-api'
import type { OrderChannel, Supplier, SupplierCreateIn } from '../../lib/types/stock'
import { WEEKDAYS } from '../stock/fmt'
import { useWrite } from '../stock/writes'
import { SupplierPage } from './SupplierPage'
import { EMPTY_TERMS, TermsFields, daysText, isBlank, parseTerms } from './terms'
import type { TermsDraft } from './terms'
import { CHANNELS, KINDS, channelLabel, emailError, fromWrite } from './vocab'

/** What the header's status line says after a write: the kit's Outcome, or nothing. */
export type Saved = Outcome | null

type TermsFilter = 'all' | 'guess' | 'confirmed'

export function SuppliersScreen() {
  const loc = useLocation()
  const q = useQuery({
    queryKey: KEYS.suppliers,
    queryFn: stockApi.suppliers,
    staleTime: 60_000,
  })
  const [adding, setAdding] = useState(false)
  const [saved, setSaved] = useState<Saved>(null)
  const [search, setSearch] = useState('')
  const [kind, setKind] = useState('all')
  const [channel, setChannel] = useState('all')
  const [terms, setTerms] = useState<TermsFilter>('all')
  const [day, setDay] = useState('all')
  const list = useMemo(() => q.data ?? [], [q.data])

  const kinds = useMemo(() => {
    const seen = new Set(list.map((s) => s.kind).filter((k): k is string => !!k))
    return [...seen].sort()
  }, [list])

  const shown = useMemo(() => {
    const f = search.trim().toLowerCase()
    return list.filter((s) => {
      if (f && !s.name.toLowerCase().includes(f)) return false
      if (kind !== 'all' && (s.kind ?? '') !== (kind === 'none' ? '' : kind)) return false
      if (channel !== 'all' && s.order_channel !== channel) return false
      if (terms === 'guess' && !s.terms_are_placeholders) return false
      if (terms === 'confirmed' && s.terms_are_placeholders) return false
      // An empty delivery week means any day (walk-in retail).
      if (day !== 'all' && s.delivery_weekdays.length > 0 && !s.delivery_weekdays.includes(Number(day))) return false
      return true
    })
  }, [list, search, kind, channel, terms, day])

  const wanted = Number(loc.segments[1])
  const selected =
    Number.isInteger(wanted) && shown.some((s) => s.supplier_id === wanted) ? wanted : (shown[0]?.supplier_id ?? null)

  const chips: ActiveFilterChip[] = []
  if (search.trim())
    chips.push({
      key: 'q',
      label: `“${search.trim()}”`,
      onRemove: () => setSearch(''),
    })
  if (kind !== 'all')
    chips.push({
      key: 'kind',
      label: `Type: ${kind === 'none' ? 'not set' : kind}`,
      onRemove: () => setKind('all'),
    })
  if (channel !== 'all')
    chips.push({
      key: 'ch',
      label: `Order: ${channelLabel(channel)}`,
      onRemove: () => setChannel('all'),
    })
  if (terms !== 'all')
    chips.push({
      key: 'terms',
      label: terms === 'guess' ? 'Terms a guess' : 'Terms confirmed',
      onRemove: () => setTerms('all'),
    })
  if (day !== 'all')
    chips.push({
      key: 'day',
      label: `Delivers ${WEEKDAYS.find((d) => String(d.iso) === day)?.label ?? day}`,
      onRemove: () => setDay('all'),
    })
  const clearAll = () => {
    setSearch('')
    setKind('all')
    setChannel('all')
    setTerms('all')
    setDay('all')
  }
  const guesses = list.filter((s) => s.terms_are_placeholders).length

  return (
    <>
      <PageHeader
        title="Suppliers"
        subtitle="who we buy from, and on what terms"
        saved={<StatusLine outcome={saved} />}
        actions={
          <Button
            variant="primary"
            onClick={() => {
              setSaved(null)
              setAdding(true)
            }}
          >
            + Add supplier
          </Button>
        }
      />
      <div className="min-h-0 min-w-0 flex-1 overflow-y-auto">
        <div className="border-b border-line-soft px-4 py-3 sm:px-5">
          <FilterBar
            activeCount={chips.length}
            label="Filter suppliers"
            search={
              <SearchInput
                label="Search suppliers"
                placeholder="Search suppliers"
                value={search}
                onChange={(e) => setSearch(e.target.value)}
              />
            }
          >
            <FilterSelect
              label="Type"
              value={kind}
              onChange={setKind}
              options={[
                { value: 'all', label: 'All types' },
                ...kinds.map((k) => ({ value: k, label: k })),
                { value: 'none', label: 'Type not set' },
              ]}
            />
            <FilterSelect
              label="How we order"
              value={channel}
              onChange={setChannel}
              options={[
                { value: 'all', label: 'Any way of ordering' },
                ...CHANNELS.map((c) => ({ value: c.value, label: c.label })),
              ]}
            />
            <FilterSelect
              label="Terms"
              value={terms}
              onChange={(v) => setTerms(v as TermsFilter)}
              options={[
                { value: 'all', label: 'All terms' },
                { value: 'guess', label: `Terms a guess (${guesses})` },
                {
                  value: 'confirmed',
                  label: `Terms confirmed (${list.length - guesses})`,
                },
              ]}
            />
            <FilterSelect
              label="Delivery day"
              value={day}
              onChange={setDay}
              options={[
                { value: 'all', label: 'Any delivery day' },
                ...WEEKDAYS.map((d) => ({
                  value: String(d.iso),
                  label: `Delivers ${d.label}`,
                })),
              ]}
            />
          </FilterBar>
          <ActiveFilters
            className="mt-2"
            chips={chips}
            onClearAll={clearAll}
            summary={chips.length > 0 ? `${shown.length} of ${list.length}` : undefined}
          />
          {!q.isPending && !q.isError && shown.length > 0 && (
            <SupplierTabs
              suppliers={shown}
              selected={adding ? null : selected}
              onSelect={(id) => {
                setAdding(false)
                setSaved(null)
                navigate(`/suppliers/${id}`)
              }}
            />
          )}
        </div>
        <div className="px-4 py-4.5 sm:px-5.5">
          <div className="mx-auto max-w-[1100px]">
            {q.isPending ? (
              <Loading what="Reading suppliers" />
            ) : q.isError ? (
              <ErrorBox error={q.error} what="suppliers" />
            ) : adding ? (
              <AddSupplier
                existing={list}
                onDone={(id, text) => {
                  setAdding(false)
                  if (text) setSaved({ kind: 'ok', text })
                  if (id !== null) {
                    clearAll()
                    navigate(`/suppliers/${id}`)
                  }
                }}
              />
            ) : selected === null ? (
              <Empty
                roomy
                action={
                  chips.length > 0 ? (
                    <Button variant="secondary" onClick={clearAll}>
                      Clear filters
                    </Button>
                  ) : undefined
                }
              >
                {list.length === 0 ? 'No suppliers yet. Add the first one.' : 'No supplier matches these filters.'}
              </Empty>
            ) : (
              <SupplierPage key={selected} supplierId={selected} onSaved={setSaved} />
            )}
          </div>
        </div>
      </div>
    </>
  )
}

/* -------------------------------------------------------------------- tabs -- */

function SupplierTabs({
  suppliers,
  selected,
  onSelect,
}: {
  suppliers: readonly Supplier[]
  selected: number | null
  onSelect: (id: number) => void
}) {
  const navRef = useRef<HTMLElement>(null)
  // Bring the selected tab into view horizontally only; never move the page.
  useEffect(() => {
    const nav = navRef.current
    const el = nav?.querySelector<HTMLElement>('[data-sel]')
    if (!nav || !el) return
    const left = el.offsetLeft // nav is `relative`, so this is within it
    if (left < nav.scrollLeft || left + el.offsetWidth > nav.scrollLeft + nav.clientWidth) nav.scrollLeft = left - 16
  }, [selected])
  return (
    <nav ref={navRef} aria-label="Suppliers" className="relative -mx-4 mt-3 overflow-x-auto px-4 sm:-mx-5 sm:px-5">
      <ul className="flex gap-2 pb-1 sm:flex-wrap">
        {suppliers.map((s) => {
          const on = s.supplier_id === selected
          return (
            <li key={s.supplier_id} className="flex-none">
              <button
                type="button"
                aria-current={on ? 'page' : undefined}
                data-sel={on ? '' : undefined}
                onClick={() => onSelect(s.supplier_id)}
                className={cx(
                  'relative flex min-w-[132px] max-w-[220px] flex-col items-start gap-0.5 rounded-button border px-3 py-2 text-left transition-[background-color,border-color]',
                  on ? 'border-brand-line bg-brand-wash' : 'border-line bg-surface hover:bg-canvas',
                )}
              >
                <span
                  className={cx(
                    'flex w-full items-center gap-1.5 text-base',
                    on ? 'font-extrabold text-brand-ink' : 'font-bold text-ink',
                  )}
                >
                  <span className="truncate">{s.name}</span>
                  {s.terms_are_placeholders && (
                    <span className="flex-none text-xs font-bold text-warn-ink">
                      <span aria-hidden="true">●</span>
                      <span className="sr-only">(terms are a guess)</span>
                    </span>
                  )}
                </span>
                <span className="fig truncate text-xs text-ink-2">
                  {s.delivery_weekdays.length === 0 ? 'any day' : daysText(s.delivery_weekdays)}
                  {s.product_count != null && ` · ${s.product_count} ${s.product_count === 1 ? 'item' : 'items'}`}
                </span>
              </button>
            </li>
          )
        })}
      </ul>
    </nav>
  )
}

/* --------------------------------------------------------------------- add -- */

interface Draft {
  name: string
  kind: string
  channel: OrderChannel
  contact: string
  url: string
  email: string
  phone: string
  notes: string
}

const EMPTY: Draft = {
  name: '',
  kind: '',
  channel: 'MANUAL',
  contact: '',
  url: '',
  email: '',
  phone: '',
  notes: '',
}

function FormSection({ n, title, hint, children }: { n: number; title: string; hint?: string; children: ReactNode }) {
  return (
    <section className="rounded-card border border-line bg-surface px-4 py-4 sm:px-5">
      <h3 className="text-md font-extrabold">
        <span className="fig mr-2 text-ink-2">{n}</span>
        {title}
      </h3>
      {hint && <p className="mt-0.5 text-sm text-ink-2">{hint}</p>}
      <div className="mt-3.5">{children}</div>
    </section>
  )
}

function AddSupplier({
  existing,
  onDone,
}: {
  existing: readonly Supplier[]
  onDone: (id: number | null, text?: string) => void
}) {
  const [operator] = useOperator()
  const [d, setD] = useState<Draft>(EMPTY)
  const [t, setT] = useState<TermsDraft>(EMPTY_TERMS)
  const [confirmed, setConfirmed] = useState(false)
  const [tried, setTried] = useState(false)
  const w = useWrite()

  const name = d.name.trim()
  const nameErr =
    name === ''
      ? 'A supplier needs a name.'
      : existing.some((s) => s.name.trim().toLowerCase() === name.toLowerCase())
        ? `There is already a supplier called ${name}.`
        : undefined
  const emailErr = emailError(d.email)
  const termsBlank = isBlank(t)
  const parsed = parseTerms(t, confirmed)
  const termsErrors = termsBlank && !confirmed ? {} : parsed.errors
  const confirmErr = confirmed && termsBlank ? 'Fill the terms in before confirming them.' : undefined
  const ok = !nameErr && !emailErr && !confirmErr && Object.keys(termsErrors).length === 0

  const submit = async () => {
    setTried(true)
    if (!ok) return
    const nul = (v: string) => (v.trim() === '' ? null : v.trim())
    const body: SupplierCreateIn = {
      name,
      created_by: operator,
      order_channel: d.channel,
      kind: nul(d.kind),
      contact: nul(d.contact),
      order_url: nul(d.url),
      email: nul(d.email),
      phone: nul(d.phone),
      notes: nul(d.notes),
    }
    if (!termsBlank && parsed.value) Object.assign(body, parsed.value, { terms_confirmed: confirmed })
    await w.run(() => supplierWrites.create(body), {
      invalidate: [KEYS.suppliers, KEYS.draft],
      after: (r) =>
        onDone(
          r.supplier.supplier_id,
          `${r.supplier.name} added`,
        ),
    })
  }

  return (
    <form
      noValidate
      className="flex max-w-[760px] flex-col gap-4"
      onSubmit={(e) => {
        e.preventDefault()
        void submit()
      }}
    >
      <div>
        <h2 className="text-2xl font-extrabold">Add a supplier</h2>
        <p className="mt-1 text-base text-ink-2">
          Only the name is required. Anything you do not know yet can be added later.
        </p>
      </div>

      <FormSection n={1} title="Who they are">
        <div className="grid grid-cols-1 gap-3 sm:grid-cols-3">
          <Field label="Name *" error={tried ? nameErr : undefined} className="sm:col-span-3">
            <Input
              autoFocus
              value={d.name}
              onChange={(e) => setD({ ...d, name: e.target.value })}
              placeholder="e.g. Highland Dairies"
              maxLength={160}
            />
          </Field>
          <Field label="Type">
            <Select value={d.kind} onChange={(e) => setD({ ...d, kind: e.target.value })}>
              <option value="">—</option>
              {KINDS.map((k) => (
                <option key={k} value={k}>
                  {k}
                </option>
              ))}
            </Select>
          </Field>
          <Field label="How we order" className="sm:col-span-2">
            <Select value={d.channel} onChange={(e) => setD({ ...d, channel: e.target.value as OrderChannel })}>
              {CHANNELS.map((c) => (
                <option key={c.value} value={c.value}>
                  {c.label}
                </option>
              ))}
            </Select>
          </Field>
        </div>
      </FormSection>

      <FormSection n={2} title="How to reach them">
        <div className="grid grid-cols-1 gap-3 sm:grid-cols-2">
          <Field label="Contact person">
            <Input
              value={d.contact}
              onChange={(e) => setD({ ...d, contact: e.target.value })}
              placeholder="Name, rep, account no."
            />
          </Field>
          <Field label="Website / portal">
            <Input value={d.url} onChange={(e) => setD({ ...d, url: e.target.value })} placeholder="https://" />
          </Field>
          <Field label="Email" error={emailErr}>
            <Input
              type="email"
              value={d.email}
              onChange={(e) => setD({ ...d, email: e.target.value })}
              placeholder="orders@…"
            />
          </Field>
          <Field label="Phone">
            <Input
              type="tel"
              value={d.phone}
              onChange={(e) => setD({ ...d, phone: e.target.value })}
              placeholder="01382 …"
            />
          </Field>
        </div>
      </FormSection>

      <FormSection
        n={3}
        title="Terms"
        hint="Orders are sized from these. Leave them empty if you don't know them yet: the supplier is then flagged until they are confirmed."
      >
        <TermsFields t={t} setT={setT} errors={tried ? termsErrors : {}} />
        <div className="mt-4 flex flex-col gap-1 rounded-button bg-canvas px-3 py-2.5">
          <Checkbox
            checked={confirmed}
            onChange={setConfirmed}
            alert={!termsBlank}
            label="I checked these terms with the supplier"
          />
          <span className="text-sm text-ink-2">
            {confirmed
              ? 'They will be saved as confirmed.'
              : termsBlank
                ? 'No terms yet: the supplier starts flagged “terms are a guess”.'
                : 'Unticked, they are saved as a guess and every order built on them says so.'}
          </span>
          {tried && confirmErr && <span className="text-sm text-bad-ink">{confirmErr}</span>}
        </div>
      </FormSection>

      <FormSection n={4} title="Notes">
        <Textarea
          rows={3}
          aria-label="Notes"
          value={d.notes}
          onChange={(e) => setD({ ...d, notes: e.target.value })}
          placeholder="Account number, rep, anything"
        />
      </FormSection>

      <div className="flex flex-wrap items-center gap-2">
        <Button type="submit" variant="primary" pending={w.pending} pendingLabel="Adding…">
          Add supplier
        </Button>
        <Button type="button" variant="ghost" onClick={() => onDone(null)}>
          Cancel
        </Button>
      </div>
      <StatusLine outcome={tried && !ok ? { kind: 'error', text: 'Fix the fields marked above.' } : fromWrite(w.outcome)} />
    </form>
  )
}
