/**
 * The most urgent thing in the payload, and the only part of this screen that
 * is about a date rather than a quantity.
 *
 * Two different states live here and they must not be blurred together:
 *
 *  - `emergency[]` — something runs out before its proper supplier can deliver
 *    and Tesco CAN bridge it. Retail costs more, so the premium is printed: the
 *    accumulated log of those premiums is the argument for fixing the ordering
 *    cadence (spec §4.4), not a licence to shop.
 *  - `emergency_notes[]` — something runs out before its next delivery and
 *    NOTHING can bridge it, Tesco included. That is a stockout unless the
 *    supplier is hurried. It is worse than a premium and it is easy to miss,
 *    because it has no line and therefore no money attached.
 *
 * On most days `emergency` is empty and the notes are not, which is exactly the
 * case the fixture records.
 */
import { Badge, Card, Note, Panel, SectionLabel } from '../../components/ui'
import { cmp, mustDec } from '../../lib/dec'
import { plural } from '../../lib/format'
import { Field, M, MExact, Q } from './figures'
import { prose, type EmergencyLine } from './data'

/** "12oz cup lid: runs out in 2.08 day(s), ..." — the subject is split off so a
 *  list of seven is scannable. Subject + body is the sentence, unchanged. */
function split(note: string): { subject: string | null; body: string } {
  const i = note.indexOf(': ')
  if (i <= 0 || i > 60) return { subject: null, body: note }
  return { subject: note.slice(0, i), body: note.slice(i + 2) }
}

/**
 * Every one of these notes ends in the same sentence, and the paragraph above the
 * list already says it. Seven verbatim repetitions of "Nothing can bridge this gap
 * -- it is a stockout unless the proper supplier can be hurried" bury the part that
 * differs, which is the ingredient and how many days are left.
 *
 * The tail is lifted only when EVERY body ends with the byte-identical sentence, so
 * nothing is ever paraphrased away; if one note differs, all seven print in full.
 * Sentence-split on ". " and compare from the end.
 */
function stripSharedTail(bodies: string[]): { bodies: string[]; shared: string | null } {
  if (bodies.length < 2) return { bodies, shared: null }
  const parts = bodies.map((b) => b.split(/(?<=\.)\s+/).filter(Boolean))
  let n = 0
  for (;;) {
    const idx = parts.map((p) => p.length - 1 - n)
    if (idx.some((i) => i <= 0)) break
    const first = parts[0]![idx[0]!]
    if (!parts.every((p, k) => p[idx[k]!] === first)) break
    n += 1
  }
  if (n === 0) return { bodies, shared: null }
  return {
    bodies: parts.map((p) => p.slice(0, p.length - n).join(' ')),
    shared: parts[0]!.slice(parts[0]!.length - n).join(' '),
  }
}

/** Exact comparison of two money strings. Never parsed to a float. */
function differs(a: string | null, b: string | null): boolean {
  if (a === null || b === null) return a !== b
  return cmp(mustDec(a), mustDec(b)) !== 0
}

function Bridged({ e }: { e: EmergencyLine }) {
  return (
    <li className="min-w-0">
      <Panel
        tone="bad"
        title={
          <span className="flex flex-wrap items-center gap-2">
            <span className="text-ink">{e.ingredient_name}</span>
            <Badge tone="bad">bought at retail</Badge>
            {e.retail_is_cheaper && <Badge tone="info">retail was cheaper anyway</Badge>}
          </span>
        }
        right={<Q qty={e.qty} unit={e.unit} size="lg" />}
      >
        <div className="grid grid-cols-2 gap-x-4 gap-y-3 sm:grid-cols-4">
          <Field label="Retail unit price">
            <MExact v={e.retail_unit_price_pence} missing="no retail price" />
          </Field>
          <Field label="Preferred unit price">
            <MExact v={e.preferred_unit_price_pence} missing="no supplier price" />
          </Field>
          <Field label="Premium paid" tone="warn">
            <MExact v={e.premium_pence} missing="not priced" tone="bad" />
          </Field>
          {/* The signed difference is only worth its own column when it differs
              from the premium — which happens exactly when retail was cheaper and
              the "premium" is therefore floored at nothing. */}
          {differs(e.premium_pence, e.raw_premium_pence) && (
            <Field label="Signed difference">
              <MExact v={e.raw_premium_pence} missing="not priced" />
            </Field>
          )}
        </div>

        <p className="mt-3">{prose(e.reason)}</p>
      </Panel>
    </li>
  )
}

