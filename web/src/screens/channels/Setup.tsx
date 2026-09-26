/**
 * The empty state — which is no longer the whole screen, and must still be
 * right.
 *
 * A fresh deployment has `channels_present`, `sources_present`, `performance`
 * and `findings` all empty, and they are empty for a reason worth saying out
 * loud: the demo seed writes no channel rows, and Deliveroo's and Just Eat's
 * partner APIs are gated to certified POS integrators, so a hand export is the
 * only path that works.
 *
 * So this is not a "no data" line. It names what lands here once fed, gives the
 * two commands that feed it as text you can copy, and says why the commands
 * exist instead of a sync. An empty state that teaches the next action is the
 * one screen state a new deployment meets first.
 *
 * The commands are the kit's `<CopyBlock>` now. This file used to carry its own
 * block with a copy button beside it; `navigator.clipboard` is unavailable over
 * plain http on a LAN address, so the button failed silently more often than it
 * worked and `select-all` is what actually copies.
 */
import { Badge, CopyBlock, Note, Panel } from '../../components/ui'
import { Label } from '../../components/prim'

function Step({
  n,
  title,
  children,
}: {
  n: string
  title: string
  children: React.ReactNode
}) {
  return (
    <div className="flex gap-3">
      <span className="fig mt-0.5 shrink-0 text-[0.6875rem] font-semibold text-ink-4">{n}</span>
      <div className="min-w-0 flex-1">
        <p className="text-[0.875rem] font-medium text-ink">{title}</p>
        <div className="mt-2 grid grid-cols-[minmax(0,1fr)] gap-2">{children}</div>
      </div>
    </div>
  )
}

/** What to run. Two commands, then the environment variable for real exports. */
export function LoadIt() {
  return (
    <div className="grid grid-cols-[minmax(0,1fr)] gap-5">
      <Step n="1" title="Load the shipped fixtures to see the screen populated">
        <CopyBlock>uv run cafeops channels import deliveroo --commit</CopyBlock>
        <CopyBlock>uv run cafeops channels import just-eat --commit</CopyBlock>
        <Note>
          These read the sample exports that ship with the repo. Enough to prove the pipeline and
          the screen; not your trading.
        </Note>
      </Step>

      <Step n="2" title="Point it at your own portal exports instead">
        <CopyBlock>export CAFEOPS_CHANNEL_CSV_DIR=/path/to/portal-exports</CopyBlock>
        <Note>
          Download the performance CSV from each portal — Deliveroo Partner Hub and the Just Eat
          partner centre — drop both files in that directory, then run the same two imports. Every
          row records the <code className="fig">source</code> it came from, so a hand export and a
          scrape are never averaged together as if equally trustworthy.
        </Note>
      </Step>
    </div>
  )
}

/** Why there is no sync button. */
export function WhyManual() {
  return (
    <div className="grid grid-cols-[minmax(0,1fr)] gap-3">
      <div className="flex flex-wrap items-center gap-2">
        <Badge tone="info">no API available</Badge>
        <Label>not a missing feature</Label>
      </div>
      <p className="text-[0.875rem] leading-[20px] text-ink-2">
        Deliveroo and Just Eat gate their partner APIs to{' '}
        <strong className="text-ink">certified POS integrators</strong>. A single-site café with
        portal logins cannot get a key, and no amount of building here changes that.
      </p>
      <p className="text-[0.875rem] leading-[20px] text-ink-3">
        So the channel reader is a <code className="fig">ChannelSource</code> protocol with a CSV
        implementation behind it. A browser agent that signs into the portal and downloads the same
        file is the second implementation, and it will break whenever a portal is redesigned — at
        which point the CSV path is what the numbers fall back to. Manual export is the floor,
        deliberately.
      </p>
    </div>
  )
}

/**
 * What an import will and will not match.
 *
 * An export line names an item in the portal's words. The importer resolves it
 * against a menu item and size, and when it cannot, it skips the line rather
 * than attaching the views and orders to the nearest-looking product — a wrong
 * match is permanent and silent, and it moves money between items on the margin
 * screen too. The last run of the shipped Just Eat export did exactly this.
 */
export function Matching() {
  return (
    <Panel title="Unmatched export lines are skipped, not guessed">
      A portal line names an item in the portal&rsquo;s words, and the importer has to resolve it
      to a menu item and a size before it can attribute anything. When it cannot, it drops the
      line. The last import of the shipped files reported one:{' '}
      <code className="fig">Iced Hazelnut Latte [M]</code> on the Just Eat export, on 9 days — no
      menu item of that name exists at size M, so its views and orders are in nobody&rsquo;s
      figures rather than in the wrong item&rsquo;s. That is the importer working; the fix is
      either the menu or the portal listing, not the import.
      <span className="mt-2 block text-ink-4">
        Skips are reported by the import run, not by this endpoint — the response carries no note
        about them, so the count above is from the run that produced these rows and not a live
        figure.
      </span>
    </Panel>
  )
}

/** What the populated screen answers. Written as questions, because that is how
 *  the owner will arrive at it. */
export function WhatLandsHere() {
  const rows: { q: string; a: string }[] = [
    {
      q: 'What is left after the platform takes its cut and the ads are paid for?',
      a: 'Gross, commission and ad spend per channel, and contribution after both. A day that reported only two of the three is dropped whole rather than part-subtracted, and the screen says how many days that was.',
    },
    {
      q: 'Is the ad spend earning anything?',
      a: 'ROAS per channel from attributed revenue, beside the commission rate and the net margin it leaves. Delivery platforms are an ad platform as much as a sales channel, and the spend is invisible in the POS.',
    },
    {
      q: 'Which items rank well but convert badly?',
      a: 'Items inside the top ranks of their category whose views-to-orders rate sits under the channel benchmark, with the orders that shortfall cost. The cheapest thing on this screen to fix: the listing is already being seen.',
    },
  ]
  return (
    <div className="divide-y divide-line">
      {rows.map((r, i) => (
        <div key={r.q} className={i === 0 ? 'pb-4' : 'py-4 last:pb-0'}>
          <p className="text-[0.875rem] font-medium text-ink">{r.q}</p>
          <p className="mt-1 text-[0.875rem] leading-[20px] text-ink-3">{r.a}</p>
        </div>
      ))}
    </div>
  )
}
