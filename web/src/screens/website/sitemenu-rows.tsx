/**
 * Website menu rows: one category, one item. Everything autosaves, as the old
 * website admin did: switches and the star save at once; text saves a second
 * after typing stops, and at once on leaving the field. Each row says Unsaved /
 * Saving… / Saved / Not saved + Retry, and reports it up so the header can sum
 * it and the page can warn before a reload loses anything.
 *
 * A refetch never overwrites what somebody is typing: a row follows the server
 * only while it holds nothing of its own (no unsaved text, nothing in flight,
 * no failed change waiting for Retry).
 */
import { useCallback, useEffect, useId, useRef, useState } from 'react'
import type { ReactNode } from 'react'
import { Button, Checkbox, Field, Pill, Textarea, Toggle, cx } from '../../components/ui'
import type { WriteResult } from '../../lib/api'
import { siteWrite, useInvalidateWebsite } from '../../lib/website-api'
import {
  kindLabel,
  opsMenuHref,
  plural,
  sizeText,
  type CategoryPut,
  type ItemPut,
  type SiteMenuCategory,
  type SiteMenuItem,
} from './sitemenu-types'

export type SaveStatus = 'idle' | 'editing' | 'saving' | 'saved' | 'failed'

/** The row tells the screen its state; null when it unmounts. */
export type Report = (id: string, status: SaveStatus | null) => void

type Patch = Record<string, unknown>

/* --------------------------------------------------------------- saver --- */

function useSaver(id: string, send: (body: Patch) => Promise<WriteResult<unknown>>, report: Report) {
  const invalidate = useInvalidateWebsite()
  const invalidateRef = useRef(invalidate)
  invalidateRef.current = invalidate
  const sendRef = useRef(send)
  sendRef.current = send

  const [status, setStatusState] = useState<SaveStatus>('idle')
  const [message, setMessage] = useState<string | null>(null)
  const statusRef = useRef<SaveStatus>('idle')
  const pending = useRef<Patch | null>(null)
  const inFlight = useRef(false)
  const mounted = useRef(true)

  const setStatus = useCallback((s: SaveStatus) => {
    statusRef.current = s
    if (mounted.current) setStatusState(s)
  }, [])

  useEffect(() => {
    report(id, status)
  }, [id, status, report])

  useEffect(() => {
    mounted.current = true
    return () => {
      mounted.current = false
      report(id, null)
    }
  }, [id, report])

  const flush = useCallback(async (): Promise<void> => {
    if (inFlight.current || !pending.current) return
    const body = pending.current
    pending.current = null
    inFlight.current = true
    setMessage(null)
    setStatus('saving')
    const r = await sendRef.current(body)
    if (r.kind === 'ok') {
      // Stay "saving" until the refetch lands, or the row would briefly follow
      // the old server value and flicker back.
      await invalidateRef.current()
      inFlight.current = false
      if (pending.current) return flush()
      setStatus('saved')
      window.setTimeout(() => {
        if (statusRef.current === 'saved') setStatus('idle')
      }, 2500)
      return
    }
    inFlight.current = false
    // Keep the failed change (under anything newer) so Retry sends it again.
    pending.current = { ...body, ...(pending.current ?? {}) }
    if (mounted.current) setMessage(r.message)
    setStatus('failed')
  }, [setStatus])

  const queue = useCallback(
    (patch: Patch) => {
      pending.current = { ...(pending.current ?? {}), ...patch }
      void flush()
    },
    [flush],
  )

  const editing = useCallback(() => {
    if (statusRef.current !== 'saving' && statusRef.current !== 'failed') setStatus('editing')
  }, [setStatus])

  /** Typing went back to what is saved. */
  const settle = useCallback(() => {
    if (statusRef.current === 'editing') setStatus(pending.current ? 'failed' : 'idle')
  }, [setStatus])

  return { status, message, queue, editing, settle, retry: () => void flush() }
}

function SaveNote({ status, message, onRetry, what }: { status: SaveStatus; message: string | null; onRetry: () => void; what: string }) {
  return (
    <span className="flex min-h-6 flex-wrap items-center gap-2 text-sm" aria-live="polite">
      {status === 'editing' && <span className="text-ink-2">Unsaved</span>}
      {status === 'saving' && <span className="text-ink-2">Saving…</span>}
      {status === 'saved' && <span className="text-ink-2">Saved</span>}
      {status === 'failed' && (
        <>
          <span className="font-bold text-bad-ink">Not saved{message ? `: ${message}` : ''}</span>
          <Button variant="outline" onClick={onRetry} aria-label={`Retry saving ${what}`}>
            Retry
          </Button>
        </>
      )}
    </span>
  )
}

