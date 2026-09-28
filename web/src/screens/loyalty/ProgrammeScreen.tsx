/**
 * Loyalty card › Programme (`#/loyalty/programme`, owner's design 5a/5b;
 * docs/loyalty/BACKOFFICE-V2.md §3 "Programme").
 *
 * Main column: the deal (stamps to fill a card, the reward, birthday / welcome /
 * expiry switches) and the sticker set. Side column: the card as members see it,
 * redrawn from the unsaved edits, and the joining link with its `?src=` tag. Staff &
 * devices (formerly their own page) sit underneath, then a link to the reward
 * catalogue and other cards.
 *
 * Edits are a draft until "Save changes": the bar says what changes ("8 → 10 stamps")
 * and asks for a manager's PIN only when the server says one is needed. Changes apply
 * to new stamps only; nobody loses what they have.
 */
import { useEffect, useMemo, useState } from 'react'
import { Button, ErrorBox, Input, Loading, PageBody, Segmented } from '../../components/ui'
import { href } from '../../lib/router'
import { loyaltyApi, useInvalidateLoyalty, useProgramSettings } from '../../lib/loyalty-api'
import type { ProgramSettings, ProgramSettingsIn, StickerKey } from '../../lib/types/loyalty'
import { StaffAndDevices } from '../members/Staff'
import { PIN_RE, Panel, PinInput } from '../members/shared'
import { LoyaltyHeader } from './LoyaltyHeader'
import { CardPreview } from './programme/CardPreview'
import { JoiningLink } from './programme/JoiningLink'
import { StickerSet } from './programme/StickerSet'
import { SwitchRow } from './programme/Switch'
import { STICKERS } from './stickers'

interface Draft {
  stamps_required: number
  reward_text: string
  birthday_reward: boolean
  welcome_stamp: boolean
  stamps_expire: boolean
  stickers: StickerKey[]
}

const STAMP_CHOICES = [6, 8, 10, 12]

function draftOf(p: ProgramSettings): Draft {
  return {
    stamps_required: p.stamps_required,
    reward_text: p.reward_text,
    birthday_reward: p.birthday_reward,
    welcome_stamp: p.welcome_stamp,
    stamps_expire: p.stamps_expire,
    stickers: [...p.stickers],
  }
}

const onOff = (b: boolean) => (b ? 'on' : 'off')

/** What would change, as the body to send and one short phrase per change. */
function diff(saved: Draft, d: Draft): { body: ProgramSettingsIn; phrases: string[] } {
  const body: ProgramSettingsIn = {}
  const phrases: string[] = []
  if (d.stamps_required !== saved.stamps_required) {
    body.stamps_required = d.stamps_required
    phrases.push(`${saved.stamps_required} → ${d.stamps_required} stamps`)
  }
  const reward = d.reward_text.trim()
  if (reward !== saved.reward_text) {
    body.reward_text = reward
    phrases.push('the reward')
  }
  if (d.birthday_reward !== saved.birthday_reward) {
    body.birthday_reward = d.birthday_reward
    phrases.push(`birthday drink ${onOff(d.birthday_reward)}`)
  }
  if (d.welcome_stamp !== saved.welcome_stamp) {
    body.welcome_stamp = d.welcome_stamp
    phrases.push(`welcome stamp ${onOff(d.welcome_stamp)}`)
  }
  if (d.stamps_expire !== saved.stamps_expire) {
    body.stamps_expire = d.stamps_expire
    phrases.push(`stamps ${d.stamps_expire ? 'expire' : 'never expire'}`)
  }
  if (d.stickers.join() !== saved.stickers.join()) {
    body.stickers = d.stickers
    phrases.push(
      d.stickers.length !== saved.stickers.length
        ? `${d.stickers.length} sticker${d.stickers.length === 1 ? '' : 's'} in the set`
        : 'sticker order',
    )
  }
  return { body, phrases }
}

export function ProgrammeScreen() {
  const q = useProgramSettings()
  return (
    <>
      <LoyaltyHeader current="programme" />
      <PageBody className="bg-canvas">
        {q.isPending && <Loading what="Reading the programme" />}
        {q.isError && <ErrorBox error={q.error} what="the programme" />}
        {q.data && <ProgrammeBody program={q.data} />}
      </PageBody>
    </>
  )
}

