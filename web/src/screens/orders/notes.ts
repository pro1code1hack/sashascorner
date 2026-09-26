/**
 * Routing the API's prose notes to the right place on the screen.
 *
 * Every note is rendered VERBATIM. This file decides only where a sentence
 * belongs, by looking for the markers the API writes on purpose
 * ("THESE TERMS ARE INVENTED PLACEHOLDERS", "INVARIANT 5:", and so on) — it
 * never lifts a figure out of a sentence. Numbers come from fields; prose stays
 * prose. ARCHITECTURE.md §8M is what happens when that line gets crossed.
 */
export type NoteKind =
  | 'placeholder'
  | 'minimum'
  | 'topup'
  | 'cap'
  | 'sourcing'
  | 'nothing'
  | 'other'

export function noteKind(n: string): NoteKind {
  if (n.includes('THESE TERMS ARE INVENTED PLACEHOLDERS')) return 'placeholder'
  if (n.includes('INVARIANT 5')) return 'topup'
  /* Checked BEFORE the top-up marker: a "SOURCING MOVED ..." note ends with
     "its minimum and any top-up were re-decided", which used to route a sourcing
     note into the invariant-5 bucket and print it as a warning. */
  if (
    n.includes('SOURCING MOVED') ||
    n.startsWith('SOURCING:') ||
    / (KEPT|SWITCHED to) /.test(n)
  )
    return 'sourcing'
  if (n.includes('top-up') || n.includes('top up with')) return 'topup'
  if (n.includes('SHELF-LIFE CAP') || n.includes('SHELF LIFE / SEASON CAP')) return 'cap'
  if (n.includes('minimum order') || n.includes('DELIVERY FEE') || n.includes('delivery threshold'))
    return 'minimum'
  if (n.includes('minimum does not apply')) return 'nothing'
  if (n.includes('nothing here to calculate') || n.includes('no supplier products on file'))
    return 'nothing'
  return 'other'
}

/** Notes bucketed by where they belong, with anything already rendered as a
 *  first-class object (a skipped candidate, a sourcing choice, an emergency
 *  note) dropped so the same sentence is not printed twice. */
export function bucketNotes(
  notes: readonly string[],
  alreadyShown: readonly string[],
): Record<NoteKind, string[]> {
  const out: Record<NoteKind, string[]> = {
    placeholder: [],
    minimum: [],
    topup: [],
    cap: [],
    sourcing: [],
    nothing: [],
    other: [],
  }
  const seen = new Set(alreadyShown)
  for (const n of notes) {
    if (seen.has(n)) continue
    seen.add(n)
    out[noteKind(n)].push(n)
  }
  return out
}
