/**
 * Loyalty card › Programme › Staff & devices (CONTRACT §2, §6): who can use the scanner,
 * with what role, and which devices it runs on.
 *
 * - Staff: role, active, Telegram id, and "Set PIN". The PIN identifies the
 *   person at the scanner, so PINs are unique among active staff; the server
 *   refuses a duplicate and the refusal is shown as written.
 * - Devices: the scanner only works on a paired device. "Pair a device" gets a
 *   6-digit code, valid for 15 minutes, typed into /staff on that device.
 *   Revoking signs out every session on it.
 *
 * From 640px the staff are a table; under it, one stacked row per person (the
 * Members list does the same at 900px).
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
  Pill,
  Select,
  StatusLine,
  Table,
  TBody,
  Td,
  Th,
  THead,
  Toggle,
  Tr,
} from '../../components/ui'
import { ago, dayFull } from '../../lib/format'
import { MEMBERS_KEY, membersApi, useDevices, useStaff } from '../../lib/members-api'
import type { DevicePairing, StaffDevice, StaffRole, StaffUser } from '../../lib/types/members'
import { PIN_RE, Panel, PinInput, ROLE_LABEL, useWriteState } from './shared'

const ROLES: StaffRole[] = ['STAFF', 'MANAGER', 'OWNER']

/** The two panels. `headingLevel="h3"` when they sit under a page section's h2. */
export function StaffAndDevices({ headingLevel = 'h2' }: { headingLevel?: 'h2' | 'h3' }) {
  const staff = useStaff()
  const devices = useDevices()
  return (
    <div className="grid gap-5 wide:grid-cols-[minmax(0,1.2fr)_minmax(0,1fr)]">
      <div className="flex min-w-0 flex-col gap-5">
        <Panel title="Staff" id="staff-h" as={headingLevel}>
          {staff.isError && <ErrorBox error={staff.error} what="the staff" />}
          {staff.isPending && <Loading what="Reading the staff" />}
          {staff.data && <StaffList users={staff.data.users} />}
          <AddStaff />
        </Panel>
      </div>
      <Panel title="Scanner devices" id="devices-h" as={headingLevel} className="self-start">
        {devices.isError && <ErrorBox error={devices.error} what="the devices" />}
        {devices.isPending && <Loading what="Reading the devices" />}
        {devices.data && <DeviceList devices={devices.data.devices} />}
        <PairDevice heading={headingLevel === 'h3' ? 'h4' : 'h3'} />
      </Panel>
    </div>
  )
}

/* ---------------------------------------------------------------- staff --- */

function StaffList({ users }: { users: StaffUser[] }) {
  if (users.length === 0) {
    return <p className="mb-3 text-base text-ink-2">Nobody yet. Add the first person below; the scanner needs at least one manager.</p>
  }
  return (
    <>
      <div className="hidden sm:block">
        <Table label="Staff" minWidth={560}>
          <THead>
            <tr>
              <Th>Name</Th>
              <Th>Role</Th>
              <Th>Telegram</Th>
              <Th>Active</Th>
              <Th>PIN</Th>
            </tr>
          </THead>
          <TBody>
            {users.map((u) => (
              <StaffRow key={u.id} u={u} />
            ))}
          </TBody>
        </Table>
      </div>
      <ul className="divide-y divide-line sm:hidden">
        {users.map((u) => (
          <StaffStackRow key={u.id} u={u} />
        ))}
      </ul>
    </>
  )
}

