/**
 * "+ Add member": a customer at the counter who would rather not scan the QR.
 * Name and one contact; birthday optional; news only on the customer's own yes.
 * The server records terms as accepted now, so the form says staff checked.
 */
import { useState } from 'react'
import { Button, Checkbox, Drawer, Field, Input, Select } from '../../../components/ui'
import { loyaltyApi, useInvalidateLoyalty } from '../../../lib/loyalty-api'
import { navigate } from '../../../lib/router'

const MONTHS = ['January', 'February', 'March', 'April', 'May', 'June', 'July', 'August', 'September', 'October', 'November', 'December']

export function AddMemberDrawer({ open, onClose }: { open: boolean; onClose: () => void }) {
  const invalidate = useInvalidateLoyalty()
  const [name, setName] = useState('')
  const [email, setEmail] = useState('')
  const [phone, setPhone] = useState('')
  const [day, setDay] = useState('')
  const [month, setMonth] = useState('')
  const [optIn, setOptIn] = useState(false)
  const [agreed, setAgreed] = useState(false)
  const [pending, setPending] = useState(false)
  const [error, setError] = useState<string | null>(null)

  const reset = () => {
    setName('')
    setEmail('')
    setPhone('')
    setDay('')
    setMonth('')
    setOptIn(false)
    setAgreed(false)
    setError(null)
  }

  const birthdayBad = (day === '') !== (month === '')
  const canSave = name.trim() !== '' && (email.trim() !== '' || phone.trim() !== '') && agreed && !birthdayBad

  const save = async () => {
    if (!canSave) return
    setPending(true)
    setError(null)
    const r = await loyaltyApi.createMember({
      first_name: name.trim(),
      email: email.trim() || null,
      phone: phone.trim() || null,
      birthday: day && month ? `${day.padStart(2, '0')}-${month.padStart(2, '0')}` : null,
      marketing_opt_in: optIn,
      source: 'back-office',
    })
    setPending(false)
    if (r.kind !== 'ok') {
      setError(r.message)
      return
    }
    await invalidate()
    reset()
    onClose()
    navigate(`/loyalty/members/${r.data.member.member_id}`)
  }

  return (
    <Drawer
      open={open}
      onClose={onClose}
      title="Add a member"
      context="For someone at the counter who would rather not scan the code"
      footer={
        <>
          <Button variant="secondary" className="flex-1" onClick={onClose}>
            Cancel
          </Button>
          <Button variant="primary" className="flex-1" disabled={!canSave} pending={pending} pendingLabel="Adding…" onClick={() => void save()}>
            Add member
          </Button>
        </>
      }
    >
      <form
        className="flex flex-col gap-4"
        onSubmit={(e) => {
          e.preventDefault()
          void save()
        }}
      >
        <Field label="Name">
          <Input value={name} maxLength={40} autoComplete="off" onChange={(e) => setName(e.target.value)} placeholder="Rafael Costa" />
        </Field>
        <Field label="Email" hint="An email or a phone number, so they can get their card back.">
          <Input type="email" value={email} autoComplete="off" onChange={(e) => setEmail(e.target.value)} placeholder="name@example.com" />
        </Field>
        <Field label="Phone">
          <Input type="tel" value={phone} autoComplete="off" onChange={(e) => setPhone(e.target.value)} placeholder="07…" />
        </Field>
        <div>
          <div className="mb-1 text-xs font-bold text-ink-2">Birthday (optional, no year)</div>
          <div className="grid grid-cols-[5rem_1fr] gap-2">
            <Input
              aria-label="Birthday day"
              inputMode="numeric"
              value={day}
              maxLength={2}
              placeholder="Day"
              onChange={(e) => setDay(e.target.value.replace(/\D/g, ''))}
            />
            <Select aria-label="Birthday month" value={month} onChange={(e) => setMonth(e.target.value)}>
              <option value="">Month</option>
              {MONTHS.map((m, i) => (
                <option key={m} value={String(i + 1)}>
                  {m}
                </option>
              ))}
            </Select>
          </div>
          {birthdayBad && <div className="mt-1 text-sm text-bad-ink">Give both the day and the month, or neither.</div>}
        </div>
        <Checkbox
          checked={optIn}
          onChange={setOptIn}
          label="They said yes to news and offers (at most 2 a month)"
        />
        <Checkbox
          checked={agreed}
          onChange={setAgreed}
          alert
          label="They agreed to the card’s terms"
        />
        <p className="text-sm text-ink-2">
          Only tick news and offers if they asked for it — the card works without it.
        </p>
        {error && (
          <p role="alert" className="text-sm text-bad-ink">
            {error}
          </p>
        )}
      </form>
    </Drawer>
  )
}
