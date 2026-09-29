/**
 * Shared bits for the Website group: the public site's admin, moved into the
 * back office (owner, 2026-09-28). The site API owns the data; the back office
 * forwards to it (lib/website-api.ts), so every screen first checks the two are
 * connected and says plainly what to fix when they are not.
 */
import type { ReactNode } from 'react'
import { Empty, Loading, WarnBox } from '../../components/ui'
import { LIVE } from '../../lib/api'
import { useWebsiteConnection } from '../../lib/website-api'

/** Renders `children` only when the site API can be reached through the back office. */
export function WebsiteGate({ children }: { children: ReactNode }) {
  const conn = useWebsiteConnection()
  if (!LIVE) {
    return (
      <Empty roomy>
        <p>This back office is showing recorded data, so the website&rsquo;s bookings, messages and photos are not here.</p>
        <p className="mt-2 text-sm text-ink-2">Ask whoever set this up to run it against the live site (VITE_LIVE=1).</p>
      </Empty>
    )
  }
  if (conn.isPending) return <Loading what="Connecting to the website" />
  const c = conn.data
  if (conn.isError || c === undefined) {
    return (
      <div className="p-5">
        <WarnBox>Couldn&rsquo;t ask the back office whether the website is connected. Reload to try again.</WarnBox>
      </div>
    )
  }
  if (!c.configured) {
    return (
      <div className="p-5">
        <WarnBox>
          <div className="font-bold">The website isn&rsquo;t connected to the back office yet.</div>
          <div>Ask whoever set this up to connect the website. Bookings, messages and photos appear here once it is.</div>
          <div className="mt-1 text-sm text-ink-2">
            For them: set <code>SITE_SERVICE_KEY</code> in <code>.env</code> to one long random value (both apps read it),
            then restart the back office and the website.
          </div>
        </WarnBox>
      </div>
    )
  }
  if (!c.reachable) {
    return (
      <div className="p-5">
        <WarnBox>
          <div className="font-bold">The website&rsquo;s server isn&rsquo;t answering.</div>
          <div className="text-ink-2">Bookings, messages and photos come from it. Start it, then reload this page.</div>
        </WarnBox>
      </div>
    )
  }
  return <>{children}</>
}
