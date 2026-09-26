/**
 * The frame (shell-agents.md §2): sidebar, banner stack, one page.
 *
 *   >= 1280px  sidebar 232px, docked
 *   900–1279   sidebar 200px, docked (the design's `compact` frame)
 *   < 900px    56px top bar + off-canvas 280px sidebar over a scrim
 *
 * Root is `flex h-dvh overflow-hidden`; each page owns its own scroll, so the
 * page itself can never scroll sideways.
 */
import { useEffect, useRef, useState } from 'react'
import type { ReactNode } from 'react'
import { LIVE, signOut } from '../../lib/api'
import { ago } from '../../lib/format'
import { useIsDocked } from '../../lib/media'
import { navigate } from '../../lib/router'
import { useOperator } from '../../lib/operator'
import {
  goToRoute,
  readDismissed,
  syncOutcomeText,
  useShell,
  useSyncNow,
  writeDismissed,
  type ShellBanner,
  type ShellResponse,
} from '../../lib/shell-api'
import { NAV_GROUPS, ROUTES, type RouteDef, type RouteId } from '../../routes'
import { Banner, BannerStack, CountBadge, cx, IconButton, useFocusTrap } from '../ui'
import { OperatorControl } from './Operator'

function syncLine(shell: ShellResponse | undefined): string | null {
  if (!LIVE) return 'fixture data, nothing is live'
  if (shell === undefined) return null
  if (!shell.sync.lightspeed_configured) return 'Lightspeed not connected'
  if (shell.sync.last_ok_finished_at === null) return 'sales not synced yet'
  return `sales synced ${ago(shell.sync.last_ok_finished_at)}`
}

function badgeFor(r: RouteDef, shell: ShellResponse | undefined): number | undefined {
  if (!r.badge || shell === undefined) return undefined
  return shell.badges[r.badge]
}

function BrandTile({ size }: { size: 'sm' | 'md' }) {
  return (
    <div
      aria-hidden="true"
      className={cx(
        'grid flex-none place-items-center bg-brand font-extrabold text-white',
        size === 'md' ? 'size-[34px] rounded-[11px] text-lg' : 'size-8 rounded-[10px] text-base',
      )}
    >
      S
    </div>
  )
}

function NavItem({
  route,
  active,
  count,
  onNavigate,
}: {
  route: RouteDef
  active: boolean
  count?: number
  onNavigate?: () => void
}) {
  return (
    <a
      href={`#${route.path}`}
      onClick={onNavigate}
      aria-current={active ? 'page' : undefined}
      className={cx(
        // 40px rows as designed; 36px on short screens (iPad landscape, 820px) so the
        // operator line (DECISIONS §6, not in the design) still leaves Sign out in view.
        'flex min-h-10 flex-none items-center justify-between gap-2 rounded-button px-2.5 text-md no-underline transition-[background-color] [@media(max-height:880px)]:min-h-9',
        active
          ? 'bg-surface font-bold text-brand-ink shadow-raised hover:text-brand-ink'
          : 'font-medium text-ink hover:bg-white/60 hover:text-ink',
      )}
    >
      <span className="min-w-0 truncate">{route.label}</span>
      <CountBadge count={count} label="waiting" />
    </a>
  )
}

function Sidebar({
  current,
  shell,
  onNavigate,
  className,
}: {
  current: RouteId | null
  shell: ShellResponse | undefined
  onNavigate?: () => void
  className?: string
}) {
  const line = syncLine(shell)
  return (
    <div
      className={cx(
        'no-scrollbar flex flex-col gap-0.5 overflow-y-auto border-r border-line-soft bg-canvas px-2.5 pb-3.5 pt-4.5',
        className,
      )}
    >
      <div className="flex flex-none items-center gap-2.5 px-2 pb-3.5">
        <BrandTile size="md" />
        <div className="min-w-0">
          <div className="text-lg font-extrabold leading-[1.15] tracking-[-.01em]">Sasha&rsquo;s Corner</div>
          {line !== null && <div className="truncate text-xs text-ink-2">{line}</div>}
        </div>
      </div>

      <nav aria-label="Main" className="flex flex-col gap-0.5">
        {NAV_GROUPS.map((g) => (
          <div key={g.head} className="flex flex-col gap-0.5" role="group" aria-labelledby={`nav-${g.head}`}>
            <div
              id={`nav-${g.head}`}
              className="px-2.5 pb-1.5 pt-4 text-label font-bold uppercase tracking-[.08em] text-ink-3"
            >
              {g.head}
            </div>
            {g.items.map((id) => (
              <NavItem
                key={id}
                route={ROUTES[id]}
                active={current === id}
                count={badgeFor(ROUTES[id], shell)}
                onNavigate={onNavigate}
              />
            ))}
          </div>
        ))}
      </nav>

      <div className="min-h-2 flex-1" />

      <OperatorControl />
      <NavItem route={ROUTES.settings} active={current === 'settings'} onNavigate={onNavigate} />
      {LIVE && (
        <button
          type="button"
          onClick={() => void signOut()}
          className="flex min-h-9 flex-none items-center rounded-button px-2.5 text-left text-base text-ink-3 hover:text-ink-2"
        >
          Sign out
        </button>
      )}
    </div>
  )
}

