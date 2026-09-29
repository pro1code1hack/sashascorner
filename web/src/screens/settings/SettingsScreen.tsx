/**
 * Settings (shell-agents.md §4.2–4.5, DECISIONS §3 and §5).
 *
 * Rows kept from the design: the shared password -- the one sign-in for the back
 * office and the website admin, which the site only reaches through here (owner,
 * 2026-09-28) -- (safe version: current
 * password required, ≥ 10 characters, every other device signed out, Telegram
 * notice) and Lightspeed status with Sync now. "Show the empty-install screen"
 * became "Setup checklist". Dropped: the bot-language select (the bot has no
 * English) and "Reset demo data" (it would erase the stock ledger from a web
 * button). Read-only lines for the Telegram bot, narration and staff rate.
 */
import { useState } from 'react'
import type { FormEvent, ReactNode } from 'react'
import { useQueryClient } from '@tanstack/react-query'
import { Button, ErrorBox, Field, Input, LinkButton, Loading, PageBody, PageHeader, StatusLine, cx } from '../../components/ui'
import { ago, gbp, stamp } from '../../lib/format'
import { useOperator } from '../../lib/operator'
import { href } from '../../lib/router'
import {
  SETTINGS_QUERY_KEY,
  changePassword,
  syncOutcomeText,
  useSettings,
  useShell,
  useSyncNow,
} from '../../lib/shell-api'
import type { PasswordChangeResponse, SettingsResponse } from '../../lib/types/shell'

function Row({ label, children }: { label: string; children: ReactNode }) {
  return (
    <>
      <div className="pt-2 text-md text-ink-2 first:pt-0 sm:pt-0">{label}</div>
      <div className="min-w-0 text-md">{children}</div>
    </>
  )
}

function PasswordRow({ data }: { data: SettingsResponse }) {
  const qc = useQueryClient()
  const [operator] = useOperator()
  const [current, setCurrent] = useState('')
  const [next, setNext] = useState('')
  const [pending, setPending] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [done, setDone] = useState<PasswordChangeResponse | null>(null)
  const min = data.auth.min_length
  const tooShort = next !== '' && next.length < min

  const submit = async (e: FormEvent) => {
    e.preventDefault()
    if (pending || current === '' || next.length < min) return
    setPending(true)
    setError(null)
    setDone(null)
    const r = await changePassword({ current_password: current, new_password: next, actor: operator })
    setPending(false)
    if (r.kind === 'ok') {
      setDone(r.data)
      setCurrent('')
      setNext('')
      void qc.invalidateQueries({ queryKey: SETTINGS_QUERY_KEY })
    } else {
      setError(r.message)
    }
  }

  const source =
    data.auth.source === 'database'
      ? `Set here${data.auth.set_by ? ` by ${data.auth.set_by}` : ''}${data.auth.set_at ? `, ${ago(data.auth.set_at)}` : ''}.`
      : data.auth.source === 'environment'
        ? 'The server’s starting password is in use; changing it here replaces it.'
        : 'No password is set on the server.'

  return (
    <form onSubmit={submit} className="flex flex-col gap-1.5">
      <p className="text-sm text-ink-2">
        The one password for the back office and the website admin. {source} Changing it signs every other device
        out.
      </p>
      {/* Visible labels: a placeholder disappears as soon as someone types. */}
      <div className="flex flex-wrap items-start gap-x-2 gap-y-2">
        <Field label="Current password" className="w-full sm:w-52">
          <Input
            type="password"
            autoComplete="current-password"
            value={current}
            onChange={(e) => {
              setCurrent(e.target.value)
              setError(null)
            }}
          />
        </Field>
        <Field
          label="New password"
          className="w-full sm:w-52"
          hint={tooShort ? undefined : `At least ${min} characters.`}
          error={tooShort ? `At least ${min} characters: ${next.length} so far.` : undefined}
        >
          <Input
            type="password"
            autoComplete="new-password"
            value={next}
            onChange={(e) => {
              setNext(e.target.value)
              setError(null)
              setDone(null)
            }}
          />
        </Field>
        <Button
          type="submit"
          variant="outline"
          className="sm:mt-[22px]"
          pending={pending}
          pendingLabel="Changing…"
          disabled={current === '' || next.length < min}
        >
          Change
        </Button>
      </div>
      <StatusLine
        outcome={
          error !== null
            ? { kind: 'error', text: error }
            : done !== null
              ? { kind: 'ok', text: `${done.message} Every other device is signed out. ${done.telegram.detail}` }
              : null
        }
      />
    </form>
  )
}

