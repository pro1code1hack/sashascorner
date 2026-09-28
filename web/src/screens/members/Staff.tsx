/**
 * Rewards › Staff & devices (`#/rewards/staff`) (CONTRACT §2, §6): who can use the scanner, with
 * what role, and which devices it runs on.
 *
 * - Staff: role, active, Telegram id, and "Set PIN". The PIN identifies the
 *   person at the scanner, so PINs are unique among active staff; the server
 *   refuses a duplicate and the refusal is shown as written.
 * - Devices: the scanner only works on a paired device. "Pair a device" gets a
 *   6-digit code, valid for 15 minutes, typed into /staff on that device.
 *   Revoking signs out every session on it.
 * Stamping alerts have their own page (Rewards › Alerts).
 */
import { useEffect, useState } from 'react'
import { useQueryClient } from '@tanstack/react-query'
import {
  Button,
  ConfirmTwiceButton,
  ErrorBox,
  Field,
  Input,
  Loading,
  PageBody,
  PageHeader,
  Pill,
  Select,
  Toggle,
} from '../../components/ui'
import { ago, dayFull } from '../../lib/format'
import { MEMBERS_KEY, membersApi, useDevices, useStaff } from '../../lib/members-api'
import type { DevicePairing, StaffDevice, StaffRole, StaffUser } from '../../lib/types/members'
import { OutcomeLine, PIN_RE, Panel, PinInput, ROLE_LABEL, useWriteState } from './shared'

const ROLES: StaffRole[] = ['STAFF', 'MANAGER', 'OWNER']

export function Staff() {
  const staff = useStaff()
  const devices = useDevices()
  return (
    <>
      <PageHeader title="Staff & devices" subtitle="Who can stamp on the scanner, with what PIN, and which tablets and phones it is paired to." />
      <PageBody className="bg-canvas">
        <div className="grid gap-5 wide:grid-cols-[minmax(0,1.2fr)_minmax(0,1fr)]">
          <div className="flex min-w-0 flex-col gap-5">
            <Panel title="Staff" id="staff-h">
              {staff.isError && <ErrorBox error={staff.error} what="the staff" />}
              {staff.isPending && <Loading what="Reading the staff" />}
              {staff.data && <StaffTable users={staff.data.users} />}
              <AddStaff />
            </Panel>
          </div>
          <Panel title="Scanner devices" id="devices-h" className="self-start">
            {devices.isError && <ErrorBox error={devices.error} what="the devices" />}
            {devices.isPending && <Loading what="Reading the devices" />}
            {devices.data && <DeviceList devices={devices.data.devices} />}
            <PairDevice />
          </Panel>
        </div>
      </PageBody>
    </>
  )
}

/* ---------------------------------------------------------------- staff --- */

const STAFF_COLS = 'sm:grid-cols-[minmax(0,1fr)_7.5rem_minmax(0,7rem)_3.5rem_5.25rem]'

function StaffTable({ users }: { users: StaffUser[] }) {
  if (users.length === 0) {
    return <p className="mb-3 text-base text-ink-2">Nobody yet. Add the first person below; the scanner needs at least one manager.</p>
  }
  return (
    <div role="table" aria-label="Staff">
      <div role="row" className={`hidden gap-3 border-b border-line-soft pb-2 text-label font-bold uppercase tracking-[.06em] text-ink-3 sm:grid ${STAFF_COLS}`}>
        <span role="columnheader">Name</span>
        <span role="columnheader">Role</span>
        <span role="columnheader">Telegram</span>
        <span role="columnheader">Active</span>
        <span role="columnheader">PIN</span>
      </div>
      {users.map((u) => (
        <StaffRow key={u.id} u={u} />
      ))}
    </div>
  )
}

