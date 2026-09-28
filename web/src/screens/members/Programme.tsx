/**
 * Members › Programme: the programmes the café runs and their rules
 * (loyalty_program, CONTRACT §0 and "Phase 3").
 *
 * Left, the list: the main stamp card first, then any club (a matcha club, a
 * points card), each with its rule in one line and how many cards it has. Right,
 * the editor for the one picked (`#/members/programme?p=<slug>`, or `?p=new`):
 *
 * - kind: stamps (one per item) or points (per pound, integer pence on the
 *   server); locked once anybody has earned, because a stamp is not a point;
 * - what earns: drinks or the whole menu, narrowed by name words, categories or
 *   drink templates, with a live list of what that matches (it writes nothing);
 * - the reward catalogue: what a ready reward can be taken as ("Any drink",
 *   "Slice of cake" up to £4.50). Empty = the reward text alone, as before;
 * - a manager's PIN for every change. Only the fields that changed are sent.
 */
import { useEffect, useMemo, useState } from 'react'
import { useQueryClient } from '@tanstack/react-query'
import { Button, Checkbox, ErrorBox, Field, Input, Loading, MoneyInput, PageBody, PageHeader, Pill, Segmented, Toggle, cx } from '../../components/ui'
import { penceToPounds, poundsToPence } from '../../components/confirm/numbers'
import { gbp } from '../../lib/format'
import { MEMBERS_KEY, membersApi, useMenuFacets, usePrograms } from '../../lib/members-api'
import { navigate, useLocation } from '../../lib/router'
import type { Eligibility, EligibilityPreview, ProgramEditIn, ProgramFull, ProgramKind, RewardOptionIn } from '../../lib/types/members'
import { MembersTabs, OutcomeLine, PIN_RE, Panel, PinInput, useWriteState } from './shared'

export function Programme() {
  const q = usePrograms()
  const loc = useLocation()
  const pick = loc.query.get('p') ?? ''
  const programs = q.data?.programs ?? []
  const current = pick === 'new' ? null : (programs.find((p) => p.slug === pick) ?? programs.find((p) => p.is_default) ?? null)
  return (
    <>
      <PageHeader title="Members" subtitle={<MembersTabs current="programme" />} saved={q.isFetching ? 'Loading…' : undefined} />
      <PageBody className="bg-canvas">
        {q.isError && <ErrorBox error={q.error} what="the programmes" />}
        {q.isPending && <Loading what="Reading the programmes" />}
        {q.data && (
          <div className="grid gap-5 compact:grid-cols-[minmax(240px,320px)_minmax(0,1fr)]">
            <ProgramList programs={programs} current={pick === 'new' ? 'new' : (current?.slug ?? '')} autoStamp={q.data.auto_stamp} />
            <Editor key={pick === 'new' ? 'new' : (current?.slug ?? 'none')} p={current} />
          </div>
        )}
      </PageBody>
    </>
  )
}

/* ----------------------------------------------------------------- list --- */

function ruleLine(p: ProgramFull): string {
  if (p.kind === 'POINTS') return `${p.points_per_pound ?? '?'} points a pound · reward at ${p.stamps_required}`
  return `${p.stamps_required} stamps · ${p.earns}`
}

