/**
 * The board's sound controls (owner, 2026-09-29): a speaker button in the page
 * header that says "Sound on" / "Sound off" in words, "Snooze 5 min" while
 * orders are ringing, and the bar that offers to turn sound on the first time
 * (audio needs a click before a browser will play it).
 */
import { Button, cx } from '../../components/ui'
import type { ShopAlert } from './alert'

function Speaker({ off }: { off: boolean }) {
  return (
    <svg aria-hidden="true" width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
      <path d="M11 5 6 9H2v6h4l5 4z" />
      {off ? (
        <>
          <line x1="23" y1="9" x2="17" y2="15" />
          <line x1="17" y1="9" x2="23" y2="15" />
        </>
      ) : (
        <>
          <path d="M15.5 8.5a5 5 0 0 1 0 7" />
          <path d="M19 5a9 9 0 0 1 0 14" />
        </>
      )}
    </svg>
  )
}

/** For the PageHeader's `actions`. */
export function SoundToggle({ alert }: { alert: ShopAlert }) {
  const snoozed = alert.snoozedUntil !== null && alert.snoozedUntil > Date.now()
  return (
    <span className="flex flex-wrap items-center gap-2">
      {alert.soundOn && alert.newCount > 0 && (
        <Button size="sm" disabled={snoozed} onClick={alert.snooze} title="Stop the repeat for five minutes without accepting anything">
          {snoozed ? 'Snoozed' : 'Snooze 5 min'}
        </Button>
      )}
      <button
        type="button"
        aria-pressed={alert.soundOn}
        onClick={() => (alert.soundOn ? alert.turnOff() : void alert.turnOn())}
        title={alert.soundOn ? 'Turn the new-order sound off' : 'Ring when an order arrives'}
        className={cx(
          'inline-flex h-10 items-center gap-1.5 rounded-control border px-3 text-base font-semibold',
          alert.soundOn ? 'border-brand-line bg-brand-wash text-brand-ink' : 'border-line-control bg-surface text-ink hover:bg-canvas',
        )}
      >
        <Speaker off={!alert.soundOn} />
        {alert.soundOn ? (alert.armed ? 'Sound on' : 'Sound on · click to arm') : 'Sound off'}
      </button>
    </span>
  )
}

/** The bar under the header while sound is off: one click turns it on. */
export function SoundBar({ alert }: { alert: ShopAlert }) {
  if (alert.soundOn) return null
  return (
    <div role="status" className="flex flex-none flex-wrap items-center gap-x-3 gap-y-1 border-b border-line-soft bg-canvas-2 px-5 py-2 text-sm text-ink-2">
      <span>Sound alerts are off. The board still updates every 15 seconds; turn sound on to hear a chime when an order arrives.</span>
      <Button size="sm" onClick={() => void alert.turnOn()}>
        Turn on
      </Button>
    </div>
  )
}
