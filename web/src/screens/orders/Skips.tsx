/**
 * What was deliberately NOT bought.
 *
 * `skipped[]` is the most valuable content in the payload and it is not an error
 * list. A shelf-life cap that skipped 6.084 L of milk is the system working
 * correctly: the difference would have spoiled, and the reason string says so at
 * length. Three causes are distinguished because their fixes are different:
 *
 *   is_capped        the window was shortened — shelf life, or a season ending
 *   below_par_floor  under its floor with nothing forecast to move it: a human
 *                    decides whether the floor or the tier is wrong, not a clamp
 *   out_of_season    the drink is not on the menu, so the stock is not needed
 *
 * The reason is rendered verbatim. The API joins two sentences with ` | `, which
 * is the only thing split here; no figure is ever lifted out of the prose.
 */
import { Badge, Panel, SectionLabel, type Tone } from '../../components/ui'
import { plural } from '../../lib/format'
import { prose, type SkippedCandidate } from './data'

function sentences(reason: string): string[] {
  return reason
    .split(' | ')
    .map((s) => s.trim())
    .filter((s) => s.length > 0)
}

/** The cause decides the tone, and the tone is the backend's classification of
 *  it rather than a mood: a data error is bad, a cap needs a human to confirm a
 *  shelf life, a par floor is information. */
function tone(s: SkippedCandidate): Tone {
  if (s.data_error) return 'bad'
  if (s.is_capped) return 'warn'
  if (s.below_par_floor) return 'info'
  return 'muted'
}

function Badges({ s }: { s: SkippedCandidate }) {
  return (
    <>
      {s.out_of_season ? (
        <Badge tone="info">out of season</Badge>
      ) : s.is_capped ? (
        <Badge tone="warn">shelf-life cap</Badge>
      ) : null}
      {s.below_par_floor && <Badge tone="info">under par floor</Badge>}
      {s.data_error && <Badge tone="bad">data error</Badge>}
      {s.clamp_blocked && <Badge tone="warn">clamp refused</Badge>}
    </>
  )
}

export function Skips({ items }: { items: SkippedCandidate[] }) {
  if (items.length === 0) return null
  return (
    <div className="mt-5">
      {/* A heading, not a disclosure. This content is the answer to the question
          no spreadsheet answers, so it is never folded away. */}
      <SectionLabel>
        Deliberately not ordered · {items.length} {plural(items.length, 'decision')}
      </SectionLabel>
      <p className="-mt-2 mb-3 max-w-[86ch] text-[0.75rem] text-ink-4">
        Decisions with causes, not failures — the run considered each of these and declined.
      </p>

      <ul className="grid gap-3 xl:grid-cols-2">
        {items.map((s) => (
          <li key={s.ingredient_id} className="min-w-0">
            <Panel
              tone={tone(s)}
              title={
                <span className="flex flex-wrap items-center gap-2">
                  <span className="text-ink">{s.ingredient_name}</span>
                  <Badges s={s} />
                </span>
              }
            >
              {s.cap_reason && (
                <p className="font-medium text-warn-ink">{prose(s.cap_reason)}</p>
              )}
              {sentences(s.reason).map((line) => (
                <p key={line} className="mt-1.5 first:mt-0">
                  {prose(line)}
                </p>
              ))}
              {s.data_error && <p className="mt-1.5 text-bad-ink">{prose(s.data_error)}</p>}
              {s.clamp_blocked && (
                <p className="mt-1.5 text-warn-ink">{prose(s.clamp_blocked)}</p>
              )}
            </Panel>
          </li>
        ))}
      </ul>
    </div>
  )
}