function ProgramList({ programs, current, autoStamp }: { programs: ProgramFull[]; current: string; autoStamp: boolean }) {
  const go = (p: string) => navigate('/members/programme', { query: p ? { p } : {}, replace: true })
  return (
    <Panel title="Programmes" id="progs-h" className="self-start">
      <ul className="-mx-2 flex flex-col">
        {programs.map((p) => {
          const on = p.slug === current
          const types = p.reward_options.filter((o) => o.active).length
          return (
            <li key={p.id}>
              <button
                type="button"
                aria-current={on ? 'true' : undefined}
                onClick={() => go(p.is_default ? '' : p.slug)}
                className={cx('flex w-full flex-col gap-0.5 rounded-button px-2 py-2 text-left', on ? 'bg-brand-wash' : 'hover:bg-canvas')}
              >
                <span className="flex flex-wrap items-center gap-2">
                  <span className="font-bold">{p.name}</span>
                  {!p.active && <Pill tone="muted">Paused</Pill>}
                  {p.is_default && <Pill tone="neutral">Main card</Pill>}
                </span>
                <span className="text-sm text-ink-2">{ruleLine(p)}</span>
                <span className="fig text-sm text-ink-2">
                  {p.cards} {p.cards === 1 ? 'card' : 'cards'} · {types || 'no'} reward {types === 1 ? 'type' : 'types'}
                </span>
              </button>
            </li>
          )
        })}
      </ul>
      <Button variant="secondary" size="sm" className="mt-3" onClick={() => go('new')} aria-pressed={current === 'new'}>
        New programme
      </Button>
      <p className="mt-4 border-t border-line pt-3 text-sm text-ink-2">
        Stamps from Lightspeed receipts are <b className="text-ink">{autoStamp ? 'on' : 'off'}</b> on this server
        {autoStamp ? ': a receipt with a linked member attached earns on each of their cards.' : ' (CAFEOPS_LOYALTY_AUTO_STAMP).'}
      </p>
    </Panel>
  )
}

/* --------------------------------------------------------------- editor --- */

function wholeIn(t: string, min: number, max: number): number | null {
  if (!/^\d{1,5}$/.test(t.trim())) return null
  const n = Number(t.trim())
  return n >= min && n <= max ? n : null
}

const words = (t: string) =>
  t
    .split(',')
    .map((w) => w.trim().toLowerCase())
    .filter(Boolean)
const same = (a: unknown, b: unknown) => JSON.stringify(a) === JSON.stringify(b)
const DEFAULT_RULE: Eligibility = { scope: 'drinks', categories: [], keywords: [], template_ids: [] }

interface OptionDraft {
  id: number | null
  name: string
  description: string
  scope: 'drinks' | 'all'
  keywords: string
  cap: string
  active: boolean
}

function optionDraft(o?: ProgramFull['reward_options'][number]): OptionDraft {
  return {
    id: o?.id ?? null,
    name: o?.name ?? '',
    description: o?.description ?? '',
    scope: o?.eligibility.scope ?? 'drinks',
    keywords: o?.eligibility.keywords.join(', ') ?? '',
    cap: penceToPounds(o?.max_price_pence ?? null),
    active: o?.active ?? true,
  }
}

function optionIn(o: OptionDraft): RewardOptionIn {
  const c = poundsToPence(o.cap)
  return {
    ...(o.id !== null ? { id: o.id } : {}),
    name: o.name.trim(),
    description: o.description.trim() || null,
    eligibility: { scope: o.scope, categories: [], keywords: words(o.keywords), template_ids: [] },
    max_price_pence: c.kind === 'value' ? c.value : null,
    active: o.active,
  }
}