/** The writes one staff row can make, shared by the table row and the stacked row. */
function useStaffRow(u: StaffUser) {
  const qc = useQueryClient()
  const w = useWriteState()
  const [pinOpen, setPinOpen] = useState(false)
  const [pin, setPin] = useState('')
  const patch = async (body: Parameters<typeof membersApi.patchStaff>[1], ok: string) => {
    const r = await w.run(() => membersApi.patchStaff(u.id, body), () => ok)
    if (r !== null) void qc.invalidateQueries({ queryKey: [...MEMBERS_KEY, 'staff'] })
    return r !== null
  }
  const savePin = async () => {
    if (!PIN_RE.test(pin)) return
    const ok = await patch({ pin }, `New PIN saved for ${u.name}.`)
    setPin('')
    if (ok) setPinOpen(false)
  }
  const roleSelect = (size: 'xs' | 'sm') => (
    <Select
      size={size}
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
  )
  const activeToggle = (
    <Toggle
      checked={u.active}
      disabled={w.busy}
      label={<span className="sr-only">{`${u.name} can use the scanner`}</span>}
      onChange={(next) => void patch({ active: next }, next ? `${u.name} can use the scanner again.` : `${u.name} is switched off.`)}
    />
  )
  const pinButton = (
    <Button variant="outline" size="sm" aria-expanded={pinOpen} onClick={() => setPinOpen((o) => !o)}>
      Set PIN
    </Button>
  )
  const pinForm = pinOpen && (
    <form
      className="flex flex-wrap items-end gap-2"
      onSubmit={(e) => {
        e.preventDefault()
        void savePin()
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
  )
  return { w, roleSelect, activeToggle, pinButton, pinForm, pinOpen }
}

function StaffRow({ u }: { u: StaffUser }) {
  const { w, roleSelect, activeToggle, pinButton, pinForm, pinOpen } = useStaffRow(u)
  const extra = pinOpen || w.outcome !== null
  return (
    <>
      <Tr>
        <Td className="font-semibold">
          {u.name}
          {!u.active && <span className="block text-sm font-normal text-ink-2">switched off</span>}
        </Td>
        <Td>{roleSelect('xs')}</Td>
        <Td className="fig text-sm">{u.telegram_id ?? <span className="text-ink-2">not linked</span>}</Td>
        <Td>{activeToggle}</Td>
        <Td>{pinButton}</Td>
      </Tr>
      {extra && (
        <tr className="border-b border-line">
          <td colSpan={5} className="pb-2 pt-1">
            {pinForm}
            <StatusLine outcome={w.outcome} />
          </td>
        </tr>
      )}
    </>
  )
}

function StaffStackRow({ u }: { u: StaffUser }) {
  const { w, roleSelect, activeToggle, pinButton, pinForm } = useStaffRow(u)
  return (
    <li className="flex flex-col gap-2 py-3">
      <div className="flex items-center justify-between gap-3">
        <span className="min-w-0 truncate font-semibold">
          {u.name}
          {!u.active && <span className="block text-sm font-normal text-ink-2">switched off</span>}
        </span>
        {activeToggle}
      </div>
      <div className="flex flex-wrap items-center gap-2">
        {roleSelect('sm')}
        {pinButton}
      </div>
      <div className="fig text-sm">
        <span className="text-ink-2">Telegram </span>
        {u.telegram_id ?? <span className="text-ink-2">not linked</span>}
      </div>
      {pinForm}
      <StatusLine outcome={w.outcome} />
    </li>
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
        <StatusLine outcome={w.outcome} />
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
        <StatusLine outcome={w.outcome} />
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
      <StatusLine outcome={w.outcome} className="col-span-2" />
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

function PairDevice({ heading: H }: { heading: 'h3' | 'h4' }) {
  const qc = useQueryClient()
  const [name, setName] = useState('')
  const [pairing, setPairing] = useState<(DevicePairing & { name: string }) | null>(null)
  const w = useWriteState()
  const left = useCountdown(pairing?.expires_at ?? null)
  const expired = left === 0
  const code = pairing ? pairing.pairing_code.replace(/^(\d{3})(\d{3})$/, '$1 $2') : ''
  return (
    <div className="mt-4 border-t border-line pt-4">
      <H className="mb-2 text-md font-bold">Pair a device</H>
      {pairing ? (
        <div aria-live="polite">
          <p className="text-sm text-ink-2">
            On <b className="text-ink">{pairing.name}</b>, open <span className="fig">/staff</span> and type:
          </p>
          <div className={`fig my-2 text-4xl font-extrabold tracking-[.12em] ${expired ? 'text-ink-2 line-through' : ''}`}>{code}</div>
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
          <StatusLine outcome={w.outcome} className="basis-full" />
        </form>
      )}
    </div>
  )
}
