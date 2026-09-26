/**
 * The Setup checklist ("Nothing here yet.", shell-agents.md §5).
 *
 * Five steps from `GET /api/setup`, each derived from the doctor's checks.
 * The first step that is not done is highlighted; done steps show a ✓ and an
 * outline CTA. Anything that has no web path (the workbook import, connecting
 * Lightspeed) shows the command to run instead of pretending to be a button.
 * The doctor's other findings follow as "Other things the system noticed".
 */
import { useState } from 'react'
import { Button, Card, ErrorBox, Loading, PageBody, cx } from '../../components/ui'
import { goToRoute, useSetup } from '../../lib/shell-api'
import type { SetupResponse, SetupStep, SetupWarning } from '../../lib/types/shell'

function CopyBlock({ text }: { text: string }) {
  const [copied, setCopied] = useState(false)
  return (
    <div className="mt-2 flex min-w-0 items-center gap-2">
      <code className="min-w-0 flex-1 overflow-x-auto whitespace-nowrap rounded-control bg-canvas px-2.5 py-1.5 font-mono text-[13px] text-ink">
        {text}
      </code>
      <Button
        variant="ghost"
        size="sm"
        onClick={async () => {
          try {
            await navigator.clipboard.writeText(text)
            setCopied(true)
            setTimeout(() => setCopied(false), 2000)
          } catch {
            /* no clipboard permission: the text is selectable */
          }
        }}
      >
        {copied ? 'Copied' : 'Copy'}
      </Button>
    </div>
  )
}

function Step({ step, highlight }: { step: SetupStep; highlight: boolean }) {
  return (
    <Card className="flex flex-col gap-3 sm:flex-row sm:items-start sm:gap-3.5">
      <div className="flex min-w-0 flex-1 gap-3.5">
        <div
          className={cx(
            'grid size-[30px] flex-none place-items-center rounded-full border text-base font-bold',
            step.done ? 'border-ok-ink bg-ok-wash text-ok-ink' : 'border-line text-ink',
          )}
        >
          {step.done ? <span aria-hidden="true">✓</span> : step.n}
          <span className="sr-only">{step.done ? `Step ${step.n}, done` : `Step ${step.n}, not done`}</span>
        </div>
        <div className="min-w-0 flex-1">
          <h2 className="text-lg font-normal">{step.title}</h2>
          <p className="text-base text-ink-2">{step.body}</p>
          {!step.done && step.cli_fix && <CopyBlock text={step.cli_fix} />}
        </div>
      </div>
      {step.cta && (
        <div className="flex-none sm:pt-0.5">
          <Button variant={highlight ? 'primary' : 'outline'} size="sm" onClick={() => goToRoute(step.cta!.route)}>
            {step.cta.label}
          </Button>
        </div>
      )}
    </Card>
  )
}

function Warnings({ warnings }: { warnings: SetupWarning[] }) {
  if (warnings.length === 0) return null
  return (
    <section className="mt-8 max-w-[720px]" aria-labelledby="setup-noticed">
      <h2 id="setup-noticed" className="mb-2 text-lg font-extrabold tracking-[-.01em]">
        Other things the system noticed
      </h2>
      <ul className="flex flex-col">
        {warnings.map((w) => (
          <li key={`${w.name}-${w.detail.slice(0, 20)}`} className="border-b border-line py-2.5 text-base">
            <div className="flex flex-wrap items-baseline gap-x-2">
              <span className={cx('text-sm font-bold', w.severity === 'FAIL' ? 'text-bad-ink' : 'text-ink-2')}>
                {w.severity === 'FAIL' ? 'Broken' : w.severity === 'WARN' ? 'Worth fixing' : 'For information'}
              </span>
              <span className="font-bold">{w.name}</span>
            </div>
            <p className="text-ink-2">{w.detail}.</p>
            {w.fix && <p className="mt-0.5 text-sm text-ink-2">What to do: {w.fix}</p>}
          </li>
        ))}
      </ul>
    </section>
  )
}

function SetupBody({ data }: { data: SetupResponse }) {
  const firstOpen = data.steps.find((s) => !s.done)?.n ?? null
  return (
    <>
      <h1 className="text-4xl font-extrabold tracking-[-.02em]">
        {data.empty_install ? 'Nothing here yet.' : data.open_steps === 0 ? 'All set up.' : 'Setup checklist'}
      </h1>
      <p className="mb-6 mt-1.5 max-w-[640px] text-lg text-ink-2">
        {data.open_steps === 0
          ? 'All five are done. The numbers rest on real inputs now; the notes below are what is still worth a look.'
          : 'Five things before the numbers mean anything. Do them in order; each one makes the next more accurate.'}
      </p>
      <ol className="flex max-w-[720px] flex-col gap-2.5">
        {data.steps.map((s) => (
          <li key={s.key}>
            <Step step={s} highlight={s.n === firstOpen} />
          </li>
        ))}
      </ol>
      <Warnings warnings={data.warnings} />
    </>
  )
}

export function SetupScreen() {
  const q = useSetup()
  return (
    <PageBody flush>
      <div className="px-4 py-6 sm:px-11 sm:py-9">
        {q.isPending ? (
          <Loading what="Checking the install" />
        ) : q.isError ? (
          <ErrorBox error={q.error} what="the setup checklist" />
        ) : (
          <SetupBody data={q.data} />
        )}
      </div>
    </PageBody>
  )
}
