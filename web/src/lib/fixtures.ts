/**
 * Fixture mode (VITE_LIVE unset, no VITE_API_BASE): every GET is answered from
 * `web/fixtures/`, the real responses `cafeops api-fixtures` recorded, instead of
 * the network. `request()` calls this, so a screen reads the same way in both
 * modes and there is one place that knows about fixtures.
 *
 * `fixtures/index.json` says which request each file answers (path + query).
 * A request matches the recorded example with the same path whose query agrees
 * with it most; a recorded query that CONTRADICTS the request (tier=A when the
 * screen asked for tier=B) is not a match. Anything unrecorded -- another
 * ingredient's count history, say -- is a 404 that says so, never a
 * substitute: showing Whole milk's history under Oat milk's name would be a lie.
 *
 * Files load lazily, one chunk each, so fixtures never weigh on the live bundle.
 */
import { ApiError } from './api'

interface IndexEntry {
  file: string
  method: string
  path: string
  params: Record<string, string> | null
  status: number
}

const files = import.meta.glob<unknown>(['../../fixtures/*.json', '!../../fixtures/openapi.json'], {
  import: 'default',
})

let indexPromise: Promise<IndexEntry[]> | null = null
function loadIndex(): Promise<IndexEntry[]> {
  const load = files['../../fixtures/index.json']
  if (load === undefined) return Promise.resolve([])
  indexPromise ??= load().then((ix) => ((ix as { examples?: IndexEntry[] }).examples ?? []).filter((e) => e.method === 'GET'))
  return indexPromise
}

export async function fixtureResponse(pathWithQuery: string): Promise<unknown> {
  const url = new URL(pathWithQuery, 'http://fixtures.local')
  const index = await loadIndex()
  let best: { e: IndexEntry; score: number } | null = null
  for (const e of index) {
    if (e.path !== url.pathname || e.status !== 200) continue
    const recorded = Object.entries(e.params ?? {})
    if (recorded.some(([k, v]) => url.searchParams.has(k) && url.searchParams.get(k) !== String(v))) continue
    const agree = recorded.filter(([k]) => url.searchParams.has(k)).length
    // Prefer more agreement, then the example with fewer extra constraints.
    const score = agree * 100 - (recorded.length - agree)
    if (best === null || score > best.score) best = { e, score }
  }
  const load = best ? files[`../../fixtures/${best.e.file}`] : undefined
  if (load === undefined) {
    const msg = `Not in the recorded fixtures (${url.pathname}). Run with VITE_LIVE=1 against the API to read it.`
    throw new ApiError(404, { detail: msg }, msg)
  }
  return load()
}