function Editor({ p }: { p: ProgramFull | null }) {
  const qc = useQueryClient()
  const facets = useMenuFacets().data
  const [slug, setSlug] = useState('')
  const [name, setName] = useState(p?.name ?? '')
  const [kind, setKind] = useState<ProgramKind>(p?.kind ?? 'STAMPS')
  const [required, setRequired] = useState(String(p?.stamps_required ?? 8))
  const [perPound, setPerPound] = useState(String(p?.points_per_pound ?? 10))
  const [perScan, setPerScan] = useState(String(p?.max_stamps_per_scan ?? 3))
  const [reward, setReward] = useState(p?.reward_text ?? '')
  const [ready, setReady] = useState(p?.reward_ready_label ?? 'Reward ready')
  const [description, setDescription] = useState(p?.description ?? '')
  const [cap, setCap] = useState(penceToPounds(p?.reward_max_price_pence ?? null))
  const [birthday, setBirthday] = useState(p?.birthday_reward ?? false)
  const [referral, setReferral] = useState(String(p?.referral_stamps ?? 0))
  const [active, setActive] = useState(p?.active ?? false)
  const [scope, setScope] = useState<'drinks' | 'all'>(p?.eligibility.scope ?? 'drinks')
  const [keywords, setKeywords] = useState(p?.eligibility.keywords.join(', ') ?? '')
  const [cats, setCats] = useState<string[]>(p?.eligibility.categories ?? [])
  const [templates, setTemplates] = useState<number[]>(p?.eligibility.template_ids ?? [])
  const [options, setOptions] = useState<OptionDraft[]>(() => (p?.reward_options ?? []).map((o) => optionDraft(o)))
  const [pin, setPin] = useState('')
  const w = useWriteState()

  // A refetch after saving replaces the starting point.
  useEffect(() => {
    if (!p) return
    setName(p.name)
    setKind(p.kind)
    setRequired(String(p.stamps_required))
    setPerPound(String(p.points_per_pound ?? 10))
    setPerScan(String(p.max_stamps_per_scan))
    setReward(p.reward_text)
    setReady(p.reward_ready_label)
    setDescription(p.description ?? '')
    setCap(penceToPounds(p.reward_max_price_pence))
    setBirthday(p.birthday_reward)
    setReferral(String(p.referral_stamps))
    setActive(p.active)
    setScope(p.eligibility.scope)
    setKeywords(p.eligibility.keywords.join(', '))
    setCats(p.eligibility.categories)
    setTemplates(p.eligibility.template_ids)
    setOptions(p.reward_options.map((o) => optionDraft(o)))
  }, [p])

  const points = kind === 'POINTS'
  const req = points ? wholeIn(required, 10, 10000) : wholeIn(required, 2, 20)
  const rate = wholeIn(perPound, 1, 100)
  const scan = wholeIn(perScan, 1, 10)
  const ref = wholeIn(referral, 0, 8)
  const capParsed = poundsToPence(cap)
  const capPence = capParsed.kind === 'value' ? capParsed.value : capParsed.kind === 'blank' ? null : undefined
  const rule: Eligibility = { scope, categories: cats, keywords: words(keywords), template_ids: templates }
  const optionsIn = options.filter((o) => o.name.trim() || o.id !== null).map(optionIn)
  const optionsBad = options.some((o) => (o.name.trim() === '' && o.id !== null) || poundsToPence(o.cap).kind === 'bad')

  const body: ProgramEditIn = { manager_pin: pin }
  if (p === null) {
    Object.assign(body, {
      name: name.trim(),
      kind,
      reward_text: reward.trim(),
      reward_ready_label: ready.trim(),
      description: description.trim(),
      active,
      eligibility: same(rule, DEFAULT_RULE) ? null : rule,
      reward_options: optionsIn,
    })
    if (req !== null) body.stamps_required = req
    if (points && rate !== null) body.points_per_pound = rate
    if (!points && scan !== null) body.max_stamps_per_scan = scan
    if (capPence !== undefined && capPence !== null) body.reward_max_price_pence = capPence
  } else {
    if (name.trim() !== p.name) body.name = name.trim()
    if (kind !== p.kind) body.kind = kind
    if (req !== null && (req !== p.stamps_required || kind !== p.kind)) body.stamps_required = req
    if (points && rate !== null && rate !== p.points_per_pound) body.points_per_pound = rate
    if (!points && scan !== null && scan !== p.max_stamps_per_scan) body.max_stamps_per_scan = scan
    if (reward.trim() !== p.reward_text) body.reward_text = reward.trim()
    if (ready.trim() !== p.reward_ready_label) body.reward_ready_label = ready.trim()
    if (description.trim() !== (p.description ?? '')) body.description = description.trim()
    if (capPence !== undefined && capPence !== p.reward_max_price_pence) body.reward_max_price_pence = capPence
    if (p.is_default) {
      if (birthday !== p.birthday_reward) body.birthday_reward = birthday
      if (ref !== null && ref !== p.referral_stamps) body.referral_stamps = ref
    }
    if (active !== p.active) body.active = active
    if (!same(rule, p.eligibility)) body.eligibility = same(rule, DEFAULT_RULE) ? null : rule
    if (!same(optionsIn, p.reward_options.map((o) => optionIn(optionDraft(o))))) body.reward_options = optionsIn
  }
  const changes = Object.keys(body).length - 1
  const slugOk = p !== null || /^[a-z0-9][a-z0-9-]{1,38}[a-z0-9]$/.test(slug)
  const valid =
    req !== null &&
    (!points || rate !== null) &&
    (points || scan !== null) &&
    ref !== null &&
    capPence !== undefined &&
    name.trim() !== '' &&
    reward.trim() !== '' &&
    ready.trim() !== '' &&
    slugOk &&
    !optionsBad
  const canSave = valid && changes > 0 && PIN_RE.test(pin)

  const save = async () => {
    if (!canSave) return
    const r = await w.run(
      () => (p === null ? membersApi.createProgram({ ...body, slug }) : membersApi.editProgram(p.id, body)),
      (data) =>
        p === null
          ? `Created ${data.name}${data.active ? '' : ', paused until you switch it on'}.`
          : `Saved ${changes} ${changes === 1 ? 'change' : 'changes'}. Cards pick them up at their next update.`,
    )
    setPin('')
    if (r) {
      await qc.invalidateQueries({ queryKey: MEMBERS_KEY })
      if (p === null) navigate('/members/programme', { query: { p: r.data.slug }, replace: true })
    }
  }

  const locked = p !== null && !p.kind_editable
  return (
    <div className="grid min-w-0 gap-5 2xl:grid-cols-[minmax(0,1fr)_minmax(260px,320px)]">
      <form
        className="flex min-w-0 flex-col gap-5"
        onSubmit={(e) => {
          e.preventDefault()
          void save()
        }}
      >
        <Panel title={p === null ? 'New programme' : p.name} id="rules-h" right={p && <span className="fig text-sm text-ink-2">{p.slug}</span>}>
          <div className="grid gap-4 sm:grid-cols-2">
            <Field label="Name customers see" className="sm:col-span-2">
              <Input value={name} onChange={(e) => setName(e.target.value)} maxLength={80} placeholder="Matcha club" />
            </Field>
            {p === null && (
              <Field
                label="Short name"
                hint="Lower case and dashes; used in links and wallet ids, never changes."
                error={slug && !slugOk ? '3 to 40 of a–z, 0–9 and dashes.' : undefined}
                className="sm:col-span-2"
              >
                <div className="w-64 max-w-full">
                  <Input value={slug} onChange={(e) => setSlug(e.target.value.toLowerCase().replace(/[^a-z0-9-]/g, ''))} className="fig" placeholder="matcha-club" />
                </div>
              </Field>
            )}
            <div className="flex flex-col gap-1 sm:col-span-2">
              <Segmented
                label="Kind"
                showLabel
                value={kind}
                onChange={(k) => {
                  if (!locked) setKind(k)
                }}
                options={[
                  { value: 'STAMPS', label: 'Stamps' },
                  { value: 'POINTS', label: 'Points per £' },
                ]}
              />
              <p className="text-xs text-ink-2">
                {locked
                  ? 'Members have earned on this card, so its kind stays: a stamp is not a point. Start a new programme to change it.'
                  : points
                    ? 'Points for every pound spent, rounded down. Staff type the spend; till receipts count their total.'
                    : 'One stamp per item that earns, like the paper card.'}
              </p>
            </div>
            <Field label={points ? 'Points for a reward' : 'Stamps for a reward'} error={req === null ? (points ? 'A whole number, 10 to 10000.' : 'A whole number, 2 to 20.') : undefined}>
              <div className="w-28">
                <Input numeric value={required} onChange={(e) => setRequired(e.target.value)} />
              </div>
            </Field>
            {points ? (
              <Field label="Points per £1" hint={rate !== null ? `£4.50 earns ${Math.floor((450 * rate) / 100)} points.` : undefined} error={rate === null ? '1 to 100.' : undefined}>
                <div className="w-24">
                  <Input numeric value={perPound} onChange={(e) => setPerPound(e.target.value)} />
                </div>
              </Field>
            ) : (
              <Field label="Most stamps in one scan" hint="For a round paid by one person." error={scan === null ? '1 to 10.' : undefined}>
                <div className="w-24">
                  <Input numeric value={perScan} onChange={(e) => setPerScan(e.target.value)} />
                </div>
              </Field>
            )}
            <Field label="The reward, as customers read it" className="sm:col-span-2">
              <Input value={reward} onChange={(e) => setReward(e.target.value)} maxLength={120} placeholder="A matcha of your choice, on us" />
            </Field>
            <Field
              label="When it is ready, the card says"
              hint={`Also names it before then: “${/\sready$/i.test(ready) ? ready.replace(/\sready$/i, '') : 'Reward'} after 3 more${points ? ' points' : ''}”.`}
            >
              <Input value={ready} onChange={(e) => setReady(e.target.value)} maxLength={60} placeholder="Free matcha ready" />
            </Field>
            <Field
              label="Price cap on the reward"
              hint={capPence === null ? 'Empty: no cap.' : capPence !== undefined ? `Up to ${gbp(capPence)}.` : undefined}
              error={capParsed.kind === 'bad' ? capParsed.message : undefined}
            >
              <div className="w-32">
                <MoneyInput value={cap} onChange={(e) => setCap(e.target.value)} placeholder="no cap" />
              </div>
            </Field>
            <Field label="One line for the join page and the pass" className="sm:col-span-2">
              <Input value={description} onChange={(e) => setDescription(e.target.value)} maxLength={200} placeholder="Every 6th matcha is free. Counts alongside your main card." />
            </Field>
            {p?.is_default && (
              <>
                <Field label="Referral stamps" hint="To the member who referred a new one. 0 turns referrals off." error={ref === null ? '0 to 8.' : undefined}>
                  <div className="w-24">
                    <Input numeric value={referral} onChange={(e) => setReferral(e.target.value)} />
                  </div>
                </Field>
                <div className="flex flex-col gap-1">
                  <Toggle checked={birthday} onChange={setBirthday} label="Birthday drink" />
                  <p className="text-xs text-ink-2">7 days either side of the birthday, once a year.</p>
                </div>
              </>
            )}
            <div className="sm:col-span-2">
              <Toggle checked={active} onChange={setActive} label={active ? 'Running: open to join' : 'Paused: not offered, earns nothing new'} />
            </div>
          </div>
        </Panel>

        <Panel title="What earns" id="earns-h">
          <EarnsEditor
            scope={scope}
            setScope={setScope}
            keywords={keywords}
            setKeywords={setKeywords}
            cats={cats}
            setCats={setCats}
            templates={templates}
            setTemplates={setTemplates}
            categories={facets?.categories ?? []}
            templateList={facets?.templates ?? []}
            rule={rule}
            unit={points ? 'points' : 'stamps'}
          />
        </Panel>

        <Panel title="Reward types" id="options-h" right={<span className="text-sm text-ink-2">what a ready reward can be</span>}>
          <OptionsEditor options={options} setOptions={setOptions} />
        </Panel>

        <Panel title="Save" id="save-h">
          <div className="flex flex-wrap items-end gap-3">
            <Field label="Manager PIN" hint={changes === 0 ? 'Nothing changed yet.' : `${changes} ${changes === 1 ? 'change' : 'changes'} to save.`}>
              <div className="w-36">
                <PinInput value={pin} onChange={setPin} />
              </div>
            </Field>
            <Button type="submit" variant="primary" size="sm" disabled={!canSave} pending={w.busy} pendingLabel="Saving…" className="mb-5">
              {p === null ? 'Create the programme' : 'Save the rules'}
            </Button>
          </div>
          <OutcomeLine outcome={w.outcome} />
        </Panel>
      </form>
      <Preview name={name} points={points} req={req} ready={ready} reward={reward} active={active} rate={rate} />
    </div>
  )
}

