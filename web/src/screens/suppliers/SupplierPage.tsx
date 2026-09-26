/**
 * One supplier (§3.1): details, terms, what we buy here.
 *
 * - Details (type, how we order, contact, website, email, phone, notes) read as
 *   a list; "Edit details" opens a form that PATCHes the changed fields at once
 *   (C20). The name saves on blur.
 * - Terms read as a list too. "Check & confirm terms" / "Edit terms" opens the
 *   grouped form, and the only way to save it is "Confirm these terms", which
 *   posts all six to /confirm (C3, §10.9b). Saving terms IS confirming them, so
 *   the form asks for "I checked these with …" before it will send.
 * - "Delete" archives, with a second tap, and is refused while an order is open (C12).
 */
import { useEffect, useMemo, useState } from 'react'
import type { ReactNode } from 'react'
import { useQuery } from '@tanstack/react-query'
import {
  Button,
  Checkbox,
  ConfirmTwiceButton,
  ErrorBox,
  Field,
  Input,
  Loading,
  Pill,
  Select,
  Textarea,
  TitleInput,
  WarnBox,
  cx,
} from '../../components/ui'
import { confirmSupplierTerms } from '../../lib/api'
import { useOperator } from '../../lib/operator'
import { navigate } from '../../lib/router'
import { KEYS, stockApi, supplierWrites } from '../../lib/stock-api'
import type { OrderChannel, Supplier, SupplierPatchIn, SupplierProductsResponse } from '../../lib/types/stock'
import { useWrite } from '../stock/writes'
import { Products } from './Products'
import type { Saved } from './SuppliersScreen'
import { TermsFields, draftOf, parseTerms, termsRows } from './terms'
import type { TermsDraft } from './terms'
import { CHANNELS, KINDS, channelLabel, emailError } from './vocab'

const AFTER_PROFILE = [KEYS.suppliers, KEYS.draft, KEYS.orders]

type PatchBody = Omit<SupplierPatchIn, 'changed_by'>

export function SupplierPage({ supplierId, onSaved }: { supplierId: number; onSaved: (s: Saved) => void }) {
  const q = useQuery({
    queryKey: KEYS.supplierProducts(supplierId),
    queryFn: () => stockApi.supplierProducts(supplierId),
    staleTime: 30_000,
  })
  if (q.isPending) return <Loading what="Reading supplier" />
  if (q.isError) return <ErrorBox error={q.error} what="this supplier" />
  return <Page data={q.data} onSaved={onSaved} />
}

function Section({ title, right, children }: { title: ReactNode; right?: ReactNode; children: ReactNode }) {
  return (
    <section className="border-t border-line pt-4">
      <div className="mb-3 flex flex-wrap items-center gap-2">
        <h2 className="text-lg font-extrabold">{title}</h2>
        <span className="flex-1" />
        {right}
      </div>
      {children}
    </section>
  )
}

function ReadList({ rows }: { rows: ReadonlyArray<{ key: string; label: string; value: ReactNode }> }) {
  return (
    <dl className="grid grid-cols-1 gap-x-8 sm:grid-cols-2">
      {rows.map((r) => (
        <div key={r.key} className="flex min-w-0 items-baseline gap-3 border-b border-line-row py-2 text-base">
          <dt className="w-[128px] flex-none text-ink-2">{r.label}</dt>
          <dd className="fig min-w-0 flex-1 break-words">{r.value}</dd>
        </div>
      ))}
    </dl>
  )
}

function Page({ data, onSaved }: { data: SupplierProductsResponse; onSaved: (s: Saved) => void }) {
  const [operator] = useOperator()
  const s = data.supplier
  const w = useWrite()
  const [name, setName] = useState(s.name)
  useEffect(() => setName(s.name), [s.name])
  useEffect(() => {
    if (w.outcome?.tone === 'bad') onSaved({ tone: 'bad', text: w.outcome.text })
  }, [w.outcome, onSaved])

  const patch = async (body: PatchBody): Promise<boolean> => {
    const r = await w.run(() => supplierWrites.patch(s.supplier_id, { changed_by: operator, ...body }), {
      invalidate: [...AFTER_PROFILE, KEYS.supplierProducts(s.supplier_id)],
    })
    if (r)
      onSaved({
        tone: 'ok',
        text: r.changed.length ? 'Saved' : 'Nothing changed',
      })
    return r !== null
  }

  const archive = async () => {
    const r = await w.run(() => supplierWrites.archive(s.supplier_id, operator), { invalidate: AFTER_PROFILE })
    if (r) {
      onSaved({
        tone: 'ok',
        text: `${r.supplier.name} archived.${r.restarred.length ? ` Recipe prices moved to another supplier for: ${r.restarred.join(', ')}.` : ''}`,
      })
      navigate('/suppliers')
    }
  }

  return (
    <div className="flex flex-col gap-5">
      <div className="flex flex-wrap items-center gap-3">
        <TitleInput
          aria-label="Supplier name"
          value={name}
          onChange={(e) => setName(e.target.value)}
          onBlur={() => {
            const n = name.trim()
            if (n === '') setName(s.name)
            else if (n !== s.name) void patch({ name: n })
          }}
          className="min-w-0 flex-[1_1_14rem] border-line py-0.5 text-[24px]!"
        />
        {s.terms_are_placeholders ? <Pill tone="warn">Terms are a guess</Pill> : <Pill tone="ok">Terms confirmed</Pill>}
        <ConfirmTwiceButton
          armedLabel="Tap again to archive"
          onConfirm={archive}
          pending={w.pending}
          className="rounded-card"
        >
          Delete
        </ConfirmTwiceButton>
      </div>

      <Details s={s} patch={patch} pending={w.pending} />
      <Terms s={s} onSaved={onSaved} />
      <section className="border-t border-line pt-4">
        <Products data={data} onSaved={onSaved} />
      </section>
    </div>
  )
}

