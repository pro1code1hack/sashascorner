/**
 * Rewards › Programme (`#/rewards`): the rules of each card the café runs
 * (loyalty_program, CONTRACT §0 and "Phase 3"). The first page of the Rewards
 * group, because it is the one the owner comes to configure.
 *
 * Left, the list: the main stamp card first, then any club (a matcha club, a
 * points card), each with its rule in one line and how many cards it has. Right,
 * the editor for the one picked (`#/rewards?p=<slug>`, or `?p=new`), in the order
 * a person thinks about a card:
 *
 * - The card: name, stamps or points (locked once anybody has earned, because a
 *   stamp is not a point), how many for a reward, what the reward is, what the
 *   pass says, running or paused.
 * - Premium drinks: every earning drink is one stamp whatever it costs; the free
 *   drink can be any drink or capped at a price.
 * - What earns: drinks or the whole menu, narrowed by name words, categories or
 *   drink templates, with a live list of what that matches (it writes nothing).
 * - At the till: most stamps in one scan and the cooldown (more than N stamps on
 *   one card within M minutes needs a manager's PIN).
 * - Extras (main card): birthday drink, referral stamps.
 *
 * What a ready reward can be taken as lives on its own page, Reward catalogue.
 * Only the fields that changed are sent; a manager's PIN approves them once a
 * manager exists (before that the back-office password is the guard).
 */
import { useEffect, useMemo, useState } from 'react'
import { useQueryClient } from '@tanstack/react-query'
import { Button, Checkbox, ErrorBox, Field, Input, Loading, MoneyInput, PageBody, PageHeader, Pill, Segmented, Toggle, cx } from '../../components/ui'
import { penceToPounds, poundsToPence } from '../../components/confirm/numbers'
import { gbp } from '../../lib/format'
import { MEMBERS_KEY, membersApi, useMenuFacets, usePrograms } from '../../lib/members-api'
import { href, navigate, useLocation } from '../../lib/router'
import type { Eligibility, EligibilityPreview, ProgramEditIn, ProgramFull, ProgramKind } from '../../lib/types/members'
import { PIN_RE, Panel, RulesSave, useWriteState } from './shared'