export function Urgent({
  emergency,
  notes,
  totalPremiumPence,
}: {
  emergency: EmergencyLine[]
  /** `emergency_notes`, already deduplicated against the run-level notes. */
  notes: string[]
  totalPremiumPence: string | null
}) {
  const nothing = emergency.length === 0 && notes.length === 0
  return (
    <Card
      title="Runs out before anything can arrive"
      subtitle={
        nothing
          ? 'Nothing is forecast to run out before its next delivery.'
          : 'Cover that the scheduled rounds do not reach. This is the part of the run that needs a decision today.'
      }
      right={
        emergency.length > 0 ? (
          <span className="whitespace-nowrap">
            <span className="mr-2 text-[0.6875rem] text-ink-3">premium</span>
            <MExact v={totalPremiumPence} tone="bad" size="lg" missing="not computed" />
          </span>
        ) : undefined
      }
      className="border-bad/25"
    >
      {nothing ? (
        <Note tone="ok">
          Every tracked ingredient is covered to its next delivery. Nothing was routed to
          retail and nothing is short.
        </Note>
      ) : (
        <>
          {emergency.length > 0 && (
            <>
              <SectionLabel>
                Bridged at retail · {emergency.length} {plural(emergency.length, 'line')}
              </SectionLabel>
              <ul className="grid gap-3">
                {emergency.map((e) => (
                  <Bridged key={e.ingredient_id} e={e} />
                ))}
              </ul>
              <Note tone="muted">
                <span className="mt-2 block">
                  A retail run is a symptom of the ordering cadence, not a supplier choice.
                  Every one of them is logged with its premium, and the accumulated log is the
                  argument for changing the cadence.
                </span>
              </Note>
            </>
          )}

          {notes.length > 0 && (
            <div className={emergency.length > 0 ? 'mt-2 border-t border-line pt-2' : ''}>
              <SectionLabel>Nothing can bridge these · {notes.length}</SectionLabel>
              <p className="-mt-2 mb-3 max-w-[86ch] text-[0.75rem] text-ink-4">
                Each of these runs out before its next delivery and retail does not stock it
                either. There is no line to confirm: the fix is to hurry the proper supplier, or
                to accept the stockout.
              </p>
              {(() => {
                const parsed = notes.map(split)
                const folded = stripSharedTail(parsed.map((x) => x.body))
                return (
                  <>
                    <ul className="grid gap-2 xl:grid-cols-2">
                      {parsed.map((x, i) => (
                        <li key={notes[i]} className="min-w-0">
                          <Panel
                            tone="bad"
                            title={
                              x.subject ? <span className="text-ink">{x.subject}</span> : undefined
                            }
                          >
                            {prose(folded.bodies[i] ?? x.body)}
                          </Panel>
                        </li>
                      ))}
                    </ul>
                    {folded.shared && (
                      <p className="mt-2 max-w-[86ch] text-[0.75rem] text-ink-5">
                        All {notes.length}: {folded.shared}
                      </p>
                    )}
                  </>
                )
              })()}
            </div>
          )}
        </>
      )}
    </Card>
  )
}

/** Used by the summary band: the premium as one figure, or the reason there is
 *  no figure. Kept here so the rule about a partial sum lives in one file. */
export function Premium({
  totalPremiumPence,
  lines,
}: {
  totalPremiumPence: string | null
  lines: number
}) {
  if (totalPremiumPence === null) return <M v={null} missing="not computed" />
  if (lines === 0) return <span className="text-[1.25rem] font-normal text-ink-4">none</span>
  return <MExact v={totalPremiumPence} tone="bad" size="xl" />
}