/* ----------------------------------------------------------------- details -- */

function href(url: string): string {
  return /^https?:\/\//i.test(url) ? url : `https://${url}`
}

function Details({ s, patch, pending }: { s: Supplier; patch: (b: PatchBody) => Promise<boolean>; pending: boolean }) {
  const [editing, setEditing] = useState(false)
  const link = (to: string, text: string, external = false) => (
    <a href={to} className="text-brand-ink underline" {...(external ? { target: '_blank', rel: 'noreferrer' } : {})}>
      {text}
    </a>
  )
  const rows = [
    { key: 'kind', label: 'Type', value: s.kind ?? '—' },
    {
      key: 'channel',
      label: 'How we order',
      value: channelLabel(s.order_channel),
    },
    { key: 'contact', label: 'Contact', value: s.contact ?? '—' },
    {
      key: 'url',
      label: 'Website / portal',
      value: s.order_url ? link(href(s.order_url), s.order_url, true) : '—',
    },
    {
      key: 'email',
      label: 'Email',
      value: s.email ? link(`mailto:${s.email}`, s.email) : '—',
    },
    {
      key: 'phone',
      label: 'Phone',
      value: s.phone ? link(`tel:${s.phone.replace(/\s+/g, '')}`, s.phone) : '—',
    },
  ]
  return (
    <Section
      title="Details"
      right={
        !editing && (
          <Button variant="secondary" size="sm" onClick={() => setEditing(true)}>
            Edit details
          </Button>
        )
      }
    >
      {editing ? (
        <DetailsForm s={s} patch={patch} pending={pending} onDone={() => setEditing(false)} />
      ) : (
        <>
          <ReadList rows={rows} />
          {s.notes && <p className="mt-3 whitespace-pre-wrap text-base text-ink-2">{s.notes}</p>}
        </>
      )}
    </Section>
  )
}

function DetailsForm({
  s,
  patch,
  pending,
  onDone,
}: {
  s: Supplier
  patch: (b: PatchBody) => Promise<boolean>
  pending: boolean
  onDone: () => void
}) {
  const [f, setF] = useState({
    kind: s.kind ?? '',
    channel: s.order_channel,
    contact: s.contact ?? '',
    url: s.order_url ?? '',
    email: s.email ?? '',
    phone: s.phone ?? '',
    notes: s.notes ?? '',
  })
  const kinds = s.kind && !KINDS.includes(s.kind) ? [s.kind, ...KINDS] : KINDS
  const emailErr = emailError(f.email)
  const nul = (v: string) => (v.trim() === '' ? null : v.trim())

  const save = async () => {
    if (emailErr) return
    const body: PatchBody = {}
    if (nul(f.kind) !== (s.kind ?? null)) body.kind = nul(f.kind)
    if (f.channel !== s.order_channel) body.order_channel = f.channel
    if (nul(f.contact) !== (s.contact ?? null)) body.contact = nul(f.contact)
    if (nul(f.url) !== (s.order_url ?? null)) body.order_url = nul(f.url)
    if (nul(f.email) !== (s.email ?? null)) body.email = nul(f.email)
    if (nul(f.phone) !== (s.phone ?? null)) body.phone = nul(f.phone)
    if (nul(f.notes) !== (s.notes ?? null)) body.notes = nul(f.notes)
    if (Object.keys(body).length === 0 || (await patch(body))) onDone()
  }

  return (
    <form
      className="flex flex-col gap-3"
      onSubmit={(e) => {
        e.preventDefault()
        void save()
      }}
    >
      <div className="grid grid-cols-1 gap-3 sm:grid-cols-2">
        <Field label="Type">
          <Select value={f.kind} onChange={(e) => setF({ ...f, kind: e.target.value })}>
            <option value="">—</option>
            {kinds.map((k) => (
              <option key={k} value={k}>
                {k}
              </option>
            ))}
          </Select>
        </Field>
        <Field label="How we order">
          <Select value={f.channel} onChange={(e) => setF({ ...f, channel: e.target.value as OrderChannel })}>
            {CHANNELS.map((c) => (
              <option key={c.value} value={c.value}>
                {c.label}
              </option>
            ))}
          </Select>
        </Field>
        <Field label="Contact person">
          <Input
            value={f.contact}
            placeholder="Name, rep, account no."
            onChange={(e) => setF({ ...f, contact: e.target.value })}
          />
        </Field>
        <Field label="Website / portal">
          <Input value={f.url} placeholder="https://" onChange={(e) => setF({ ...f, url: e.target.value })} />
        </Field>
        <Field label="Email" error={emailErr}>
          <Input
            type="email"
            value={f.email}
            placeholder="orders@…"
            onChange={(e) => setF({ ...f, email: e.target.value })}
          />
        </Field>
        <Field label="Phone">
          <Input
            type="tel"
            value={f.phone}
            placeholder="01382 …"
            onChange={(e) => setF({ ...f, phone: e.target.value })}
          />
        </Field>
      </div>
      <Field label="Notes">
        <Textarea
          rows={3}
          value={f.notes}
          placeholder="Account number, rep, anything"
          onChange={(e) => setF({ ...f, notes: e.target.value })}
        />
      </Field>
      <div className="flex flex-wrap gap-2">
        <Button
          type="submit"
          variant="primary"
          size="sm"
          disabled={emailErr !== undefined}
          pending={pending}
          pendingLabel="Saving…"
        >
          Save details
        </Button>
        <Button type="button" variant="ghost" size="sm" onClick={onDone}>
          Cancel
        </Button>
      </div>
    </form>
  )
}

