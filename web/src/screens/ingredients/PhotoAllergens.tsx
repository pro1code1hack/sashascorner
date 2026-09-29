/**
 * The ingredient page's photo and allergens.
 *
 * Photo: click or drop to replace (downscaled in the browser like a menu photo,
 * then stored by content hash), "Remove photo" to clear it (the file is kept).
 * A photo the café did not take carries its credit under it: CC BY / BY-SA
 * require the author, the licence and a link back to the source.
 *
 * Allergens (UK 14): null is "not recorded", [] is "no allergens". A list the
 * reference seed researched is an estimate until somebody records it from the
 * pack; saving here signs it "checked by <operator>".
 */
import { useRef, useState } from 'react'
import type { DragEvent } from 'react'
import { Button, FilterChip, Pill, StatusLine, cx } from '../../components/ui'
import type { Outcome } from '../../components/ui'
import { ingredientApi, useInvalidateMenu } from '../../lib/menu-api'
import { useOperator } from '../../lib/operator'
import type { IngredientRow } from '../../lib/types/menu'
import { PhotoView, shrink } from '../menu/Photo'
import { useQueryClient } from '@tanstack/react-query'
import { KEYS as STOCK_KEYS } from '../../lib/stock-api'

export const UK14: [string, string][] = [
  ['celery', 'Celery'],
  ['cereals_gluten', 'Gluten'],
  ['crustaceans', 'Crustaceans'],
  ['eggs', 'Eggs'],
  ['fish', 'Fish'],
  ['lupin', 'Lupin'],
  ['milk', 'Milk'],
  ['molluscs', 'Molluscs'],
  ['mustard', 'Mustard'],
  ['tree_nuts', 'Tree nuts'],
  ['peanuts', 'Peanuts'],
  ['sesame', 'Sesame'],
  ['soya', 'Soya'],
  ['sulphites', 'Sulphites'],
]
const LABEL = new Map(UK14)

function useInvalidateBoth() {
  const invalidate = useInvalidateMenu()
  const qc = useQueryClient()
  return async () => {
    await invalidate()
    await qc.invalidateQueries({ queryKey: STOCK_KEYS.stock })
  }
}

function hostOf(url: string): string | null {
  try {
    const h = new URL(url).hostname.replace(/^www\./, '')
    return h.endsWith('wikimedia.org') ? 'Wikimedia Commons' : h
  } catch {
    return null
  }
}

export function PhotoCredit({ row }: { row: IngredientRow }) {
  const licence = row.photo_licence
  if (!row.photo_url || !licence || licence.toLowerCase().startsWith('own')) return null
  const via = row.photo_source_url ? hostOf(row.photo_source_url) : null
  const text = `Photo: ${row.photo_author ?? 'unknown author'}, ${licence}${via ? `, via ${via}` : ''}`
  return (
    <p className="text-xs text-ink-2">
      {row.photo_source_url ? (
        <a href={row.photo_source_url} target="_blank" rel="noopener noreferrer" className="underline">
          {text}
        </a>
      ) : (
        text
      )}
    </p>
  )
}

export function IngredientPhoto({ row }: { row: IngredientRow }) {
  const input = useRef<HTMLInputElement>(null)
  const [operator] = useOperator()
  const [busy, setBusy] = useState(false)
  const [outcome, setOutcome] = useState<Outcome | null>(null)
  const [over, setOver] = useState(false)
  const invalidate = useInvalidateBoth()
  const url = row.photo_url ?? null

  const upload = async (file: File | undefined) => {
    if (!file) return
    setOutcome({ kind: 'info', text: 'Uploading…' })
    setBusy(true)
    try {
      const blob = await shrink(file).catch(() => file)
      const r = await ingredientApi.uploadPhoto(row.ingredient_id, blob, operator)
      if (r.kind === 'ok') {
        await invalidate()
        setOutcome({ kind: 'ok', text: url ? 'Photo replaced.' : 'Photo added.' })
      } else setOutcome({ kind: 'error', text: r.message })
    } finally {
      setBusy(false)
    }
  }

  const onDrop = (e: DragEvent) => {
    e.preventDefault()
    setOver(false)
    void upload(e.dataTransfer.files[0])
  }

  return (
    <div className="flex w-full max-w-[240px] flex-col gap-1.5 sm:w-[220px] sm:flex-none">
      <button
        type="button"
        onClick={() => input.current?.click()}
        onDragOver={(e) => {
          e.preventDefault()
          setOver(true)
        }}
        onDragLeave={() => setOver(false)}
        onDrop={onDrop}
        aria-label={url ? `Replace the photo of ${row.name}` : `Add a photo of ${row.name}`}
        className={cx('relative aspect-square w-full overflow-hidden rounded-card', over && 'ring-2 ring-brand')}
      >
        <PhotoView
          url={url}
          placeholder={busy ? 'Uploading…' : url ? '' : 'Drop a photo here, or click to choose one'}
          className="absolute inset-0"
        />
        {busy && url && <span className="absolute inset-x-0 bottom-0 bg-surface/85 py-1 text-center text-sm text-ink-2">Uploading…</span>}
      </button>
      <input
        ref={input}
        type="file"
        accept="image/webp,image/jpeg,image/png"
        className="sr-only"
        tabIndex={-1}
        onChange={(e) => {
          void upload(e.target.files?.[0])
          e.target.value = ''
        }}
      />
      <PhotoCredit row={row} />
      <div className="flex items-start gap-2">
        <StatusLine outcome={outcome} className="min-w-0 flex-1" />
        {url && (
          <Button
            variant="link"
            className="ml-auto flex-none"
            onClick={async () => {
              const r = await ingredientApi.clearPhoto(row.ingredient_id)
              if (r.kind === 'ok') {
                await invalidate()
                setOutcome({ kind: 'ok', text: 'Photo removed.' })
              } else setOutcome({ kind: 'error', text: r.message })
            }}
          >
            Remove photo
          </Button>
        )}
      </div>
    </div>
  )
}