/* ----------------------------------------------------------- what earns --- */

function EarnsEditor(props: {
  scope: 'drinks' | 'all'
  setScope: (s: 'drinks' | 'all') => void
  keywords: string
  setKeywords: (s: string) => void
  cats: string[]
  setCats: (c: string[]) => void
  templates: number[]
  setTemplates: (t: number[]) => void
  categories: string[]
  templateList: { id: number; name: string }[]
  rule: Eligibility
  unit: string
}) {
  const [preview, setPreview] = useState<EligibilityPreview | null>(null)
  const [previewErr, setPreviewErr] = useState<string | null>(null)
  const ruleKey = JSON.stringify(props.rule)
  useEffect(() => {
    const t = window.setTimeout(async () => {
      const r = await membersApi.previewEligibility(JSON.parse(ruleKey) as Eligibility)
      if (r.kind === 'ok') {
        setPreview(r.data)
        setPreviewErr(null)
      } else setPreviewErr(r.message)
    }, 300)
    return () => window.clearTimeout(t)
  }, [ruleKey])
  const toggle = <T,>(list: T[], v: T) => (list.includes(v) ? list.filter((x) => x !== v) : [...list, v])
  return (
    <div className="flex flex-col gap-4">
      <Segmented
        label="From"
        showLabel
        value={props.scope}
        onChange={props.setScope}
        options={[
          { value: 'drinks', label: 'Drinks' },
          { value: 'all', label: 'Anything on the menu' },
        ]}
      />
      <Field label="Only items with these words in the name" hint="Comma-separated. Empty: all of the above. Most menu items have no category, so words are the dependable filter.">
        <Input value={props.keywords} onChange={(e) => props.setKeywords(e.target.value)} placeholder="e.g. matcha" />
      </Field>
      {props.categories.length > 0 && (
        <fieldset>
          <legend className="mb-1 text-xs font-bold text-ink-2">…or in these categories</legend>
          <div className="flex flex-wrap gap-x-4 gap-y-1">
            {props.categories.map((c) => (
              <Checkbox key={c} checked={props.cats.includes(c.toLowerCase())} onChange={() => props.setCats(toggle(props.cats, c.toLowerCase()))} label={c} />
            ))}
          </div>
        </fieldset>
      )}
      {props.templateList.length > 0 && (
        <fieldset>
          <legend className="mb-1 text-xs font-bold text-ink-2">…or made from these recipe templates</legend>
          <div className="flex flex-wrap gap-x-4 gap-y-1">
            {props.templateList.map((t) => (
              <Checkbox key={t.id} checked={props.templates.includes(t.id)} onChange={() => props.setTemplates(toggle(props.templates, t.id))} label={t.name} />
            ))}
          </div>
        </fieldset>
      )}
      <div className="rounded-button bg-canvas px-3 py-2.5 text-sm" role="status">
        {previewErr ? (
          <span className="text-ink-2">{previewErr}</span>
        ) : preview === null ? (
          <span className="text-ink-2">Working out what that matches…</span>
        ) : (
          <>
            <b className="fig">
              {preview.count} menu {preview.count === 1 ? 'row' : 'rows'}
            </b>{' '}
            earn {props.unit} ({preview.covers}){preview.sample.length > 0 ? ': ' : '.'}
            <span className="text-ink-2">{preview.sample.join(', ')}</span>
            {preview.count === 0 && <span className="block font-bold text-bad-ink">Nothing on the menu matches: this card could never earn.</span>}
          </>
        )}
      </div>
    </div>
  )
}