/* ------------------------------------------------------------------- terms -- */

function Terms({ s, onSaved }: { s: Supplier; onSaved: (x: Saved) => void }) {
  const initial = useMemo(() => draftOf(s), [s])
  const [editing, setEditing] = useState(false)
  const [t, setT] = useState<TermsDraft>(initial)
  const [checked, setChecked] = useState(false)
  const [tried, setTried] = useState(false)
  const [changed, setChanged] = useState<string[] | null>(null)
  const cw = useWrite()
  useEffect(() => {
    if (cw.outcome?.tone === 'bad') onSaved({ tone: 'bad', text: cw.outcome.text })
  }, [cw.outcome, onSaved])

  const open = () => {
    setT(initial)
    setChecked(false)
    setTried(false)
    setChanged(null)
    setEditing(true)
  }
  const parsed = parseTerms(t, true)

  const confirm = async () => {
    setTried(true)
    if (parsed.value === null || !checked) return
    const v = parsed.value
    const r = await cw.run(() => confirmSupplierTerms(s.supplier_id, v), {
      invalidate: [KEYS.suppliers, KEYS.draft, KEYS.supplierProducts(s.supplier_id)],
    })
    if (r) {
      setChanged(r.changed)
      setEditing(false)
      onSaved({
        tone: 'ok',
        text: r.was_placeholder ? `${s.name}: terms confirmed` : 'Terms saved',
      })
    }
  }

  return (
    <Section
      title="Terms"
      right={
        !editing && (
          <Button variant={s.terms_are_placeholders ? 'primary' : 'secondary'} size="sm" onClick={open}>
            {s.terms_are_placeholders ? 'Check & confirm terms' : 'Edit terms'}
          </Button>
        )
      }
    >
      {editing ? (
        <form
          className="flex flex-col gap-4"
          onSubmit={(e) => {
            e.preventDefault()
            void confirm()
          }}
        >
          <p className="text-base text-ink-2">
            The six terms are saved together, and saving them marks them as confirmed with {s.name}. Orders are sized
            from them from the next run.
          </p>
          <TermsFields t={t} setT={setT} errors={tried ? parsed.errors : {}} />
          <div className="flex flex-col gap-1">
            <Checkbox checked={checked} onChange={setChecked} alert label={`I checked these terms with ${s.name}`} />
            {tried && !checked && (
              <span className="text-sm text-bad-ink">Tick this once you have checked them. A guess stays a guess.</span>
            )}
          </div>
          <div className="flex flex-wrap gap-2">
            <Button type="submit" variant="primary" size="sm" pending={cw.pending} pendingLabel="Confirming…">
              Confirm these terms
            </Button>
            <Button type="button" variant="ghost" size="sm" onClick={() => setEditing(false)}>
              Cancel
            </Button>
          </div>
        </form>
      ) : (
        <>
          {s.terms_are_placeholders && (
            <WarnBox className="mb-3">
              <strong className="text-alert">These terms are a guess.</strong> Nobody has checked them with {s.name}, so
              every order built on them says so. Ring them or check their site, then press{' '}
              <em>Check &amp; confirm terms</em>.
            </WarnBox>
          )}
          <div className={cx(s.terms_are_placeholders && 'italic text-ink-2')}>
            <ReadList rows={termsRows(s)} />
          </div>
          {changed !== null && (
            <p role="status" className="mt-2 text-sm text-ink-2">
              {changed.length === 0 ? 'Confirmed as they were: nothing moved.' : `Changed: ${changed.join('; ')}.`}
            </p>
          )}
        </>
      )}
    </Section>
  )
}
