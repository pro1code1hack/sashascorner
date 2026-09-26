/**
 * The frame every screen sits in: a grouped sidebar, a top bar, and the
 * password gate.
 *
 * The chrome follows finsepa.com: sidebar groups under small grey labels, the
 * active item a filled rounded rect rather than a tinted stripe, and a top bar
 * carrying search and square icon buttons. A horizontal strip of seven text
 * links gave no sense of where you were or what else existed -- which is how
 * the first build came to read as "two tabs".
 */
import { useEffect, useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import { LIVE, api, clearKey, getKey, setKey } from '../lib/api'
import { Button, IconButton, Kbd } from './ui'

export type ScreenName =
  | 'today'
  | 'stock'
  | 'orders'
  | 'composition'
  | 'margin'
  | 'channels'
  | 'money'
  | 'proposals'

type Item = { key: ScreenName; label: string; hint: string; icon: string }

const GROUPS: { title: string; items: Item[] }[] = [
  {
    title: 'Daily',
    items: [
      { key: 'today', label: 'Today', hint: 'Is today normal?', icon: '\u25f7' },
      { key: 'stock', label: 'Stock', hint: 'On-hand, batches, drift', icon: '\u25e7' },
      { key: 'orders', label: 'Orders', hint: 'Drafts by supplier', icon: '\u21c4' },
    ],
  },
  {
    title: 'Decide',
    items: [
      { key: 'composition', label: 'Composition', hint: 'Recipes and costs', icon: '\u2b21' },
      { key: 'margin', label: 'Menu margin', hint: 'What actually earns', icon: '\u25d1' },
      { key: 'channels', label: 'Channels', hint: 'Deliveroo, Just Eat', icon: '\u25ce' },
      { key: 'money', label: 'Money & P&L', hint: 'Where it went', icon: '\u00a3' },
    ],
  },
  {
    // Spec 6: pattern detection proposes, a human confirms. 27 proposals cover 314
    // legacy rows and nothing is written until somebody says so, which is why this
    // is a screen rather than a migration script.
    title: 'Set up',
    items: [
      {
        key: 'proposals',
        label: 'Import review',
        hint: 'Confirm detected templates',
        icon: '\u2713',
      },
    ],
  },
]

const ALL: Item[] = GROUPS.flatMap((g) => g.items)


/**
 * What needs acting on, visible from every screen.
 *
 * The reference carries a portfolio value here; the equivalent for this product
 * is the one thing it promises -- that nobody has to remember what is running
 * out. `act` alerts are the backend's own severity, not a count this invents.
 *
 * It fails silent. A broken chip must never be the reason a screen looks broken,
 * so an error or a still-loading query renders nothing at all rather than a zero
 * (which would read as "all clear" -- the flattering kind of wrong).
 */
function ActionChip({ onGo }: { onGo: () => void }) {
  const today = useQuery({ queryKey: ['today'], queryFn: api.today, retry: 1 })
  if (today.isError || today.data === undefined) return null
  const act = today.data.alerts.filter((a) => a.severity === 'act').length
  const watch = today.data.alerts.filter((a) => a.severity === 'watch').length
  if (act === 0 && watch === 0) {
    return (
      <button
        type="button"
        onClick={onGo}
        className="hidden sm:inline-flex items-center gap-2 rounded-control border border-line bg-surface px-3 py-1.5 text-[0.75rem] text-ink-4 hover:border-line-2 hover:text-ink-3 transition-colors"
      >
        <span className="h-1.5 w-1.5 rounded-pill bg-ok" aria-hidden="true" />
        Nothing to act on
      </button>
    )
  }
  return (
    <button
      type="button"
      onClick={onGo}
      className="inline-flex items-center gap-2 rounded-control border border-line bg-surface px-3 py-1.5 text-[0.75rem] hover:border-line-2 transition-colors"
    >
      <span
        className={`h-1.5 w-1.5 rounded-pill ${act > 0 ? 'bg-bad' : 'bg-warn'}`}
        aria-hidden="true"
      />
      <span className="text-ink">
        <span className="fig font-medium">{act > 0 ? act : watch}</span>{' '}
        {act > 0 ? 'to act on' : 'to watch'}
      </span>
      {act > 0 && watch > 0 && (
        <span className="fig text-ink-5">+{watch}</span>
      )}
    </button>
  )
}

export function Shell({
  screen,
  onScreen,
  children,
}: {
  screen: ScreenName
  onScreen: (s: ScreenName) => void
  children: React.ReactNode
}) {
  const [open, setOpen] = useState(false)
  const [jump, setJump] = useState(false)
  // Collapsed is a per-laptop preference, so it is remembered. Wrapped because a
  // private window throws on access rather than returning null.
  const [collapsed, setCollapsed] = useState(() => {
    try {
      return localStorage.getItem('cafeops.sidebar') === 'collapsed'
    } catch {
      return false
    }
  })
  const toggleCollapsed = () => {
    setCollapsed((v) => {
      try {
        localStorage.setItem('cafeops.sidebar', v ? 'open' : 'collapsed')
      } catch {
        /* private mode; the choice lasts this page only */
      }
      return !v
    })
  }
  const current = ALL.find((i) => i.key === screen)

  /* `s` opens the screen switcher, as the reference's search field advertises.
     Ignored while typing, so it cannot eat a keystroke in the recipe editor. */
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      const t = e.target as HTMLElement | null
      const typing =
        t && (t.tagName === 'INPUT' || t.tagName === 'TEXTAREA' || t.isContentEditable)
      if (typing) return
      if (e.key === 's' && !e.metaKey && !e.ctrlKey && !e.altKey) {
        e.preventDefault()
        setJump((v) => !v)
      }
      if (e.key === 'Escape') setJump(false)
    }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [])

  return (
    <div
      className={`min-h-screen lg:grid ${
        collapsed ? 'lg:grid-cols-[60px_minmax(0,1fr)]' : 'lg:grid-cols-[236px_minmax(0,1fr)]'
      }`}
    >
      <aside
        className={`bg-surface border-r border-line flex flex-col lg:sticky lg:top-0 lg:h-screen ${
          open ? 'block' : 'hidden lg:flex'
        }`}
      >
        <div
          className={`flex items-center gap-2 py-4 ${collapsed ? 'justify-center px-2' : 'justify-between px-4'}`}
        >
          {!collapsed && (
            <div className="min-w-0">
              <div className="font-semibold text-[0.875rem] text-ink tracking-[-0.01em] truncate">
                Sasha&rsquo;s Corner
              </div>
              <div className="text-[0.6875rem] text-ink-5 mt-0.5 truncate">Dundee</div>
            </div>
          )}
          <button
            type="button"
            onClick={toggleCollapsed}
            aria-label={collapsed ? 'Expand the sidebar' : 'Collapse the sidebar'}
            title={collapsed ? 'Expand' : 'Collapse'}
            aria-expanded={!collapsed}
            className="hidden lg:inline-flex h-7 w-7 shrink-0 items-center justify-center rounded-control border border-line text-ink-5 hover:text-ink hover:border-line-2 transition-colors"
          >
            <span aria-hidden="true">{collapsed ? '\u00bb' : '\u00ab'}</span>
          </button>
        </div>

        <nav className="flex-1 overflow-y-auto px-3 pb-3">
          {GROUPS.map((g) => (
            <div key={g.title} className="mb-5">
              {collapsed ? (
                <div className="mx-2 mb-1.5 border-t border-line" aria-hidden="true" />
              ) : (
                <div className="px-2 pb-1.5 text-[0.6875rem] font-medium text-ink-5">{g.title}</div>
              )}
              {g.items.map((it) => {
                const on = it.key === screen
                return (
                  <button
                    key={it.key}
                    type="button"
                    onClick={() => {
                      onScreen(it.key)
                      setOpen(false)
                    }}
                    aria-current={on ? 'page' : undefined}
                    title={collapsed ? `${it.label} — ${it.hint}` : undefined}
                    className={`group flex w-full items-center gap-2 rounded-control py-[7px] text-left transition-colors ${
                      collapsed ? 'justify-center px-0' : 'px-2'
                    } ${
                      on ? 'bg-raised text-ink' : 'text-ink-3 hover:bg-raised/60 hover:text-ink-2'
                    }`}
                  >
                    <span
                      className={`w-4 shrink-0 text-center text-[0.875rem] leading-none ${
                        on ? 'text-brand-2' : 'text-ink-5 group-hover:text-ink-4'
                      }`}
                      aria-hidden="true"
                    >
                      {it.icon}
                    </span>
                    {!collapsed && (
                      <span className="text-[0.875rem] font-medium truncate">{it.label}</span>
                    )}
                  </button>
                )
              })}
            </div>
          ))}
        </nav>

        {!collapsed && (
          <div className="px-4 py-3 border-t border-line">
            <KeyState />
          </div>
        )}
      </aside>

      <div className="min-w-0 flex flex-col">
        <header className="sticky top-0 z-20 flex items-center gap-2 border-b border-line bg-canvas/85 px-4 py-2.5 backdrop-blur sm:px-6">
          <Button variant="ghost" onClick={() => setOpen((v) => !v)}>
            <span className="lg:hidden">{open ? 'Close' : 'Menu'}</span>
            <span className="hidden lg:inline text-ink-5">{current?.hint}</span>
          </Button>

          <div className="flex-1" />

          <ActionChip onGo={() => onScreen('today')} />

          <button
            type="button"
            onClick={() => setJump(true)}
            className="hidden sm:flex items-center gap-2 rounded-control border border-line bg-surface px-3 py-1.5 text-[0.75rem] text-ink-5 hover:border-line-2 hover:text-ink-3 transition-colors"
          >
            <span aria-hidden="true">{'\u2315'}</span>
            <span>Jump to screen</span>
            <Kbd>s</Kbd>
          </button>

          <IconButton label="Reload this screen" onClick={() => location.reload()}>
            {'\u21bb'}
          </IconButton>
        </header>

        <main className="min-w-0 flex-1 px-4 py-6 sm:px-6 lg:px-10 lg:py-8 max-w-[1600px]">
          {children}
        </main>
      </div>

      {jump && (
        <div
          className="fixed inset-0 z-50 bg-black/60 px-4 pt-[12vh] backdrop-blur-sm"
          onClick={() => setJump(false)}
          role="presentation"
        >
          <div
            className="mx-auto w-full max-w-md overflow-hidden rounded-card-lg border border-line-2 bg-surface shadow-pop"
            onClick={(e) => e.stopPropagation()}
            role="dialog"
            aria-label="Jump to screen"
          >
            <div className="border-b border-line px-4 py-2.5 text-[0.6875rem] text-ink-5">
              Jump to screen
            </div>
            <div className="p-2">
              {ALL.map((it) => (
                <button
                  key={it.key}
                  type="button"
                  autoFocus={it.key === screen}
                  onClick={() => {
                    onScreen(it.key)
                    setJump(false)
                  }}
                  className={`flex w-full items-center gap-3 rounded-control px-3 py-2 text-left transition-colors ${
                    it.key === screen ? 'bg-raised text-ink' : 'text-ink-2 hover:bg-raised/60'
                  }`}
                >
                  <span className="w-4 text-center text-ink-5" aria-hidden="true">
                    {it.icon}
                  </span>
                  <span className="text-[0.875rem] font-medium">{it.label}</span>
                  <span className="ml-auto text-[0.6875rem] text-ink-5">{it.hint}</span>
                </button>
              ))}
            </div>
          </div>
        </div>
      )}
    </div>
  )
}