function StaffRow({ u }: { u: StaffUser }) {
  const qc = useQueryClient()
  const w = useWriteState()
  const [pinOpen, setPinOpen] = useState(false)
  const [pin, setPin] = useState('')
  const patch = async (body: Parameters<typeof membersApi.patchStaff>[1], ok: string) => {
    const r = await w.run(() => membersApi.patchStaff(u.id, body), () => ok)
    if (r !== null) void qc.invalidateQueries({ queryKey: [...MEMBERS_KEY, 'staff'] })
    return r !== null
  }
  return (
    <div role="rowgroup" className="border-b border-line py-2 last:border-b-0">
      <div role="row" className={`grid grid-cols-[minmax(0,1fr)_7.5rem] items-center gap-x-3 gap-y-2 ${STAFF_COLS}`}>
        <span role="cell" className={u.active ? 'min-w-0 truncate font-semibold' : 'min-w-0 truncate font-semibold text-ink-2'}>
          {u.name}
          {!u.active && <span className="block text-sm font-normal">switched off</span>}
        </span>
        <span role="cell">
          <Select
            size="xs"
            aria-label={`Role for ${u.name}`}
            value={u.role}
            disabled={w.busy}
            onChange={(e) => void patch({ role: e.target.value as StaffRole }, `${u.name} is now ${ROLE_LABEL[e.target.value as StaffRole].toLowerCase()}.`)}
          >
            {ROLES.map((r) => (
              <option key={r} value={r}>
                {ROLE_LABEL[r]}
              </option>
            ))}
          </Select>
        </span>
        <span role="cell" className="fig truncate text-sm">
          <span className="text-ink-2 sm:hidden">Telegram </span>
          {u.telegram_id ?? <span className="text-ink-2">not linked</span>}
        </span>
        <span role="cell" className="flex items-center justify-end gap-3 sm:contents">
          <span className="sm:block">
            <Toggle
              checked={u.active}
              disabled={w.busy}
              label={<span className="sr-only">{u.active ? `${u.name} can use the scanner` : `${u.name} is switched off`}</span>}
              onChange={(next) => void patch({ active: next }, next ? `${u.name} can use the scanner again.` : `${u.name} is switched off.`)}
            />
          </span>
          <span className="sm:block">
            <Button variant="outline" size="sm" aria-expanded={pinOpen} onClick={() => setPinOpen((o) => !o)}>
              Set PIN
            </Button>
          </span>
        </span>
      </div>
      {pinOpen && (
        <form
          className="mt-2 flex flex-wrap items-end gap-2"
          onSubmit={async (e) => {
            e.preventDefault()
            if (!PIN_RE.test(pin)) return
            const ok = await patch({ pin }, `New PIN saved for ${u.name}.`)
            setPin('')
            if (ok) setPinOpen(false)
          }}
        >
          <Field label={`New PIN for ${u.name}`} hint="4–6 digits, different from everyone else’s.">
            <div className="w-36">
              <PinInput value={pin} onChange={setPin} />
            </div>
          </Field>
          <Button type="submit" variant="primary" size="sm" disabled={!PIN_RE.test(pin)} pending={w.busy} pendingLabel="Saving…" className="mb-5">
            Save PIN
          </Button>
        </form>
      )}
      <OutcomeLine outcome={w.outcome} className="mt-1" />
    </div>
  )
}

function AddStaff() {
  const qc = useQueryClient()
  const [open, setOpen] = useState(false)
  const [name, setName] = useState('')
  const [role, setRole] = useState<StaffRole>('STAFF')
  const [pin, setPin] = useState('')
  const [tg, setTg] = useState('')
  const w = useWriteState()
  const tgBad = tg.trim() !== '' && !/^\d{5,15}$/.test(tg.trim())
  const ready = name.trim() !== '' && PIN_RE.test(pin) && !tgBad
  if (!open) {
    return (
      <div className="mt-3 flex flex-wrap items-center gap-3">
        <Button variant="add" onClick={() => setOpen(true)}>
          + Add a person
        </Button>
        <OutcomeLine outcome={w.outcome} />
      </div>
    )
  }
  return (
    <form
      className="mt-4 grid gap-3 border-t border-line pt-4 sm:grid-cols-2"
      onSubmit={async (e) => {
        e.preventDefault()
        if (!ready) return
        const r = await w.run(
          () => membersApi.addStaff({ name: name.trim(), role, pin, telegram_id: tg.trim() ? Number(tg.trim()) : null }),
          () => `${name.trim()} added. They sign in on the scanner with their PIN.`,
        )
        setPin('')
        if (r !== null) {
          setName('')
          setTg('')
          setOpen(false)
          void qc.invalidateQueries({ queryKey: [...MEMBERS_KEY, 'staff'] })
        }
      }}
    >
      <Field label="Name">
        <Input value={name} onChange={(e) => setName(e.target.value)} maxLength={80} autoFocus />
      </Field>
      <Field label="Role" hint={role === 'STAFF' ? 'Stamps and redeems.' : 'Also approves corrections and the cooldown.'}>
        <Select value={role} onChange={(e) => setRole(e.target.value as StaffRole)}>
          {ROLES.map((r) => (
            <option key={r} value={r}>
              {ROLE_LABEL[r]}
            </option>
          ))}
        </Select>
      </Field>
      <Field label="PIN" hint="4–6 digits. It is how the scanner knows who they are.">
        <PinInput value={pin} onChange={setPin} />
      </Field>
      <Field label="Telegram id (optional)" hint="For /member in the bot." error={tgBad ? 'Digits only, as the bot reports it.' : undefined}>
        <Input value={tg} inputMode="numeric" onChange={(e) => setTg(e.target.value)} className="fig" />
      </Field>
      <div className="flex flex-wrap items-center gap-2 sm:col-span-2">
        <Button type="submit" variant="primary" size="sm" disabled={!ready} pending={w.busy} pendingLabel="Adding…">
          Add person
        </Button>
        <Button variant="secondary" size="sm" onClick={() => setOpen(false)} disabled={w.busy}>
          Cancel
        </Button>
        <OutcomeLine outcome={w.outcome} />
      </div>
    </form>
  )
}

/* -------------------------------------------------------------- devices --- */

function deviceState(d: StaffDevice): { text: string; tone: 'neutral' | 'muted' | 'brand' } {
  if (d.revoked_at) return { text: `Revoked ${dayFull(d.revoked_at)}`, tone: 'muted' }
  if (d.registered_at) return { text: 'Paired', tone: 'neutral' }
  return { text: 'Waiting for its code', tone: 'brand' }
}

