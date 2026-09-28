/**
 * The Loyalty card header: a title (or, on a member's page, "‹ Members / Name #id")
 * and the grey tab strip Members · Insights · Messages · Programme. The Members tab
 * carries the number of members with a free drink waiting.
 */
import type { ReactNode } from 'react'
import { cx } from '../../components/ui'
import { useRewardReadyCount } from '../../lib/loyalty-api'
import { href } from '../../lib/router'

export type LoyaltyTab = 'members' | 'insights' | 'messages' | 'programme'

const TABS: { id: LoyaltyTab; label: string; path: string }[] = [
  { id: 'members', label: 'Members', path: '/loyalty' },
  { id: 'insights', label: 'Insights', path: '/loyalty/insights' },
  { id: 'messages', label: 'Messages', path: '/loyalty/messages' },
  { id: 'programme', label: 'Programme', path: '/loyalty/programme' },
]

export function LoyaltyHeader({
  current,
  crumb,
  actions,
}: {
  current: LoyaltyTab
  /** On a member's page: the member's name and number, shown after "‹ Members /". */
  crumb?: { name: string; id: number }
  actions?: ReactNode
}) {
  const ready = useRewardReadyCount()
  return (
    <header className="flex flex-none flex-wrap items-center gap-x-5 gap-y-2 border-b border-line px-4 py-3 sm:px-5">
      {crumb ? (
        <div className="flex min-w-0 items-baseline gap-2">
          <a href={href('/loyalty')} className="flex-none text-base font-semibold text-brand-ink no-underline hover:underline">
            ‹ Members
          </a>
          <span className="text-ink-3" aria-hidden="true">/</span>
          <h1 className="min-w-0 truncate text-2xl font-extrabold tracking-[-.01em]">{crumb.name}</h1>
          <span className="fig flex-none text-base text-ink-3">#{crumb.id}</span>
        </div>
      ) : (
        <h1 className="text-2xl font-extrabold tracking-[-.01em]">Loyalty card</h1>
      )}
      <nav aria-label="Loyalty card sections" className="inline-flex max-w-full items-center gap-0.5 overflow-x-auto rounded-button bg-wash p-[3px]">
        {TABS.map((t) => {
          const on = t.id === current
          return (
            <a
              key={t.id}
              href={href(t.path)}
              aria-current={on ? 'page' : undefined}
              className={cx(
                'inline-flex h-[34px] flex-none items-center gap-1.5 whitespace-nowrap rounded-[9px] px-3 text-base no-underline transition-colors sm:px-3.5',
                on ? 'bg-surface font-bold text-brand-ink shadow-seg' : 'font-semibold text-ink-2 hover:text-ink',
              )}
            >
              {t.label}
              {t.id === 'members' && ready !== null && ready > 0 && (
                <span
                  className="fig rounded-full bg-brand px-[6px] py-px text-label font-bold leading-[14px] text-white"
                  aria-label={`${ready} with a free drink waiting`}
                  title={`${ready} with a free drink waiting`}
                >
                  {ready}
                </span>
              )}
            </a>
          )
        })}
      </nav>
      {actions && <div className="ml-auto flex flex-none items-center gap-2">{actions}</div>}
    </header>
  )
}
