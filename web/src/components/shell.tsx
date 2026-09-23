import { useState } from 'react'
import { Fig, Label } from './prim'
import { LIVE, clearKey, getKey, setKey } from '../lib/api'

export type ScreenName = 'composition' | 'stock'

const SCREENS: { key: ScreenName; label: string }[] = [
  { key: 'composition', label: 'Composition' },
  { key: 'stock', label: 'Stock' },
]

export function Masthead({
  screen,
  onScreen,
  asOf,
}: {
  screen: ScreenName
  onScreen: (s: ScreenName) => void
  asOf?: string
}) {
  return (
    <header className="rule-b border-rule-strong mb-6">
      <div className="mx-auto flex max-w-[1500px] flex-wrap items-end justify-between gap-x-6 gap-y-1 px-5 pt-5 pb-1 sm:px-8">
        <h1
          className="text-[length:var(--text-title)] leading-none font-[600] tracking-[-0.015em]"
          style={{ fontVariationSettings: "'opsz' 32" }}
        >
          Sasha&rsquo;s Corner
        </h1>
        <Label className="pb-[3px]">
          23 Commercial Street, Dundee
          {asOf ? <> · as of {asOf}</> : null}
          {!LIVE && <> · fixture data</>}
        </Label>
      </div>
      <nav className="mx-auto flex max-w-[1500px] items-baseline gap-5 px-5 pb-2 sm:px-8">
        {SCREENS.map((s) => (
          <button
            key={s.key}
            type="button"
            onClick={() => onScreen(s.key)}
            className={
              screen === s.key
                ? 'border-ink border-b-2 pb-[1px] text-[length:var(--text-prose)] font-[600]'
                : 'text-muted hover:text-ink border-b-2 border-transparent pb-[1px] text-[length:var(--text-prose)]'
            }
            aria-current={screen === s.key ? 'page' : undefined}
          >
            {s.label}
          </button>
        ))}
        <Label className="ml-auto hidden sm:inline">
          orders · margin · channels — not built yet
        </Label>
      </nav>
    </header>
  )
}

export function Page({ children }: { children: React.ReactNode }) {
  return <main className="mx-auto max-w-[1500px] px-5 pb-24 sm:px-8">{children}</main>
}

export function ScreenTitle({
  title,
  sub,
}: {
  title: string
  sub?: React.ReactNode
}) {
  return (
    <div className="mb-4">
      <h2
        className="text-[length:var(--text-title)] leading-tight font-[500]"
        style={{ fontVariationSettings: "'opsz' 24" }}
      >
        {title}
      </h2>
      {sub ? <div className="text-muted mt-1">{sub}</div> : null}
    </div>
  )
}

/** Auth is one shared password sent as X-API-Key. No user management. Only
 *  reached in live mode; fixture mode has nothing to unlock. */
export function Unlock({ onDone }: { onDone: () => void }) {
  const [value, setValue] = useState('')
  return (
    <form
      className="mx-auto mt-24 max-w-[30rem] px-5"
      onSubmit={(e) => {
        e.preventDefault()
        if (value.trim() !== '') {
          setKey(value.trim())
          onDone()
        }
      }}
    >
      <h1
        className="text-[length:var(--text-title)] font-[600]"
        style={{ fontVariationSettings: "'opsz' 32" }}
      >
        Sasha&rsquo;s Corner
      </h1>
      <p className="text-muted mt-2 mb-4 leading-relaxed">
        One shared password. It is held for this browser tab only and is sent as an
        <Fig size="sub"> X-API-Key</Fig> header.
      </p>
      <input
        type="password"
        autoFocus
        value={value}
        onChange={(e) => setValue(e.target.value)}
        className="fig bg-slab border-rule-strong w-full border px-3 py-2"
        aria-label="Shared password"
      />
      <button
        type="submit"
        className="border-ink bg-ink mt-3 border px-4 py-[6px] text-paper"
      >
        Unlock
      </button>
    </form>
  )
}

export function KeyState() {
  if (!LIVE) return null
  return (
    <button
      type="button"
      className="text-muted hover:text-ink fixed right-3 bottom-3 text-[length:var(--text-micro)]"
      onClick={() => {
        clearKey()
        location.reload()
      }}
    >
      {getKey() ? 'forget password' : ''}
    </button>
  )
}