function DeviceList({ devices }: { devices: StaffDevice[] }) {
  if (devices.length === 0) return <p className="text-base text-ink-2">No devices yet. Pair the till tablet first.</p>
  const sorted = [...devices].sort((a, b) => Number(a.revoked_at !== null) - Number(b.revoked_at !== null) || a.name.localeCompare(b.name))
  return (
    <ul>
      {sorted.map((d) => (
        <DeviceRow key={d.id} d={d} />
      ))}
    </ul>
  )
}

function DeviceRow({ d }: { d: StaffDevice }) {
  const qc = useQueryClient()
  const w = useWriteState()
  const s = deviceState(d)
  return (
    <li className="grid grid-cols-[minmax(0,1fr)_auto] items-center gap-x-3 gap-y-1 border-b-[1.5px] border-dashed border-line py-2">
      <span className="min-w-0">
        <span className="flex flex-wrap items-center gap-2">
          <span className="truncate font-semibold">{d.name}</span>
          <Pill tone={s.tone}>{s.text}</Pill>
        </span>
        <span className="block text-sm text-ink-2">
          {d.registered_at ? `Paired ${dayFull(d.registered_at)}` : 'Not paired yet'}
          {d.last_seen_at ? ` · last used ${ago(d.last_seen_at)}` : ''}
        </span>
      </span>
      {!d.revoked_at && (
        <ConfirmTwiceButton
          variant="outline"
          size="sm"
          armedLabel="Tap again to revoke"
          pending={w.busy}
          pendingLabel="Revoking…"
          onConfirm={async () => {
            const r = await w.run(() => membersApi.revokeDevice(d.id), () => `${d.name} revoked; anyone signed in on it is signed out.`)
            if (r !== null) void qc.invalidateQueries({ queryKey: [...MEMBERS_KEY, 'devices'] })
          }}
        >
          Revoke
        </ConfirmTwiceButton>
      )}
      <OutcomeLine outcome={w.outcome} className="col-span-2" />
    </li>
  )
}

function useCountdown(until: string | null): number | null {
  const [now, setNow] = useState(() => Date.now())
  useEffect(() => {
    if (until === null) return
    const t = window.setInterval(() => setNow(Date.now()), 1000)
    return () => window.clearInterval(t)
  }, [until])
  if (until === null) return null
  return Math.max(0, Math.floor((Date.parse(until) - now) / 1000))
}

function PairDevice() {
  const qc = useQueryClient()
  const [name, setName] = useState('')
  const [pairing, setPairing] = useState<(DevicePairing & { name: string }) | null>(null)
  const w = useWriteState()
  const left = useCountdown(pairing?.expires_at ?? null)
  const expired = left === 0
  const code = pairing ? pairing.pairing_code.replace(/^(\d{3})(\d{3})$/, '$1 $2') : ''
  return (
    <div className="mt-4 border-t border-line pt-4">
      <h3 className="mb-2 text-md font-bold">Pair a device</h3>
      {pairing ? (
        <div aria-live="polite">
          <p className="text-sm text-ink-2">
            On <b className="text-ink">{pairing.name}</b>, open <span className="fig">/staff</span> and type:
          </p>
          <div className={`fig my-2 text-4xl font-extrabold tracking-[.12em] ${expired ? 'text-ink-3 line-through' : ''}`}>{code}</div>
          <p className="fig text-sm text-ink-2">
            {expired ? (
              'This code has expired.'
            ) : (
              <>
                Expires in {Math.floor((left ?? 0) / 60)}:{String((left ?? 0) % 60).padStart(2, '0')}
              </>
            )}
          </p>
          <div className="mt-3 flex flex-wrap gap-2">
            <Button
              variant={expired ? 'primary' : 'secondary'}
              size="sm"
              onClick={() => {
                setPairing(null)
                w.setOutcome(null)
                void qc.invalidateQueries({ queryKey: [...MEMBERS_KEY, 'devices'] })
              }}
            >
              {expired ? 'Start again' : 'Done'}
            </Button>
          </div>
        </div>
      ) : (
        <form
          className="flex flex-wrap items-end gap-2"
          onSubmit={async (e) => {
            e.preventDefault()
            if (!name.trim()) return
            const r = await w.run(() => membersApi.pairDevice(name.trim()), () => undefined)
            if (r) {
              setPairing({ ...r.data, name: name.trim() })
              setName('')
              void qc.invalidateQueries({ queryKey: [...MEMBERS_KEY, 'devices'] })
            }
          }}
        >
          <Field label="Device name" className="min-w-0 flex-1 basis-44">
            <Input value={name} onChange={(e) => setName(e.target.value)} placeholder="Till tablet" maxLength={80} />
          </Field>
          <Button type="submit" variant="primary" size="sm" disabled={!name.trim()} pending={w.busy} pendingLabel="Getting a code…">
            Get a pairing code
          </Button>
          <OutcomeLine outcome={w.outcome} className="basis-full" />
        </form>
      )}
    </div>
  )
}