/* -------------------------------------------------------- reward types --- */

function OptionsEditor({ options, setOptions }: { options: OptionDraft[]; setOptions: (o: OptionDraft[]) => void }) {
  const set = (i: number, patch: Partial<OptionDraft>) => setOptions(options.map((o, j) => (j === i ? { ...o, ...patch } : o)))
  const live = options.filter((o) => o.active)
  return (
    <div className="flex flex-col gap-3">
      <p className="text-sm text-ink-2">
        {live.length === 0
          ? 'None: a ready reward is the reward text above, for whatever earns on this card.'
          : live.length === 1
            ? 'One type: the till redeems it without asking.'
            : `${live.length} types: the till asks which one, then lists only what it covers.`}
      </p>
      {options.map((o, i) => {
        const capBad = poundsToPence(o.cap).kind === 'bad'
        return (
          <fieldset key={o.id ?? `new-${i}`} className={cx('grid gap-3 rounded-button border border-line p-3 sm:grid-cols-2', !o.active && 'opacity-60')}>
            <legend className="sr-only">Reward type {i + 1}</legend>
            <Field label="Name">
              <Input value={o.name} onChange={(e) => set(i, { name: e.target.value })} maxLength={80} placeholder="Slice of cake" />
            </Field>
            <Field label="Note for staff">
              <Input value={o.description} onChange={(e) => set(i, { description: e.target.value })} maxLength={200} placeholder="Instead of the drink" />
            </Field>
            <div className="flex flex-col gap-2">
              <Segmented
                label="Covers"
                showLabel
                value={o.scope}
                onChange={(v) => set(i, { scope: v })}
                options={[
                  { value: 'drinks', label: 'Drinks' },
                  { value: 'all', label: 'Menu' },
                ]}
              />
              <Input value={o.keywords} onChange={(e) => set(i, { keywords: e.target.value })} placeholder="words in the name: cake, slice" aria-label="Words in the name" />
            </div>
            <Field label="Up to" error={capBad ? 'Pounds and pence, like 4.50.' : undefined}>
              <div className="w-32">
                <MoneyInput value={o.cap} onChange={(e) => set(i, { cap: e.target.value })} placeholder="no cap" />
              </div>
            </Field>
            <div className="flex items-center gap-3 sm:col-span-2">
              {o.id === null ? (
                <Button variant="link" onClick={() => setOptions(options.filter((_, j) => j !== i))}>
                  Remove
                </Button>
              ) : (
                <Toggle checked={o.active} onChange={(v) => set(i, { active: v })} label={o.active ? 'Offered' : 'Retired (kept for history)'} />
              )}
            </div>
          </fieldset>
        )
      })}
      <div>
        <Button variant="secondary" size="sm" onClick={() => setOptions([...options, optionDraft()])} disabled={options.length >= 12}>
          Add a reward type
        </Button>
      </div>
    </div>
  )
}