function ProgrammeBody({ program }: { program: ProgramSettings }) {
  const saved = useMemo(() => draftOf(program), [program])
  const [draft, setDraft] = useState<Draft>(saved)
  // A fresh read from the server (after a save, or another tab's) resets the draft.
  useEffect(() => setDraft(saved), [saved])
  const catalogue = program.sticker_catalogue.length ? program.sticker_catalogue : STICKERS
  const set = <K extends keyof Draft>(k: K, v: Draft[K]) => setDraft((d) => ({ ...d, [k]: v }))
  const stampChoices = STAMP_CHOICES.includes(saved.stamps_required)
    ? STAMP_CHOICES
    : [...STAMP_CHOICES, saved.stamps_required].sort((a, b) => a - b)
  const { body, phrases } = diff(saved, draft)

  return (
    <div className="flex flex-col gap-5">
      <div className="grid items-start gap-5 min-[820px]:grid-cols-[minmax(0,1.65fr)_minmax(17rem,1fr)]">
        <div className="flex min-w-0 flex-col gap-5">
          <Panel title="The deal" id="deal-h">
            <p className="-mt-2 mb-4 text-base text-ink-2">Changes apply to new stamps only; nobody loses what they have.</p>
            <div className="grid gap-4 sm:grid-cols-[minmax(0,1.1fr)_minmax(0,1fr)]">
              <div className="flex min-w-0 flex-col gap-1.5">
                <span className="text-sm font-bold" aria-hidden="true">
                  Stamps to fill a card
                </span>
                <Segmented
                  label="Stamps to fill a card"
                  value={String(draft.stamps_required)}
                  onChange={(v) => set('stamps_required', Number(v))}
                  options={stampChoices.map((n) => ({ value: String(n), label: <span className="fig">{n}</span> }))}
                  className="flex w-full [&>button]:flex-1"
                />
              </div>
              <label className="flex min-w-0 flex-col gap-1.5">
                <span className="text-sm font-bold">The reward</span>
                <Input
                  value={draft.reward_text}
                  onChange={(e) => set('reward_text', e.target.value)}
                  maxLength={120}
                  placeholder="A free drink, any drink"
                  missing={draft.reward_text.trim() === ''}
                />
              </label>
            </div>
            <div className="mt-4">
              <SwitchRow
                label="Birthday drink"
                hint="A free drink lands on the card in their birthday week"
                checked={draft.birthday_reward}
                onChange={(v) => set('birthday_reward', v)}
              />
              <SwitchRow
                label="Welcome stamp"
                hint="The first stamp is on us when they join"
                checked={draft.welcome_stamp}
                onChange={(v) => set('welcome_stamp', v)}
              />
              <SwitchRow
                label="Stamps expire"
                hint="Cards untouched for 12 months reset to zero"
                checked={draft.stamps_expire}
                onChange={(v) => set('stamps_expire', v)}
              />
            </div>
          </Panel>

          <Panel title="Stickers" id="stickers-h">
            <p className="-mt-2 mb-4 text-base text-ink-2">
              Each stamp is a sticker. The scanner goes through the ticked ones in order. Drag to reorder, tap to switch off.
            </p>
            <StickerSet catalogue={catalogue} enabled={draft.stickers} onChange={(v) => set('stickers', v)} />
          </Panel>
        </div>

        <div className="flex min-w-0 flex-col gap-5">
          <Panel title="What members see" id="preview-h">
            <CardPreview required={draft.stamps_required} stickers={draft.stickers} rewardText={draft.reward_text} />
          </Panel>
          <Panel title="Joining link" id="join-h">
            <p className="-mt-2 mb-3 text-base text-ink-2">
              Put it on the counter, the window and Instagram. Add a tag so Insights knows where people came from.
            </p>
            <JoiningLink joinUrl={program.join_url} sources={program.join_sources} />
          </Panel>
        </div>
      </div>

      <section aria-labelledby="staff-devices-h" className="flex flex-col gap-3">
        <div>
          <h2 id="staff-devices-h" className="text-lg font-extrabold tracking-[-.01em]">
            Staff & devices
          </h2>
          <p className="text-base text-ink-2">Who can stamp on the scanner, with what PIN, and which tablets and phones it is paired to.</p>
        </div>
        <StaffAndDevices />
      </section>

      <p className="pb-2">
        <a
          href={href('/loyalty/programme/catalogue')}
          className="text-sm font-semibold text-ink-2 no-underline hover:text-brand-ink hover:underline"
        >
          Reward catalogue and other cards →
        </a>
      </p>

      {phrases.length > 0 && (
        <SaveBar
          phrases={phrases}
          body={body}
          pinRequired={program.pin_required}
          invalid={draft.reward_text.trim() === '' || draft.stickers.length === 0}
          onDiscard={() => setDraft(saved)}
        />
      )}
    </div>
  )
}

function SaveBar({
  phrases,
  body,
  pinRequired,
  invalid,
  onDiscard,
}: {
  phrases: string[]
  body: ProgramSettingsIn
  pinRequired: boolean
  invalid: boolean
  onDiscard: () => void
}) {
  const invalidate = useInvalidateLoyalty()
  const [pin, setPin] = useState('')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const pinOk = !pinRequired || PIN_RE.test(pin)

  const save = async () => {
    setBusy(true)
    setError(null)
    const r = await loyaltyApi.saveProgram(pinRequired ? { ...body, manager_pin: pin } : body)
    setBusy(false)
    if (r.kind === 'ok') {
      setPin('')
      await invalidate()
    } else {
      setError(r.message)
    }
  }

  return (
    <div
      className="sticky bottom-0 z-10 -mx-4 border-t border-line bg-surface px-4 py-3 shadow-login sm:-mx-5 sm:px-5"
      role="region"
      aria-label="Unsaved changes"
    >
      <div className="flex flex-wrap items-center gap-x-4 gap-y-2">
        <p className="min-w-0 flex-[1_1_14rem] text-base">
          <span className="font-bold">Unsaved: </span>
          <span className="text-ink-2">{phrases.join(' · ')}</span>
        </p>
        {pinRequired && <PinInput value={pin} onChange={setPin} label="Manager PIN" className="w-32" />}
        <Button variant="ghost" onClick={onDiscard} disabled={busy}>
          Discard
        </Button>
        <Button onClick={() => void save()} disabled={busy || invalid || !pinOk}>
          {busy ? 'Saving…' : 'Save changes'}
        </Button>
      </div>
      {invalid && <p className="mt-1.5 text-sm text-bad-ink">The reward needs a few words before it can be saved.</p>}
      {error && (
        <p role="alert" className="mt-1.5 text-sm text-bad-ink">
          {error}
        </p>
      )}
    </div>
  )
}
