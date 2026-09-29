/**
 * Loyalty card › Programme › Reward catalogue (`#/loyalty/programme/catalogue`,
 * `?p=<slug>` for a second programme): what a ready reward can be taken as
 * (`loyalty_reward_option`). Rendered under the Loyalty header, so it has no
 * header of its own.
 *
 * - No options: a ready reward is the programme's own reward text ("Any drink,
 *   on us"), for whatever earns on the card. That is the phase-1 behaviour and a
 *   perfectly good setting, so the empty state says so rather than nagging.
 * - One option: the till redeems it without asking.
 * - Several: the till asks which, then lists only the menu items it covers, and
 *   refuses one over its price cap.
 *
 * Options are never deleted -- a redeemed reward points at the option it was
 * taken as -- so "Archive" retires one (the till stops offering it) and "Restore"
 * brings it back. The server receives the whole catalogue in order; any option
 * missing from the list would be retired, so archived ones are always sent too.
 */
import { useEffect, useState } from 'react'
import { useQueryClient } from '@tanstack/react-query'
import { Button, Empty, ErrorBox, Field, Input, LinkButton, Loading, MoneyInput, PageBody, Pill, Segmented, SectionHead, cx } from '../../components/ui'
import { penceToPounds, poundsToPence } from '../../components/confirm/numbers'
import { gbp } from '../../lib/format'
import { MEMBERS_KEY, membersApi, usePrograms } from '../../lib/members-api'
import { href, navigate, useLocation } from '../../lib/router'
import type { ProgramFull, RewardOption, RewardOptionIn } from '../../lib/types/members'
import { PIN_RE, Panel, RulesSave, useWriteState } from './shared'

const MAX_OPTIONS = 12

export function Catalogue() {
  const q = usePrograms()
  const pick = useLocation().query.get('p') ?? ''
  const programs = q.data?.programs ?? []
  const current = programs.find((p) => p.slug === pick) ?? programs.find((p) => p.is_default) ?? programs[0] ?? null
  return (
    <>
      <PageBody className="bg-canvas">
        <div className="mb-4 flex flex-wrap items-baseline justify-between gap-x-4 gap-y-1">
          <div className="min-w-0">
            <SectionHead size="panel" right={<a href={href('/loyalty/programme')} className="text-sm font-semibold text-brand-ink no-underline hover:underline">‹ Programme</a>}>
              Reward catalogue
            </SectionHead>
            <p className="text-base text-ink-2">
              What a full card can be swapped for: any drink, a slice of cake, a pastry. Each option can have its own price cap.
            </p>
          </div>
          {q.isFetching && q.data && <span className="text-sm text-ink-2">Updating…</span>}
        </div>
        {q.isError && <ErrorBox error={q.error} what="the reward catalogue" />}
        {q.isPending && <Loading what="Reading the catalogue" />}
        {q.data && current === null && (
          <Empty roomy action={<LinkButton variant="primary" href={href('/loyalty/programme')}>Set up the programme</LinkButton>}>
            There is no rewards programme yet, so there is nothing to put in a catalogue.
          </Empty>
        )}
        {q.data && current !== null && (
          <div className="flex flex-col gap-5">
            {programs.length > 1 && (
              <Segmented
                label="Programme"
                showLabel
                value={current.slug}
                onChange={(slug) => {
                  const p = programs.find((x) => x.slug === slug)
                  navigate('/loyalty/programme/catalogue', { query: p && !p.is_default ? { p: p.slug } : {}, replace: true })
                }}
                options={programs.map((p) => ({ value: p.slug, label: p.name }))}
              />
            )}
            <CatalogueEditor key={current.slug} p={current} pinRequired={q.data.pin_required} />
          </div>
        )}
      </PageBody>
    </>
  )
}

/* --------------------------------------------------------------- drafts --- */

interface Draft {
  /** Stable key for React while unsaved. */
  key: string
  id: number | null
  name: string
  description: string
  scope: 'drinks' | 'all'
  keywords: string
  cap: string
  active: boolean
}

let seq = 0
function draftOf(o?: RewardOption): Draft {
  seq += 1
  return {
    key: o ? `o${o.id}` : `new${seq}`,
    id: o?.id ?? null,
    name: o?.name ?? '',
    description: o?.description ?? '',
    scope: o?.eligibility.scope ?? 'drinks',
    keywords: o?.eligibility.keywords.join(', ') ?? '',
    cap: penceToPounds(o?.max_price_pence ?? null),
    active: o?.active ?? true,
  }
}

const words = (t: string) =>
  t
    .split(',')
    .map((w) => w.trim().toLowerCase())
    .filter(Boolean)

function toIn(d: Draft, original?: RewardOption): RewardOptionIn {
  const c = poundsToPence(d.cap)
  return {
    ...(d.id !== null ? { id: d.id } : {}),
    name: d.name.trim(),
    description: d.description.trim() || null,
    // Keep any categories/templates the option was given elsewhere; this page edits scope and words.
    eligibility: {
      scope: d.scope,
      categories: original?.eligibility.categories ?? [],
      keywords: words(d.keywords),
      template_ids: original?.eligibility.template_ids ?? [],
    },
    max_price_pence: c.kind === 'value' ? c.value : null,
    active: d.active,
  }
}