/**
 * A text field that autosaves. `saved` is the server's value; the draft follows
 * it only while nothing is held locally.
 */
function useAutosaveText(
  saved: string,
  saver: ReturnType<typeof useSaver>,
  field: string,
  toBody: (v: string) => unknown,
  send: (body: Patch) => Promise<WriteResult<unknown>>,
) {
  const [draft, setDraft] = useState(saved)
  const draftRef = useRef(saved)
  const savedRef = useRef(saved)
  savedRef.current = saved
  const timer = useRef<number | null>(null)
  const holding = saver.status === 'editing' || saver.status === 'saving' || saver.status === 'failed'
  const holdingRef = useRef(holding)
  holdingRef.current = holding

  // Follow the server only when the draft is still the last value we saw from
  // it: typed-but-unsent text survives any refetch.
  const seenRef = useRef(saved)
  useEffect(() => {
    const untouched = draftRef.current.trim() === seenRef.current.trim()
    seenRef.current = saved
    if (holdingRef.current || !untouched) return
    draftRef.current = saved
    setDraft(saved)
  }, [saved])

  const commit = useCallback(() => {
    if (timer.current !== null) window.clearTimeout(timer.current)
    timer.current = null
    const v = draftRef.current.trim()
    if (v === savedRef.current.trim()) {
      saver.settle()
      return
    }
    saver.queue({ [field]: toBody(v) })
  }, [saver, field, toBody])
  const commitRef = useRef(commit)
  commitRef.current = commit

  // Leaving the page mid-sentence (another screen, the hash changes) still saves it.
  const sendRef = useRef(send)
  sendRef.current = send
  const invalidate = useInvalidateWebsite()
  const invalidateRef = useRef(invalidate)
  invalidateRef.current = invalidate
  useEffect(
    () => () => {
      if (timer.current !== null) window.clearTimeout(timer.current)
      const v = draftRef.current.trim()
      if (v !== savedRef.current.trim() && holdingRef.current) {
        void sendRef.current({ [field]: toBody(v) }).then(() => invalidateRef.current())
      }
    },
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [],
  )

  return {
    value: draft,
    onChange: (v: string) => {
      draftRef.current = v
      setDraft(v)
      saver.editing()
      if (timer.current !== null) window.clearTimeout(timer.current)
      timer.current = window.setTimeout(() => commitRef.current(), 1000)
    },
    onBlur: () => commitRef.current(),
  }
}

/* ---------------------------------------------------------- move buttons --- */

export interface Mover {
  /** False while filtering, or while the order is being saved. */
  enabled: boolean
  index: number
  count: number
  move: (from: number, to: number) => void
}

/** Up / down, keyboard-operable; ids let the screen put focus back after a move. */
export function MoveButtons({ name, mover, idBase }: { name: string; mover: Mover; idBase: string }) {
  const btn =
    'grid size-11 flex-none place-items-center rounded-control border border-line-control bg-surface text-md text-ink-2 outline-none hover:bg-canvas focus-visible:edge-brand disabled:opacity-50 sm:size-9'
  return (
    <span className="flex flex-none gap-1">
      <button
        type="button"
        id={`${idBase}-up`}
        className={btn}
        aria-label={`Move ${name} up`}
        title="Move up"
        disabled={!mover.enabled || mover.index === 0}
        onClick={() => mover.move(mover.index, mover.index - 1)}
      >
        <span aria-hidden="true">↑</span>
      </button>
      <button
        type="button"
        id={`${idBase}-down`}
        className={btn}
        aria-label={`Move ${name} down`}
        title="Move down"
        disabled={!mover.enabled || mover.index >= mover.count - 1}
        onClick={() => mover.move(mover.index, mover.index + 1)}
      >
        <span aria-hidden="true">↓</span>
      </button>
    </span>
  )
}

/** A stable DOM id for a row's move buttons (names may hold any character). */
export function domId(prefix: string, key: string): string {
  let h = 0
  for (let i = 0; i < key.length; i++) h = (Math.imul(31, h) + key.charCodeAt(i)) | 0
  return `${prefix}-${(h >>> 0).toString(36)}`
}

/* --------------------------------------------------------------- prices --- */