export function Programme() {
  const q = usePrograms()
  const loc = useLocation()
  const pick = loc.query.get('p') ?? ''
  const programs = q.data?.programs ?? []
  const current = pick === 'new' ? null : (programs.find((p) => p.slug === pick) ?? programs.find((p) => p.is_default) ?? null)
  return (
    <>
      <PageHeader
        title="Programme"
        subtitle="The rules of the card: how many stamps, what the reward is, what earns, and the checks at the till."
        saved={q.isFetching ? 'Loading…' : undefined}
      />
      <PageBody className="bg-canvas">
        {q.isError && <ErrorBox error={q.error} what="the programmes" />}
        {q.isPending && <Loading what="Reading the programmes" />}
        {q.data && programs.length === 0 && pick !== 'new' && (
          <Panel title="No programme yet" id="none-h">
            <p className="mb-3 text-base text-ink-2">Nothing is set up. Create the stamp card customers join from the website.</p>
            <Button variant="primary" onClick={() => navigate('/rewards', { query: { p: 'new' }, replace: true })}>
              Set up a programme
            </Button>
          </Panel>
        )}
        {q.data && (programs.length > 0 || pick === 'new') && (
          <div className="grid gap-5 compact:grid-cols-[minmax(220px,300px)_minmax(0,1fr)]">
            <ProgramList programs={programs} current={pick === 'new' ? 'new' : (current?.slug ?? '')} autoStamp={q.data.auto_stamp} />
            <Editor key={pick === 'new' ? 'new' : (current?.slug ?? 'none')} p={current} pinRequired={q.data.pin_required} />
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
  const go = (p: string) => navigate('/rewards', { query: p ? { p } : {}, replace: true })
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
                  {p.cards} {p.cards === 1 ? 'card' : 'cards'} · {types || 'no'} reward {types === 1 ? 'option' : 'options'}
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
        {autoStamp
          ? ': a receipt with a linked member attached earns on each of their cards.'
          : ' (the CAFEOPS_LOYALTY_AUTO_STAMP setting). Staff stamp with the scanner.'}
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

function Editor({ p, pinRequired }: { p: ProgramFull | null; pinRequired: boolean }) {
  const qc = useQueryClient()
  const facets = useMenuFacets().data
  const [slug, setSlug] = useState('')
  const [name, setName] = useState(p?.name ?? '')
  const [kind, setKind] = useState<ProgramKind>(p?.kind ?? 'STAMPS')
  const [required, setRequired] = useState(String(p?.stamps_required ?? 8))
  const [perPound, setPerPound] = useState(String(p?.points_per_pound ?? 10))
  const [perScan, setPerScan] = useState(String(p?.max_stamps_per_scan ?? 3))
  const [coolStamps, setCoolStamps] = useState(String(p?.cooldown_max_stamps ?? 3))
  const [coolMinutes, setCoolMinutes] = useState(String(p?.cooldown_minutes ?? 10))
  const [reward, setReward] = useState(p?.reward_text ?? '')
  const [ready, setReady] = useState(p?.reward_ready_label ?? 'Reward ready')
  const [description, setDescription] = useState(p?.description ?? '')
  const [capOn, setCapOn] = useState(p?.reward_max_price_pence != null)
  const [cap, setCap] = useState(penceToPounds(p?.reward_max_price_pence ?? null))
  const [birthday, setBirthday] = useState(p?.birthday_reward ?? false)
  const [referral, setReferral] = useState(String(p?.referral_stamps ?? 0))
  const [active, setActive] = useState(p?.active ?? false)
  const [scope, setScope] = useState<'drinks' | 'all'>(p?.eligibility.scope ?? 'drinks')
  const [keywords, setKeywords] = useState(p?.eligibility.keywords.join(', ') ?? '')
  const [cats, setCats] = useState<string[]>(p?.eligibility.categories ?? [])
  const [templates, setTemplates] = useState<number[]>(p?.eligibility.template_ids ?? [])
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
    setCoolStamps(String(p.cooldown_max_stamps))
    setCoolMinutes(String(p.cooldown_minutes))
    setReward(p.reward_text)
    setReady(p.reward_ready_label)
    setDescription(p.description ?? '')
    setCapOn(p.reward_max_price_pence !== null)
    setCap(penceToPounds(p.reward_max_price_pence))
    setBirthday(p.birthday_reward)
    setReferral(String(p.referral_stamps))
    setActive(p.active)
    setScope(p.eligibility.scope)
    setKeywords(p.eligibility.keywords.join(', '))
    setCats(p.eligibility.categories)
    setTemplates(p.eligibility.template_ids)
  }, [p])

  const points = kind === 'POINTS'
  const req = points ? wholeIn(required, 10, 10000) : wholeIn(required, 2, 20)
  const rate = wholeIn(perPound, 1, 100)
  const scan = wholeIn(perScan, 1, 10)
  const coolN = wholeIn(coolStamps, 1, 20)
  const coolM = wholeIn(coolMinutes, 1, 240)
  const coolBelowScan = !points && coolN !== null && scan !== null && coolN < scan
  const ref = wholeIn(referral, 0, 8)
  const capParsed = poundsToPence(cap)
  // undefined = a cap is switched on but is not a valid amount yet.
  const capPence: number | null | undefined = !capOn ? null : capParsed.kind === 'value' ? capParsed.value : undefined
  const rule: Eligibility = { scope, categories: cats, keywords: words(keywords), template_ids: templates }

  const body: ProgramEditIn = {}
  if (p === null) {
    Object.assign(body, {
      name: name.trim(),
      kind,
      reward_text: reward.trim(),
      reward_ready_label: ready.trim(),
      description: description.trim(),
      active,
      eligibility: same(rule, DEFAULT_RULE) ? null : rule,
    })
    if (req !== null) body.stamps_required = req
    if (points && rate !== null) body.points_per_pound = rate
    if (!points && scan !== null) body.max_stamps_per_scan = scan
    if (!points && coolN !== null) body.cooldown_max_stamps = coolN
    if (!points && coolM !== null) body.cooldown_minutes = coolM
    if (capPence !== undefined && capPence !== null) body.reward_max_price_pence = capPence
  } else {
    if (name.trim() !== p.name) body.name = name.trim()
    if (kind !== p.kind) body.kind = kind
    if (req !== null && (req !== p.stamps_required || kind !== p.kind)) body.stamps_required = req
    if (points && rate !== null && rate !== p.points_per_pound) body.points_per_pound = rate
    if (!points && scan !== null && scan !== p.max_stamps_per_scan) body.max_stamps_per_scan = scan
    if (!points && coolN !== null && coolN !== p.cooldown_max_stamps) body.cooldown_max_stamps = coolN
    if (!points && coolM !== null && coolM !== p.cooldown_minutes) body.cooldown_minutes = coolM
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
  }
  const changes = Object.keys(body).length
  const slugOk = p !== null || /^[a-z0-9][a-z0-9-]{1,38}[a-z0-9]$/.test(slug)
  const problems: string[] = []
  if (name.trim() === '') problems.push('a name')
  if (p === null && !slugOk) problems.push('a short name')
  if (req === null) problems.push(points ? 'points for a reward' : 'stamps for a reward')
  if (points && rate === null) problems.push('points per pound')
  if (!points && (scan === null || coolN === null || coolM === null || coolBelowScan)) problems.push('the till checks')
  if (reward.trim() === '') problems.push('what the reward is')
  if (ready.trim() === '') problems.push('what the card says when ready')
  if (capPence === undefined) problems.push('the price cap')
  if (ref === null) problems.push('referral stamps')
  const valid = problems.length === 0
  const canSave = valid && changes > 0 && (!pinRequired || PIN_RE.test(pin))

  const save = async () => {
    if (!canSave) return
    const sent: ProgramEditIn = pin ? { ...body, manager_pin: pin } : body
    const r = await w.run(
      () => (p === null ? membersApi.createProgram({ ...sent, slug }) : membersApi.editProgram(p.id, sent)),
      (data) =>
        p === null
          ? `Created ${data.name}${data.active ? '' : ', paused until you switch it on'}.`
          : `Saved ${changes} ${changes === 1 ? 'change' : 'changes'}. Cards pick them up at their next update.`,
    )
    setPin('')
    if (r) {
      await qc.invalidateQueries({ queryKey: MEMBERS_KEY })
      if (p === null) navigate('/rewards', { query: { p: r.data.slug }, replace: true })
    }
  }

  const locked = p !== null && !p.kind_editable
  const unit = points ? 'points' : 'stamps'
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
            <Field label="Name customers see" className="sm:col-span-2" error={name.trim() === '' ? 'The card needs a name.' : undefined}>
              <Input value={name} onChange={(e) => setName(e.target.value)} maxLength={80} placeholder="Sasha’s Corner Rewards" />
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
                label="Customers collect"
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
                  ? 'Members have earned on this card, so this stays: a stamp is not a point. Start a new programme to change it.'
                  : points
                    ? 'Points for every pound spent, rounded down. Staff type the spend; till receipts count their total.'
                    : 'One stamp per item that earns, like the paper card.'}
              </p>
            </div>
            <Field
              label={points ? 'Points for a reward' : 'Stamps for a reward'}
              error={req === null ? (points ? 'A whole number, 10 to 10000.' : 'A whole number, 2 to 20.') : undefined}
            >
              <div className="w-28">
                <Input numeric value={required} onChange={(e) => setRequired(e.target.value)} />
              </div>
            </Field>
            {points && (
              <Field label="Points per £1" hint={rate !== null ? `£4.50 earns ${Math.floor((450 * rate) / 100)} points.` : undefined} error={rate === null ? '1 to 100.' : undefined}>
                <div className="w-24">
                  <Input numeric value={perPound} onChange={(e) => setPerPound(e.target.value)} />
                </div>
              </Field>
            )}
            <Field label="The reward, as customers read it" className="sm:col-span-2" error={reward.trim() === '' ? 'Say what the reward is.' : undefined}>
              <Input value={reward} onChange={(e) => setReward(e.target.value)} maxLength={120} placeholder="Any drink, on us" />
            </Field>
            <Field
              label="When it is ready, the card says"
              hint={`Before then it reads “${/\sready$/i.test(ready) ? ready.replace(/\sready$/i, '') : 'Reward'} after 3 more${points ? ' points' : ''}”.`}
              error={ready.trim() === '' ? 'Say what the card shows.' : undefined}
            >
              <Input value={ready} onChange={(e) => setReady(e.target.value)} maxLength={60} placeholder="Free drink ready" />
            </Field>
            <Field label="One line for the join page and the pass">
              <Input value={description} onChange={(e) => setDescription(e.target.value)} maxLength={200} placeholder="Every 9th drink is on us." />
            </Field>
            <div className="sm:col-span-2">
              <Toggle checked={active} onChange={setActive} label={active ? 'Running: open to join, earning' : 'Paused: not offered to new customers, earns nothing new'} />
            </div>
          </div>
        </Panel>

        <Panel title="Premium drinks" id="premium-h">
          <p className="mb-3 text-sm text-ink-2">
            {points
              ? 'Points follow the spend, so a dearer drink earns more. '
              : 'Every drink that earns gets one stamp, whatever it costs: a large rose latte earns the same one stamp as an espresso. '}
            What you choose here is how far the free reward goes.
          </p>
          <div className="flex flex-wrap items-end gap-4">
            <Segmented
              label="The free drink can be"
              showLabel
              value={capOn ? 'cap' : 'any'}
              onChange={(v) => setCapOn(v === 'cap')}
              options={[
                { value: 'any', label: 'Any drink' },
                { value: 'cap', label: 'Up to a price' },
              ]}
            />
            {capOn && (
              <Field
                label="Up to"
                hint={capPence != null ? `A drink over ${gbp(capPence)} is refused at the till.` : undefined}
                error={capPence === undefined ? 'Pounds and pence, like 4.50.' : undefined}
              >
                <div className="w-32">
                  <MoneyInput value={cap} onChange={(e) => setCap(e.target.value)} placeholder="4.50" />
                </div>
              </Field>
            )}
          </div>
          <p className="mt-3 text-sm text-ink-2">
            To offer a choice (a drink, or a slice of cake up to £4.50), add options in the{' '}
            <a href={href('/rewards/catalogue', p && !p.is_default ? { p: p.slug } : undefined)} className="font-bold text-brand-ink underline">
              reward catalogue
            </a>
            .
          </p>
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
            unit={unit}
          />
        </Panel>

        {!points && (
          <Panel title="At the till" id="till-h" right={<span className="text-sm text-ink-2">checks on the scanner</span>}>
            <div className="grid gap-4 sm:grid-cols-[auto_minmax(0,1fr)]">
              <Field label="Most stamps in one scan" hint="For a round paid by one person." error={scan === null ? '1 to 10.' : undefined}>
                <div className="w-24">
                  <Input numeric value={perScan} onChange={(e) => setPerScan(e.target.value)} />
                </div>
              </Field>
              <fieldset className="flex min-w-0 flex-col gap-1">
                <legend className="mb-1 text-xs font-bold text-ink-2">Cooldown: a manager’s PIN for more than</legend>
                <div className="flex flex-wrap items-center gap-2 text-base">
                  <div className="w-20">
                    <Input numeric aria-label="Stamps" value={coolStamps} onChange={(e) => setCoolStamps(e.target.value)} missing={coolN === null || coolBelowScan} />
                  </div>
                  <span>stamps on one card within</span>
                  <div className="w-20">
                    <Input numeric aria-label="Minutes" value={coolMinutes} onChange={(e) => setCoolMinutes(e.target.value)} missing={coolM === null} />
                  </div>
                  <span>minutes</span>
                </div>
                <p className={cx('text-xs', coolN === null || coolM === null || coolBelowScan ? 'text-bad-ink' : 'text-ink-2')}>
                  {coolN === null
                    ? 'Stamps: 1 to 20.'
                    : coolM === null
                      ? 'Minutes: 1 to 240.'
                      : coolBelowScan
                        ? `At least ${scan}, the most stamps in one scan, or every full scan would need a manager.`
                        : 'Stops one card being stamped again and again. The scanner asks for the PIN and the approval is logged.'}
                </p>
              </fieldset>
            </div>
          </Panel>
        )}

        {p?.is_default && (
          <Panel title="Extras" id="extras-h">
            <div className="grid gap-4 sm:grid-cols-2">
              <div className="flex flex-col gap-1">
                <Toggle checked={birthday} onChange={setBirthday} label="Birthday drink" />
                <p className="text-xs text-ink-2">A free drink from 7 days before the birthday to 7 days after, once a year.</p>
              </div>
              <div className="flex flex-col gap-2">
                <Toggle
                  checked={ref !== 0}
                  onChange={(on) => setReferral(on ? (p.referral_stamps > 0 ? String(p.referral_stamps) : '1') : '0')}
                  label="Referrals"
                />
                {ref !== 0 ? (
                  <Field label="Stamps to the member who referred them" hint="Given when the new member’s first stamp lands." error={ref === null ? '1 to 8.' : undefined}>
                    <div className="w-24">
                      <Input numeric value={referral} onChange={(e) => setReferral(e.target.value)} />
                    </div>
                  </Field>
                ) : (
                  <p className="text-xs text-ink-2">Off: nobody earns for bringing a friend.</p>
                )}
              </div>
            </div>
          </Panel>
        )}

        <Panel title="Save" id="save-h">
          {!valid && <p className="mb-2 text-sm text-bad-ink">Still needed: {problems.join(', ')}.</p>}
          <RulesSave
            pinRequired={pinRequired}
            pin={pin}
            setPin={setPin}
            changes={changes}
            canSave={canSave}
            busy={w.busy}
            label={p === null ? 'Create the programme' : 'Save the rules'}
            outcome={w.outcome}
          />
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
        label="Earns on"
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