function coversLine(d: Draft): string {
  const w = words(d.keywords)
  const base = d.scope === 'drinks' ? 'Any drink' : 'Anything on the menu'
  const named = w.length ? `${base} with “${w.join('”, “')}” in the name` : base
  const c = poundsToPence(d.cap)
  return c.kind === 'value' ? `${named}, up to ${gbp(c.value)}` : named
}

/* --------------------------------------------------------------- editor --- */

function CatalogueEditor({ p, pinRequired }: { p: ProgramFull; pinRequired: boolean }) {
  const qc = useQueryClient()
  const [drafts, setDrafts] = useState<Draft[]>(() => p.reward_options.map((o) => draftOf(o)))
  const [open, setOpen] = useState<string | null>(null)
  const [pin, setPin] = useState('')
  const w = useWriteState()
  useEffect(() => {
    setDrafts(p.reward_options.map((o) => draftOf(o)))
  }, [p])

  const byId = new Map(p.reward_options.map((o) => [o.id, o]))
  const sent = drafts.filter((d) => d.id !== null || d.name.trim() !== '').map((d) => toIn(d, d.id !== null ? byId.get(d.id) : undefined))
  const original = p.reward_options.map((o) => toIn(draftOf(o), o))
  const changed = JSON.stringify(sent) !== JSON.stringify(original)
  const badName = drafts.some((d) => d.name.trim() === '' && d.id !== null)
  const badCap = drafts.some((d) => poundsToPence(d.cap).kind === 'bad')
  const canSave = changed && !badName && !badCap && (!pinRequired || PIN_RE.test(pin))
  const live = drafts.filter((d) => d.active)
  const archived = drafts.filter((d) => !d.active && d.id !== null)

  const set = (key: string, patch: Partial<Draft>) => setDrafts((all) => all.map((d) => (d.key === key ? { ...d, ...patch } : d)))
  const add = () => {
    const d = draftOf()
    setDrafts((all) => [...all, d])
    setOpen(d.key)
  }

  const save = async () => {
    if (!canSave) return
    const r = await w.run(
      () => membersApi.editProgram(p.id, { reward_options: sent, ...(pin ? { manager_pin: pin } : {}) }),
      (data) => {
        const n = data.reward_options.filter((o) => o.active).length
        return `Saved. The till now offers ${n === 0 ? 'the reward text alone' : `${n} ${n === 1 ? 'option' : 'options'}`}.`
      },
    )
    setPin('')
    if (r) {
      setOpen(null)
      await qc.invalidateQueries({ queryKey: MEMBERS_KEY })
    }
  }

  return (
    <form
      className="grid min-w-0 gap-5 wide:grid-cols-[minmax(0,1fr)_minmax(260px,340px)]"
      onSubmit={(e) => {
        e.preventDefault()
        void save()
      }}
    >
      <div className="flex min-w-0 flex-col gap-5">
        <Panel
          title={`Offered on ${p.name}`}
          id="offered-h" as="h3"
          right={
            <Button variant="add" size="sm" onClick={add} disabled={drafts.length >= MAX_OPTIONS}>
              + Add an option
            </Button>
          }
        >
          {live.length === 0 ? (
            <div className="rounded-button border-[1.5px] border-dashed border-line px-4 py-4 text-base text-ink-2">
              <p>
                No options, so a full card is simply <b className="text-ink">“{p.reward_text}”</b>
                {p.reward_max_price_pence !== null ? `, up to ${gbp(p.reward_max_price_pence)}` : ''}, for whatever earns on this card.
              </p>
              <p className="mt-1">Add options when you want customers to choose, for example a slice of cake instead of a drink.</p>
            </div>
          ) : (
            <ul className="flex flex-col">
              {live.map((d) => (
                <OptionRow key={d.key} d={d} open={open === d.key} setOpen={(o) => setOpen(o ? d.key : null)} set={(patch) => set(d.key, patch)} remove={() => setDrafts((all) => all.filter((x) => x.key !== d.key))} />
              ))}
            </ul>
          )}
          {drafts.length >= MAX_OPTIONS && <p className="mt-2 text-sm text-ink-2">Twelve options is the most a programme can offer.</p>}
        </Panel>

        {archived.length > 0 && (
          <Panel title="Archived" id="archived-h" as="h3" right={<span className="text-sm text-ink-2">kept for past rewards; the till no longer offers them</span>}>
            <ul className="flex flex-col">
              {archived.map((d) => (
                <li key={d.key} className="grid grid-cols-[minmax(0,1fr)_auto] items-center gap-3 border-b-[1.5px] border-dashed border-line py-2 last:border-b-0">
                  <span className="min-w-0">
                    <span className="block truncate font-semibold text-ink-2">{d.name}</span>
                    <span className="block truncate text-sm text-ink-2">{coversLine(d)}</span>
                  </span>
                  <Button variant="outline" size="sm" onClick={() => set(d.key, { active: true })}>
                    Restore
                  </Button>
                </li>
              ))}
            </ul>
          </Panel>
        )}

        <Panel title="Save" id="cat-save-h" as="h3">
          {(badName || badCap) && <p className="mb-2 text-sm text-ink-2">{badName ? 'Every option needs a name.' : 'A price cap is pounds and pence, like 4.50.'}</p>}
          <RulesSave
            pinRequired={pinRequired}
            pin={pin}
            setPin={setPin}
            changes={changed ? 1 : 0}
            canSave={canSave}
            busy={w.busy}
            label="Save the catalogue"
            outcome={w.outcome}
          />
        </Panel>
      </div>

      <Panel title="At the till" id="till-note-h" as="h3" className="self-start">
        <dl className="flex flex-col gap-2 text-base">
          <dt className="font-bold">{live.length === 0 ? 'Now: no options' : live.length === 1 ? 'Now: one option' : `Now: ${live.length} options`}</dt>
          <dd className="text-ink-2">
            {live.length === 0
              ? 'Staff redeem the reward with one tap and pick the drink it went on.'
              : live.length === 1
                ? `Staff redeem “${live[0]?.name ?? ''}” without being asked which.`
                : 'Staff are asked which option, then see only the menu items it covers.'}
          </dd>
          <dt className="mt-2 font-bold">Price caps</dt>
          <dd className="text-ink-2">
            An option’s own cap wins; without one, the programme’s ({p.reward_max_price_pence === null ? 'none: any drink' : `up to ${gbp(p.reward_max_price_pence)}`}, set on{' '}
            <a href={href('/loyalty/programme')} className="font-bold text-brand-ink underline">
              Programme
            </a>
            ).
          </dd>
        </dl>
      </Panel>
    </form>
  )
}