export function KeyState() {
  const [, force] = useState(0)
  if (!LIVE) {
    return (
      <p className="text-[0.6875rem] text-ink-4 leading-[16px]">
        Fixture data. Screens render from the recorded API responses in{' '}
        <code>web/fixtures</code>.
      </p>
    )
  }
  return (
    <div className="flex items-center justify-between gap-2">
      <span className="text-[0.6875rem] text-ink-4">Live API</span>
      <button
        type="button"
        className="text-[0.6875rem] text-ink-3 underline underline-offset-2 hover:text-ink"
        onClick={() => {
          clearKey()
          force((v) => v + 1)
          location.reload()
        }}
      >
        sign out
      </button>
    </div>
  )
}

/** The single shared password (spec §10: no user management). */
export function Unlock({ onDone }: { onDone: () => void }) {
  const [value, setValue] = useState('')
  const [err, setErr] = useState<string | null>(null)
  return (
    <div className="min-h-screen grid place-items-center px-5">
      <form
        className="w-full max-w-sm bg-surface border border-line rounded-card shadow-pop p-6"
        onSubmit={async (e) => {
          e.preventDefault()
          setErr(null)
          setKey(value)
          try {
            const r = await fetch('/api/meta', { headers: { 'X-API-Key': value } })
            if (!r.ok) throw new Error(r.status === 401 ? 'Wrong password.' : `HTTP ${r.status}`)
            onDone()
          } catch (e) {
            clearKey()
            setErr(e instanceof Error ? e.message : String(e))
          }
        }}
      >
        <h1 className="font-semibold text-[1.25rem]">Sasha&rsquo;s Corner</h1>
        <p className="text-[0.875rem] text-ink-3 mt-1 mb-5">
          One shared password. There are no user accounts.
        </p>
        <input
          type="password"
          autoFocus
          value={value}
          onChange={(e) => setValue(e.target.value)}
          placeholder="Password"
          className="w-full rounded-control bg-raised border border-line-2 px-3 py-2 text-[0.875rem] outline-none focus:border-brand"
        />
        {err && <p className="text-[0.75rem] text-bad mt-2">{err}</p>}
        <div className="mt-4">
          <Button type="submit" variant="primary">
            Open
          </Button>
        </div>
      </form>
    </div>
  )
}

export { getKey }

/* ---------------------------------------------------------------------- *
 * Compatibility shims for the two screens written against the previous
 * shell. They keep Composition and Stock compiling while they are restyled;
 * new screens should use PageHeader from ./ui directly.
 * ---------------------------------------------------------------------- */

export function Page({ children }: { children: React.ReactNode }) {
  return <>{children}</>
}

export function ScreenTitle({
  title,
  sub,
  children,
}: {
  title?: string
  sub?: React.ReactNode
  children?: React.ReactNode
}) {
  return (
    <div className="mb-6">
      {title && (
        <h1 className="text-[1.75rem] font-semibold tracking-[-0.02em] leading-[16px]">
          {title}
        </h1>
      )}
      {(sub || children) && <div className="text-ink-3 mt-1">{sub ?? children}</div>}
    </div>
  )
}