export function Allergens({ row }: { row: IngredientRow }) {
  const [operator] = useOperator()
  const invalidate = useInvalidateBoth()
  const [editing, setEditing] = useState(false)
  const [picked, setPicked] = useState<Set<string>>(new Set(row.allergens ?? []))
  const [pending, setPending] = useState(false)
  const [outcome, setOutcome] = useState<Outcome | null>(null)
  const list = row.allergens ?? null
  const estimate = list !== null && !row.allergens_confirmed
  const researched = row.allergens_source?.startsWith('http') ? row.allergens_source : null

  const save = async (value: string[] | null) => {
    setPending(true)
    setOutcome(null)
    const r = await ingredientApi.allergens(row.ingredient_id, value, operator)
    setPending(false)
    if (r.kind === 'ok') {
      setEditing(false)
      await invalidate()
      setOutcome({ kind: 'ok', text: value === null ? 'Marked not recorded.' : `Saved, checked by ${operator}.` })
    } else setOutcome({ kind: 'error', text: r.message })
  }

  return (
    <section aria-labelledby="ing-allergens" className="flex min-w-0 flex-1 flex-col gap-2">
      <div className="flex flex-wrap items-baseline gap-x-2.5">
        <h3 id="ing-allergens" className="text-lg font-extrabold">
          Allergens
        </h3>
        {!editing && (
          <Button
            variant="link"
            onClick={() => {
              setPicked(new Set(row.allergens ?? []))
              setEditing(true)
            }}
          >
            {list === null ? 'Record from the pack' : 'Edit'}
          </Button>
        )}
      </div>

      {!editing && (
        <>
          {list === null ? (
            <p className="text-base text-ink-2">Allergens not recorded</p>
          ) : list.length === 0 ? (
            <div className="flex flex-wrap gap-1.5">
              <Pill tone="muted">No allergens</Pill>
            </div>
          ) : (
            <ul className="flex flex-wrap gap-1.5" aria-label="Contains">
              {list.map((a) => (
                <li key={a}>
                  <Pill tone="neutral" className="text-ink">
                    {LABEL.get(a) ?? a}
                  </Pill>
                </li>
              ))}
            </ul>
          )}
          {estimate && (
            <p className="text-sm italic text-ink-2">
              Estimate — check the pack.
              {researched && (
                <>
                  {' '}
                  <a href={researched} target="_blank" rel="noopener noreferrer" className="underline">
                    Where it came from
                  </a>
                </>
              )}
            </p>
          )}
          {list !== null && row.allergens_confirmed && <p className="text-sm text-ink-2">{capitalise(row.allergens_source ?? '')}</p>}
        </>
      )}

      {editing && (
        <div className="flex flex-col gap-2.5">
          <p className="text-sm text-ink-2">Tick what the pack lists. Saving records it as checked by {operator}.</p>
          <div className="flex flex-wrap gap-1.5">
            {UK14.map(([key, label]) => (
              <FilterChip
                key={key}
                active={picked.has(key)}
                onClick={() => {
                  const next = new Set(picked)
                  if (next.has(key)) next.delete(key)
                  else next.add(key)
                  setPicked(next)
                }}
              >
                {label}
              </FilterChip>
            ))}
          </div>
          <div className="flex flex-wrap items-center gap-2">
            <Button variant="primary" size="sm" pending={pending} pendingLabel="Saving…" onClick={() => save([...picked])}>
              {picked.size === 0 ? 'Save: no allergens' : 'Save'}
            </Button>
            <Button variant="ghost" size="sm" onClick={() => setEditing(false)}>
              Cancel
            </Button>
            {list !== null && (
              <Button variant="link" className="ml-auto" onClick={() => save(null)}>
                Mark not recorded
              </Button>
            )}
          </div>
        </div>
      )}
      <StatusLine outcome={outcome} />
    </section>
  )
}

function capitalise(s: string): string {
  return s ? s.charAt(0).toUpperCase() + s.slice(1) + '.' : ''
}