export function Prices({ item }: { item: SiteMenuItem }) {
  if (!item.sizes.length) return <span className="text-sm text-ink-2">No sizes in the menu</span>
  return (
    <span className="fig text-sm text-ink">
      {item.sizes.map((z, i) => (
        <span key={z.code}>
          {i > 0 && <span aria-hidden="true"> · </span>}
          <span className={cx(z.price_pence === null && 'text-ink-2')}>{sizeText(z)}</span>
        </span>
      ))}
    </span>
  )
}

/* ----------------------------------------------------------------- item --- */

export function ItemRow({
  item,
  mover,
  report,
  shown,
}: {
  item: SiteMenuItem
  mover: Mover
  report: Report
  /** False when the search or a filter leaves it out (kept mounted: unsaved text survives). */
  shown: boolean
}) {
  const key = item.key
  const send = useCallback(
    (body: Patch) => siteWrite<SiteMenuItem>(`/menu/items/${encodeURIComponent(key)}`, body as ItemPut, 'PUT'),
    [key],
  )
  const saver = useSaver(`item:${key}`, send, report)
  const holding = saver.status === 'saving' || saver.status === 'failed'
  const busy = saver.status === 'saving'

  // Switches: shown at once, sent at once; follow the server when nothing is held.
  const [sig, setSig] = useState(item.web.signature)
  const [hidden, setHidden] = useState(item.web.hidden)
  const [useNote, setUseNote] = useState(item.web.use_ops_note)
  const holdingRef = useRef(holding)
  holdingRef.current = holding
  useEffect(() => {
    if (holdingRef.current) return
    setSig(item.web.signature)
    setHidden(item.web.hidden)
    setUseNote(item.web.use_ops_note)
  }, [item.web.signature, item.web.hidden, item.web.use_ops_note])

  const toDesc = useCallback((v: string) => v || null, [])
  const desc = useAutosaveText(item.web.description ?? '', saver, 'description', toDesc, send)
  const base = domId('smi', key)
  const note = item.ops_note
  const outOfSeason = item.seasonal !== null && !item.seasonal.in_season

  return (
    <li hidden={!shown} className="flex flex-col gap-2.5 border-t border-line-soft py-3 first:border-t-0">
      <div className="flex flex-wrap items-start gap-x-3 gap-y-2">
        <div className="min-w-0 flex-[1_1_14rem]">
          <div className="flex flex-wrap items-center gap-x-2 gap-y-1">
            <h3 className="text-md font-bold text-ink">{item.name}</h3>
            {sig && <Pill tone="brand">★ Signature</Pill>}
            {hidden && <Pill tone="neutral">Hidden</Pill>}
            {item.seasonal && <Pill tone="neutral">{item.seasonal.name}</Pill>}
            {outOfSeason && <Pill tone="muted">Out of season: shows when it opens</Pill>}
          </div>
          <div className="mt-0.5 flex flex-wrap items-baseline gap-x-3 gap-y-0.5">
            <Prices item={item} />
            <a href={opsMenuHref(item.name)} className="text-sm text-ink-2 underline underline-offset-2 hover:text-ink">
              Prices in Menu items
            </a>
          </div>
        </div>
        <div className="flex flex-wrap items-center gap-2">
          <button
            type="button"
            aria-pressed={sig}
            disabled={busy}
            title="Signature items carry a star on the website"
            onClick={() => {
              const on = !sig
              setSig(on)
              saver.queue({ signature: on })
            }}
            className={cx(
              'inline-flex h-11 items-center gap-1.5 rounded-control border px-3 text-base font-semibold outline-none focus-visible:edge-brand disabled:opacity-50 sm:h-9',
              sig ? 'border-brand-line bg-brand-wash text-brand-ink' : 'border-line-control bg-surface text-ink hover:bg-canvas',
            )}
          >
            <span aria-hidden="true">{sig ? '★' : '☆'}</span>
            Signature<span className="sr-only">: {item.name}</span>
          </button>
          <Toggle
            checked={!hidden}
            disabled={busy}
            className="min-h-11 sm:min-h-9"
            label={
              <>
                On website<span className="sr-only">: {item.name}</span>
              </>
            }
            onChange={(on) => {
              setHidden(!on)
              saver.queue({ hidden: !on })
            }}
          />
          <MoveButtons name={item.name} mover={mover} idBase={base} />
        </div>
      </div>

      {note && (
        <Checkbox
          checked={useNote}
          disabled={busy}
          className="min-h-11 sm:min-h-0"
          label="Use the Café Ops note as the website description"
          onChange={(on) => {
            setUseNote(on)
            saver.queue({ use_ops_note: on })
          }}
        />
      )}
      {note && useNote ? (
        <p className="rounded-control bg-canvas-2 px-2.5 py-2 text-base text-ink">
          <span className="text-xs font-bold text-ink-2">Café Ops note · </span>
          {note}
        </p>
      ) : (
        <Field
          label={
            <>
              Website description<span className="sr-only"> for {item.name}</span>
            </>
          }
          hint={note ? 'The note is kept; switching back to it loses nothing.' : undefined}
        >
          <Textarea
            rows={2}
            maxLength={600}
            className="min-h-0"
            placeholder="No description on the website"
            value={desc.value}
            onChange={(e) => desc.onChange(e.target.value)}
            onBlur={desc.onBlur}
          />
        </Field>
      )}
      <SaveNote status={saver.status} message={saver.message} onRetry={saver.retry} what={item.name} />
    </li>
  )
}