function OptionRow({
  d,
  open,
  setOpen,
  set,
  remove,
}: {
  d: Draft
  open: boolean
  setOpen: (o: boolean) => void
  set: (patch: Partial<Draft>) => void
  remove: () => void
}) {
  const capBad = poundsToPence(d.cap).kind === 'bad'
  const nameBad = d.name.trim() === ''
  return (
    <li className="border-b-[1.5px] border-dashed border-line py-2.5 last:border-b-0">
      <div className="grid grid-cols-[minmax(0,1fr)_auto] items-center gap-3">
        <span className="min-w-0">
          <span className="flex flex-wrap items-center gap-2">
            <span className={cx('truncate font-bold', nameBad && 'text-ink-2')}>{d.name.trim() || 'New option'}</span>
            {d.id === null && <Pill tone="brand">Not saved yet</Pill>}
          </span>
          <span className="block truncate text-sm text-ink-2">{coversLine(d)}</span>
          {d.description.trim() && <span className="block truncate text-sm text-ink-2">Staff see: {d.description.trim()}</span>}
        </span>
        <span className="flex flex-wrap justify-end gap-2">
          <Button variant="outline" size="sm" aria-expanded={open} onClick={() => setOpen(!open)}>
            {open ? 'Done' : 'Edit'}
          </Button>
          {d.id === null ? (
            <Button variant="ghost" size="sm" onClick={remove}>
              Remove
            </Button>
          ) : (
            <Button variant="ghost" size="sm" onClick={() => set({ active: false })}>
              Archive
            </Button>
          )}
        </span>
      </div>
      {open && (
        <fieldset className="mt-3 grid gap-3 rounded-button bg-canvas p-3 sm:grid-cols-2">
          <legend className="sr-only">Edit {d.name || 'the new option'}</legend>
          <Field label="Name" error={nameBad ? 'A name customers and staff will recognise.' : undefined}>
            <Input value={d.name} onChange={(e) => set({ name: e.target.value })} maxLength={80} placeholder="Slice of cake" autoFocus={d.id === null} />
          </Field>
          <Field label="Note for staff (optional)">
            <Input value={d.description} onChange={(e) => set({ description: e.target.value })} maxLength={200} placeholder="Instead of the drink" />
          </Field>
          <div className="flex flex-col gap-2">
            <Segmented
              label="Covers"
              showLabel
              value={d.scope}
              onChange={(v) => set({ scope: v })}
              options={[
                { value: 'drinks', label: 'Drinks' },
                { value: 'all', label: 'Anything on the menu' },
              ]}
            />
            <Field label="Only items with these words in the name" hint="Comma-separated. Empty: everything above.">
              <Input value={d.keywords} onChange={(e) => set({ keywords: e.target.value })} placeholder="cake, slice" />
            </Field>
          </div>
          <Field label="Up to" hint="Empty: no cap of its own." error={capBad ? 'Pounds and pence, like 4.50.' : undefined}>
            <div className="w-32">
              <MoneyInput value={d.cap} onChange={(e) => set({ cap: e.target.value })} placeholder="no cap" />
            </div>
          </Field>
        </fieldset>
      )}
    </li>
  )
}