/* -------------------------------------------------------------- preview --- */

function Preview({
  name,
  points,
  req,
  ready,
  reward,
  active,
  rate,
}: {
  name: string
  points: boolean
  req: number | null
  ready: string
  reward: string
  active: boolean
  rate: number | null
}) {
  const now = useMemo(() => (req === null ? null : points ? Math.round(req * 0.64) : Math.min(5, req - 1)), [req, points])
  const thing = /\sready$/i.test(ready) ? ready.replace(/\sready$/i, '') : 'Reward'
  return (
    <Panel title="On the card" id="preview-h" className="self-start">
      <p className="text-sm text-ink-2">What a member part-way there sees on the pass, with the rules as typed.</p>
      <dl className="mt-3 grid grid-cols-[minmax(0,6.5rem)_minmax(0,1fr)] gap-x-3 gap-y-1.5 text-base">
        <dt className="text-ink-2">Programme</dt>
        <dd className="font-semibold">{name.trim() || '—'}</dd>
        <dt className="text-ink-2">Header</dt>
        <dd className="fig">
          {points ? 'Points' : 'Stamps'} {now ?? '—'}/{req ?? '—'}
        </dd>
        <dt className="text-ink-2">Line</dt>
        <dd>{req !== null && now !== null ? `${thing} after ${req - now} more${points ? ' points' : ''}` : '—'}</dd>
        <dt className="text-ink-2">When full</dt>
        <dd>{ready.trim() || '—'}</dd>
        <dt className="text-ink-2">Reward</dt>
        <dd>{reward.trim() || '—'}</dd>
        {points && (
          <>
            <dt className="text-ink-2">Earning</dt>
            <dd className="fig">{rate !== null ? `${rate} a pound` : '—'}</dd>
          </>
        )}
        <dt className="text-ink-2">State</dt>
        <dd className="font-semibold">{active ? 'Running' : 'Paused'}</dd>
      </dl>
    </Panel>
  )
}