/* ------------------------------------------------------------- category --- */

export function CategoryBlock({
  cat,
  mover,
  report,
  compact,
  shown,
  children,
}: {
  cat: SiteMenuCategory
  mover: Mover
  report: Report
  /** "Categories only": no item list. */
  compact: boolean
  shown: boolean
  children: ReactNode
}) {
  const slug = cat.slug
  const send = useCallback(
    (body: Patch) => siteWrite<SiteMenuCategory>(`/menu/categories/${encodeURIComponent(slug)}`, body as CategoryPut, 'PUT'),
    [slug],
  )
  const saver = useSaver(`cat:${slug}`, send, report)
  const holding = saver.status === 'saving' || saver.status === 'failed'
  const busy = saver.status === 'saving'
  const [hidden, setHidden] = useState(cat.hidden)
  const holdingRef = useRef(holding)
  holdingRef.current = holding
  useEffect(() => {
    if (!holdingRef.current) setHidden(cat.hidden)
  }, [cat.hidden])
  const toBlurb = useCallback((v: string) => v || null, [])
  const blurb = useAutosaveText(cat.blurb ?? '', saver, 'blurb', toBlurb, send)
  const headId = useId()
  const kind = kindLabel(cat.kind)

  return (
    <section hidden={!shown} aria-labelledby={headId} className="rounded-card border border-line bg-surface px-4 py-3.5">
      <div className="flex flex-wrap items-start gap-x-3 gap-y-2">
        <div className="min-w-0 flex-[1_1_14rem]">
          <div className="flex flex-wrap items-center gap-x-2 gap-y-1">
            <h2 id={headId} className="text-lg font-extrabold tracking-[-.01em]">
              {cat.name}
            </h2>
            {hidden && <Pill tone="neutral">Hidden from website</Pill>}
          </div>
          <p className="text-sm text-ink-2">
            {kind && <>{kind} · </>}
            <span className="fig">{plural(cat.items.length, 'item')}</span>
          </p>
        </div>
        <div className="flex flex-wrap items-center gap-2">
          <Toggle
            checked={!hidden}
            disabled={busy}
            className="min-h-11 sm:min-h-9"
            label={
              <>
                On website<span className="sr-only">: the {cat.name} section</span>
              </>
            }
            onChange={(on) => {
              setHidden(!on)
              saver.queue({ hidden: !on })
            }}
          />
          <MoveButtons name={cat.name} mover={mover} idBase={domId('smc', slug)} />
        </div>
      </div>
      <Field label={<>Line under the heading<span className="sr-only"> for {cat.name}</span></>} className="mt-2.5">
        <Textarea
          rows={1}
          maxLength={300}
          className="min-h-0"
          placeholder="No line under the heading"
          value={blurb.value}
          onChange={(e) => blurb.onChange(e.target.value)}
          onBlur={blurb.onBlur}
        />
      </Field>
      <SaveNote status={saver.status} message={saver.message} onRetry={saver.retry} what={cat.name} />
      {/* Hidden, not unmounted, in "Categories only": unsaved item text survives. */}
      <div className="mt-1" hidden={compact}>
        {cat.items.length ? (
          <ul aria-label={`${cat.name} items`}>{children}</ul>
        ) : (
          <p className="py-2 text-base text-ink-2">No items in this category.</p>
        )}
      </div>
    </section>
  )
}