function Banners({ shell, path }: { shell: ShellResponse | undefined; path: string | null }) {
  const [dismissed, setDismissed] = useState<Set<string>>(readDismissed)
  const [operator] = useOperator()
  const sync = useSyncNow()
  const outcome = syncOutcomeText(sync.state)

  const dismiss = (key: string) => {
    const next = new Set(dismissed)
    next.add(key)
    writeDismissed(next)
    setDismissed(next)
  }

  // A banner whose only action is "go to the page you are on" is noise there.
  const here = (b: ShellBanner) =>
    b.action?.kind === 'navigate' && path !== null && b.action.route?.replace(/^#/, '').split('?')[0] === path
  const visible = (shell?.banners ?? []).filter((b) => !dismissed.has(b.instance_key) && !here(b))
  if (LIVE && visible.length === 0 && outcome === null) return null

  return (
    <BannerStack>
      {!LIVE && <Banner tone="stale">Fixture data: recorded responses, nothing is live.</Banner>}
      {visible.map((b) => {
        const action = b.action
        return (
          <Banner
            key={b.instance_key}
            tone={b.tone}
            onDismiss={() => dismiss(b.instance_key)}
            action={
              action === null
                ? undefined
                : action.kind === 'sync_now'
                  ? {
                      label: action.label,
                      pending: sync.busy,
                      pendingLabel: 'Syncing…',
                      onClick: () => void sync.start(operator),
                    }
                  : {
                      label: action.label,
                      onClick: () => {
                        if (action.route) goToRoute(action.route)
                      },
                    }
            }
          >
            {b.text}
          </Banner>
        )
      })}
      {outcome !== null && (
        <Banner
          tone={
            sync.state.kind === 'refused' || (sync.state.kind === 'done' && sync.state.run.status === 'FAILED')
              ? 'alert'
              : 'stale'
          }
          onDismiss={sync.reset}
        >
          {outcome}
        </Banner>
      )}
    </BannerStack>
  )
}

/**
 * An empty install (nothing imported yet) lands on the Setup checklist rather
 * than on a Stock screen with nothing in it (shell-agents §5). Once per page
 * load, and never away from Settings or Setup themselves.
 */
function useEmptyInstallRedirect(shell: ShellResponse | undefined, current: RouteId | null) {
  const done = useRef(false)
  useEffect(() => {
    if (done.current || shell === undefined) return
    done.current = true
    if (shell.setup.empty_install && current !== 'setup' && current !== 'settings') {
      navigate('/setup', { replace: true })
    }
  }, [shell, current])
}

function Hamburger({ open, onClick, count }: { open: boolean; onClick: () => void; count: number }) {
  return (
    <IconButton
      label={open ? 'Close menu' : 'Open menu'}
      size={40}
      aria-expanded={open}
      aria-controls="offcanvas-nav"
      onClick={onClick}
      className="text-ink"
    >
      <span aria-hidden="true" className="flex w-[18px] flex-col gap-[4px]">
        <span className="h-0.5 rounded-full bg-current" />
        <span className="h-0.5 rounded-full bg-current" />
        <span className="h-0.5 rounded-full bg-current" />
      </span>
      {count > 0 && (
        <span className="absolute -right-1 -top-1">
          <CountBadge count={count} label="waiting" />
        </span>
      )}
    </IconButton>
  )
}

export function Shell({ route, children }: { route: RouteDef | null; children: ReactNode }) {
  const shell = useShell()
  const docked = useIsDocked()
  const [open, setOpen] = useState(false)
  const panel = useRef<HTMLDivElement>(null)
  useFocusTrap(panel, open && !docked, () => setOpen(false))

  // Growing past 900px docks the sidebar; the off-canvas state must not linger.
  useEffect(() => {
    if (docked) setOpen(false)
  }, [docked])

  useEffect(() => {
    document.title = route ? `${route.label} · Sasha's Corner` : "Sasha's Corner"
  }, [route])

  const waiting = (shell?.badges.orders_waiting ?? 0) + (shell?.badges.proposals_waiting ?? 0)
  const current = route?.id ?? null
  useEmptyInstallRedirect(shell, current)

  return (
    <div className="flex h-dvh overflow-hidden bg-surface">
      <Sidebar current={current} shell={shell} className="hidden w-[200px] flex-none compact:flex wide:w-[232px]" />

      <div className="flex min-h-0 min-w-0 flex-1 flex-col">
        <div className="flex h-14 flex-none items-center gap-2.5 border-b border-line px-2 compact:hidden">
          <Hamburger open={open} onClick={() => setOpen((v) => !v)} count={waiting} />
          <BrandTile size="sm" />
          <div className="min-w-0 flex-1 truncate text-lg font-extrabold tracking-[-.01em]">
            {route?.label ?? 'Sasha’s Corner'}
          </div>
        </div>

        <Banners shell={shell} path={route?.path ?? null} />

        <main id="main" className="flex min-h-0 min-w-0 flex-1 flex-col">
          {children}
        </main>
      </div>

      {open && !docked && (
        <div className="fixed inset-0 z-50 compact:hidden">
          <div className="absolute inset-0 bg-scrim" onClick={() => setOpen(false)} aria-hidden="true" />
          <div
            ref={panel}
            id="offcanvas-nav"
            role="dialog"
            aria-modal="true"
            aria-label="Menu"
            className="absolute inset-y-0 left-0 flex w-[280px] max-w-[85vw] transition-transform duration-150 starting:-translate-x-full"
          >
            <Sidebar current={current} shell={shell} onNavigate={() => setOpen(false)} className="w-full" />
          </div>
        </div>
      )}
    </div>
  )
}