function LightspeedRow({ data }: { data: SettingsResponse }) {
  const shell = useShell()
  const [operator] = useOperator()
  const sync = useSyncNow()
  const ls = data.lightspeed
  const outcome = syncOutcomeText(sync.state)

  if (!ls.configured) {
    return (
      <div className="flex flex-col gap-1">
        <div>Not connected.</div>
        <div className="text-sm text-ink-2">
          Whoever runs the server connects the till there, not in a web form: the till's key is a long-lived
          credential.
        </div>
        {ls.env_vars.length > 0 && (
          <details className="text-sm text-ink-2">
            <summary className="cursor-pointer">What they need to set</summary>
            <span className="break-words">{ls.env_vars.join(', ')}</span>
          </details>
        )}
      </div>
    )
  }
  const lastOk = shell?.sync.last_ok_finished_at ?? ls.last_ok_finished_at
  const attempt = ls.last_attempt
  const failed = sync.state.kind === 'refused' || (sync.state.kind === 'done' && sync.state.run.status === 'FAILED')
  return (
    <div className="flex flex-col gap-1">
      <div className="flex flex-wrap items-center gap-2">
        <span>Connected · {lastOk ? `sales last came in ${ago(lastOk)}` : 'no sync has brought sales in yet'}</span>
        <Button
          variant="outline"
          size="sm"
          pending={sync.busy}
          pendingLabel="Syncing…"
          onClick={() => void sync.start(operator)}
        >
          Sync now
        </Button>
      </div>
      {/* Always mounted, so the sync outcome is announced when it arrives. */}
      <div className={cx('text-sm', failed ? 'text-bad-ink' : 'text-ink-2')} role="status" aria-live="polite">
        {outcome}
      </div>
      {outcome === null && (
        attempt !== null &&
        (attempt.status === 'FAILED' || attempt.status === 'PARTIAL') && (
          <div className={cx('text-sm', attempt.status === 'FAILED' ? 'text-bad-ink' : 'text-ink-2')}>
            Last attempt {attempt.status === 'FAILED' ? 'failed' : 'was partial'}
            {attempt.finished_at ? ` (${stamp(attempt.finished_at)})` : ''}: {attempt.detail ?? 'no reason recorded'}
          </div>
        )
      )}
    </div>
  )
}

function SettingsBody({ data }: { data: SettingsResponse }) {
  const shell = useShell()
  const open = shell?.setup.open_steps
  return (
    <div className="grid max-w-[820px] grid-cols-1 items-center gap-x-5 gap-y-1 sm:grid-cols-[220px_minmax(0,1fr)] sm:gap-y-4">
      <Row label="Shared password">
        <PasswordRow data={data} />
      </Row>
      <Row label="Lightspeed">
        <LightspeedRow data={data} />
      </Row>
      <Row label="Setup checklist">
        <div className="flex flex-wrap items-center gap-2">
          <LinkButton variant="outline" size="sm" href={href('/setup')}>
            Open the checklist
          </LinkButton>
          {open !== undefined && (
            <span className="text-sm text-ink-2">
              {open === 0 ? 'All five steps done.' : `${open} of 5 steps still open.`}
            </span>
          )}
        </div>
      </Row>
      <Row label="Telegram bot">
        <span>
          {data.telegram.bot_configured && data.telegram.owner_chat_configured
            ? 'Connected'
            : data.telegram.bot_configured
              ? 'Token set, but no owner chat, so nothing is sent'
              : 'No bot token, so nothing is sent'}
          <span className="text-ink-2"> · Russian. The bot has no English text yet.</span>
        </span>
      </Row>
      <Row label="Agent narration">
        <span>
          {data.agent.narration === 'model'
            ? `Claude (${data.agent.model ?? 'model'})`
            : 'Template sentences (no API key set)'}
        </span>
      </Row>
      <Row label="Staff time rate">
        <span className="fig">
          {data.labour.loaded_hourly_rate_pence === null
            ? 'Not set, so labour costs are left blank'
            : `${gbp(data.labour.loaded_hourly_rate_pence)}/hr loaded`}
        </span>
      </Row>
    </div>
  )
}

export function SettingsScreen() {
  const q = useSettings()
  return (
    <>
      <PageHeader title="Settings" />
      <PageBody flush>
        <div className="px-4 py-4 sm:px-5 compact:px-7 compact:py-5">
          {q.isPending ? (
            <Loading what="Loading settings" />
          ) : q.isError ? (
            <ErrorBox error={q.error} what="settings" />
          ) : (
            <SettingsBody data={q.data} />
          )}
        </div>
      </PageBody>
    </>
  )
}
