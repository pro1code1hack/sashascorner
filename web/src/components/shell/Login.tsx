/**
 * The login screen, as designed (shell-agents.md §4.1), minus "(Demo: corner)".
 *
 * Verifies against an AUTHENTICATED endpoint through `signIn()` (lib/api):
 * the pre-v2 Unlock checked the open `/api/meta` and let any password in.
 */
import { useId, useState } from 'react'
import { signIn, type SignInOutcome } from '../../lib/api'
import { Button, PasswordInput } from '../ui'

const MESSAGES: Record<Exclude<SignInOutcome['kind'], 'ok' | 'failed'>, string> = {
  wrong: 'That’s not it. Ask whoever runs the café.',
  no_password_set: 'The back office has no password set yet. Set CAFEOPS_API_PASSWORD on the server.',
  rate_limited: 'Too many tries. Wait a minute.',
}

export function Login({ notice, onSignedIn }: { notice?: string | null; onSignedIn: () => void }) {
  const [pw, setPw] = useState('')
  const [err, setErr] = useState<string | null>(notice ?? null)
  const [pending, setPending] = useState(false)
  const id = useId()
  const errId = `${id}-err`

  return (
    <main className="grid min-h-dvh place-items-center bg-canvas px-4">
      <form
        className="w-[min(444px,100%)] rounded-login bg-surface px-8 pb-7 pt-9 shadow-login max-[400px]:px-6"
        onSubmit={async (e) => {
          e.preventDefault()
          if (pw === '' || pending) return
          setPending(true)
          setErr(null)
          const r = await signIn(pw)
          setPending(false)
          if (r.kind === 'ok') {
            setPw('')
            onSignedIn()
          } else {
            setErr(r.kind === 'failed' ? r.message : MESSAGES[r.kind])
          }
        }}
      >
        <div
          className="mb-4.5 grid size-11 place-items-center rounded-[14px] bg-brand text-xl font-extrabold text-white"
          aria-hidden="true"
        >
          S
        </div>
        <h1 className="text-3xl font-extrabold tracking-[-.01em]">Sasha&rsquo;s Corner</h1>
        <p className="mb-4.5 text-md text-ink-2">Back office · 23 Commercial Street</p>
        <label htmlFor={id} className="mb-1.5 block text-base">
          Password
        </label>
        <PasswordInput
          id={id}
          name="password"
          autoComplete="current-password"
          autoFocus
          placeholder="shared password"
          value={pw}
          onChange={(e) => {
            setPw(e.target.value)
            setErr(null)
          }}
          aria-invalid={err !== null || undefined}
          aria-describedby={errId}
        />
        {/* Design: alert #d4554a at 14px regular; bad-ink keeps it above 4.5:1. */}
        <div id={errId} role="alert" className="my-1.5 min-h-6 text-base text-bad-ink">
          {err}
        </div>
        <Button type="submit" variant="primary" size="lg" block pending={pending} pendingLabel="Checking…">
          Open
        </Button>
        <p className="mt-3.5 text-sm text-ink-2">One password for everyone on the team. Change it in Settings.</p>
      </form>
    </main>
  )
}
