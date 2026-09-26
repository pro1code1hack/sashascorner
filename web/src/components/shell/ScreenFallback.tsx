/**
 * What the page area shows while a screen's chunk loads, and if it fails to.
 * Quiet on purpose: nothing in this app is urgent (spec §10), so no spinner --
 * the same words-only `Loading` every screen uses for its own reads.
 */
import { Button, Loading } from '../ui'

export function ScreenLoading() {
  return (
    <div className="min-h-0 flex-1 overflow-y-auto">
      <Loading what="Opening" />
    </div>
  )
}

export function ScreenLoadFailed() {
  return (
    <div role="alert" className="m-5 flex flex-col items-start gap-3 rounded-card border border-line bg-alert-wash px-3.5 py-3 text-base">
      <div>
        <div className="font-bold">Couldn&rsquo;t open this page.</div>
        <div className="text-sm text-bad-ink">
          The back office was probably updated since this tab opened, or the connection dropped.
        </div>
      </div>
      <Button onClick={() => location.reload()}>Reload</Button>
    </div>
  )
}
