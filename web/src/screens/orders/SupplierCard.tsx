/**
 * One draft order. There is one card per supplier because that is what the run
 * produces — N drafts with their own terms, minimums and dates, not one basket
 * (spec §5.5).
 *
 * A supplier with no lines is NOT an empty row to hide. Three of the eight have
 * nothing on them for three different reasons — its lines were re-sourced
 * elsewhere, its products are all tier C checklist items, or it has no products
 * on file at all — and one of those is spec §15 question 5 still unanswered.
 * Hiding them would hide the reasons.
 *
 * `meets_minimum: false` means two different things and the card says which:
 * below the minimum WITH lines is a real problem (below it the supplier ships
 * nothing, so the alternative is a stockout, not a smaller order); below it with
 * nothing needed is not a problem at all, because a minimum is a condition on
 * placing an order, not a reason to place one.
 *
 * Where a card has both lines and skips, the two switch in place on an underline
 * tab rather than stacking — they are two views of the same run for the same
 * supplier, which is exactly what that tab style means. The tab carries its
 * count, so a skip is announced whether or not it is the view on screen: this is
 * a switch between two full-size views, never a disclosure that hides one.
 */
import { useState } from 'react'
import { Badge, Card, Note, TabsUnderline } from '../../components/ui'
import { plural } from '../../lib/format'
import { bucketNotes } from './notes'
import { channelLabel, prose, type SupplierOrder } from './data'
import { DayName, M } from './figures'
import { Lines } from './Lines'
import { Skips } from './Skips'
import { TermsPanel } from './Terms'

function NoteList({ notes, tone }: { notes: string[]; tone: 'warn' | 'muted' | 'bad' }) {
  if (notes.length === 0) return null
  return (
    <div className="mt-3 grid gap-1">
      {notes.map((n) => (
        <Note key={n} tone={tone}>
          {prose(n)}
        </Note>
      ))}
    </div>
  )
}

type View = 'lines' | 'skips'

export function SupplierCard({
  o,
  alreadyShown,
  open,
  onToggle,
}: {
  o: SupplierOrder
  /** Sentences rendered elsewhere on the page as first-class objects, so the
   *  same prose is not printed twice. */
  alreadyShown: readonly string[]
  open: ReadonlySet<string>
  onToggle: (key: string) => void
}) {
  const s = o.supplier
  const hasLines = o.lines.length > 0
  const hasSkips = o.skipped.length > 0
  const ph = s.terms_are_placeholders
  const shortOfMinimum = hasLines && !o.meets_minimum
  const buckets = bucketNotes(o.notes, alreadyShown)

  /* Tabs only where there are two views to switch between. With lines and no
     skips, or skips and no lines, a tab row would be one tab. */
  const tabbed = hasLines && hasSkips
  const [view, setView] = useState<View>('lines')
  const showLines = hasLines && (!tabbed || view === 'lines')
  const showSkips = hasSkips && (!tabbed || view === 'skips')

  return (
    <Card
      className={shortOfMinimum ? 'border-bad/30' : undefined}
      title={
        <span className="flex flex-wrap items-center gap-2">
          <span>{s.name}</span>
          <Badge tone="muted">{o.status.toLowerCase()}</Badge>
          {ph ? <Badge tone="warn">terms invented</Badge> : <Badge tone="ok">terms confirmed</Badge>}
          {shortOfMinimum && (
            <Badge tone="bad">
              below the <M v={s.min_order_pence} size="sm" tone="bad" /> minimum
            </Badge>
          )}
          {!hasLines && !o.meets_minimum && <Badge tone="muted">minimum not in play</Badge>}
          {o.min_order_topped_up && <Badge tone="info">topped up</Badge>}
          {o.capped_line_count > 0 && (
            <Badge tone="warn">
              {o.capped_line_count} capped {plural(o.capped_line_count, 'line')}
            </Badge>
          )}
          {o.low_confidence_line_count > 0 && (
            <Badge tone="muted">
              {o.low_confidence_line_count} forecast{' '}
              {plural(o.low_confidence_line_count, 'withheld')}
            </Badge>
          )}
        </span>
      }
      subtitle={
        <span className="flex flex-wrap items-baseline gap-x-2">
          <span>{channelLabel(s.order_channel)}</span>
          <span aria-hidden>·</span>
          <span>
            delivery <DayName iso={o.target_delivery_date} />
          </span>
          {hasSkips && (
            <>
              <span aria-hidden>·</span>
              <span>
                {o.skipped.length} {plural(o.skipped.length, 'skip')} explained
                {tabbed ? ' on its own tab' : ' below'}
              </span>
            </>
          )}
        </span>
      }
      right={
        hasLines ? (
          <span className="whitespace-nowrap">
            <M v={o.total_pence} size="xl" />
          </span>
        ) : (
          <Badge tone="muted">nothing to order</Badge>
        )
      }
    >
      <TermsPanel o={o} />

      {tabbed && (
        <div className="-mx-5 mt-5 px-5">
          <TabsUnderline
            tabs={[
              { key: 'lines', label: `Ordering · ${o.lines.length}` },
              { key: 'skips', label: `Not ordered · ${o.skipped.length}` },
            ]}
            active={view}
            onChange={setView}
            size="sm"
          />
        </div>
      )}

      <div className="mt-4">
        {showLines && <Lines o={o} open={open} onToggle={onToggle} />}
        {!hasLines && (
          <div>
            <p className="text-[0.875rem] text-ink-2">
              Nothing is being bought from {s.name} on this run.
            </p>
            <NoteList notes={buckets.nothing} tone="muted" />
            <NoteList notes={buckets.sourcing} tone="muted" />
          </div>
        )}
      </div>

      {/* Below its minimum WITH lines on it: the one state on this card that is
          a problem rather than a description. */}
      {shortOfMinimum && <NoteList notes={buckets.minimum} tone="bad" />}
      {/* Invariant 5 — what was refused as a top-up, and why. */}
      <NoteList notes={buckets.topup} tone="warn" />

      {showSkips && <Skips items={o.skipped} />}

      {hasLines && <NoteList notes={buckets.nothing} tone="muted" />}
      <NoteList notes={buckets.other} tone="muted" />

      {(buckets.placeholder.length > 0 ||
        buckets.cap.length > 0 ||
        (hasLines &&
          (buckets.sourcing.length > 0 || (!shortOfMinimum && buckets.minimum.length > 0)))) && (
        <details className="mt-4 border-t border-line pt-3">
          <summary className="cursor-pointer text-[0.75rem] text-ink-4 hover:text-ink-2">
            the run&rsquo;s own words on this order
          </summary>
          <div className="mt-2 grid gap-2">
            {[
              ...buckets.placeholder,
              ...buckets.cap,
              ...(hasLines ? buckets.sourcing : []),
              ...(shortOfMinimum ? [] : buckets.minimum),
            ].map((n) => (
              <p key={n} className="max-w-[90ch] text-[0.75rem] leading-[16px] text-ink-3">
                {prose(n)}
              </p>
            ))}
          </div>
        </details>
      )}
    </Card>
  )
}
