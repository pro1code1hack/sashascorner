/**
 * The card as the customer sees it in their wallet: blush, "SASHA'S CORNER", the
 * count, a headline, a sticker per filled slot and a dashed numbered ring for the
 * rest. Tapping a filled slot (when `onPick` is given) opens the eight stickers to
 * choose from — cosmetic, the ledger is untouched (BACKOFFICE-V2 §2).
 */
import { useEffect, useRef, useState } from 'react'
import { cx } from '../../../components/ui'
import type { CardFace, StickerKey } from '../../../lib/types/loyalty'
import { STICKERS, StickerImg, stickerName } from '../stickers'

export function headline(card: CardFace): string {
  if (card.reward_available) return 'Free drink ready'
  const left = card.stamps_required - card.stamps_current
  if (card.stamps_current === 0) return `${card.stamps_required} stamps, then a free drink, any drink`
  return `A free drink after ${left} more`
}

export function PhysicalCard({
  card,
  memberName,
  onPick,
  busySlot,
}: {
  card: CardFace
  memberName: string
  onPick?: (slot: number, sticker: StickerKey) => void
  /** The slot being saved, dimmed meanwhile. */
  busySlot?: number | null
}) {
  const [openSlot, setOpenSlot] = useState<number | null>(null)
  const slotRefs = useRef<Array<HTMLButtonElement | null>>([])
  /** Close the picker; `refocus` puts focus back on the slot that opened it (not on an outside click). */
  const close = (slot: number, refocus: boolean) => {
    setOpenSlot(null)
    if (refocus) slotRefs.current[slot]?.focus()
  }
  const slots = Math.min(Math.max(card.stamps_required, 1), 12)
  return (
    <div className="relative w-full max-w-[21rem] flex-none rounded-card-lg bg-pass-blush px-4 pb-4 pt-4 text-pass-ink shadow-login sm:px-5">
      <div className="flex items-start justify-between gap-3">
        <span className="pt-0.5 text-label font-extrabold uppercase tracking-[.16em]">Sasha’s Corner</span>
        <span className="text-right leading-none">
          <span className="block text-label font-extrabold uppercase tracking-[.14em]">Stamps</span>
          <span className="fig text-xl font-extrabold">
            {card.stamps_current}/{card.stamps_required}
          </span>
        </span>
      </div>
      <p className="mt-2 flex items-center gap-2 text-xl font-extrabold leading-snug tracking-[-.01em]">
        {card.reward_available && (
          <img src="/stickers/reward.svg" alt="" aria-hidden="true" width={34} height={34} className="size-[34px] flex-none select-none" />
        )}
        {headline(card)}
      </p>
      <ol className="mt-3 grid grid-cols-4 gap-2" aria-label={`${card.stamps_current} of ${card.stamps_required} stamps`}>
        {Array.from({ length: slots }, (_, i) => {
          const key = card.stickers[i]
          const filled = i < card.stamps_current
          return (
            <li key={i} className="relative aspect-square">
              {filled ? (
                <button
                  ref={(el) => {
                    slotRefs.current[i] = el
                  }}
                  type="button"
                  disabled={!onPick || card.voided}
                  onClick={() => setOpenSlot(openSlot === i ? null : i)}
                  aria-label={`Stamp ${i + 1}: ${stickerName(key) || 'sticker'}. Change sticker`}
                  aria-expanded={openSlot === i}
                  className={cx(
                    'grid size-full place-items-center rounded-full bg-surface shadow-raised transition-transform',
                    onPick && 'hover:scale-[1.04] focus-visible:outline-2 focus-visible:outline-brand',
                    busySlot === i && 'opacity-50',
                  )}
                >
                  <StickerImg sticker={key} size={40} className="size-[62%]" />
                </button>
              ) : (
                <span className="fig grid size-full place-items-center rounded-full border-[1.5px] border-dashed border-pass-ink/50 text-2xl font-semibold text-pass-ink/80">
                  {i + 1}
                </span>
              )}
              {openSlot === i && onPick && (
                <StickerPicker
                  current={key}
                  alignRight={i % 4 >= 2}
                  onClose={(refocus) => close(i, refocus)}
                  onPick={(s) => {
                    close(i, true)
                    if (s !== key) onPick(i, s)
                  }}
                />
              )}
            </li>
          )
        })}
      </ol>
      <div className="mt-4 flex items-end justify-between gap-3 border-t border-pass-ink/20 pt-2.5">
        <span className="min-w-0">
          <span className="block text-label font-extrabold uppercase tracking-[.14em]">Member</span>
          <span className="block truncate text-md font-bold">{memberName}</span>
        </span>
        <span className="flex-none text-sm">{card.voided ? 'Deleted' : 'Active'}</span>
      </div>
    </div>
  )
}

function StickerPicker({
  current,
  alignRight,
  onPick,
  onClose,
}: {
  current: StickerKey | undefined
  alignRight: boolean
  onPick: (s: StickerKey) => void
  /** `true` when focus should go back to the slot (Escape), `false` for a click or tab away. */
  onClose: (refocus: boolean) => void
}) {
  const ref = useRef<HTMLDivElement>(null)
  // The latest onClose, so the effect runs once per opening (it also moves focus in).
  const closeRef = useRef(onClose)
  closeRef.current = onClose
  useEffect(() => {
    const onClose = (refocus: boolean) => closeRef.current(refocus)
    const down = (e: MouseEvent) => {
      if (ref.current && !ref.current.parentElement?.contains(e.target as Node)) onClose(false)
    }
    const key = (e: KeyboardEvent) => {
      if (e.key === 'Escape') onClose(true)
    }
    document.addEventListener('mousedown', down)
    document.addEventListener('keydown', key)
    ref.current?.querySelector<HTMLButtonElement>('button[aria-pressed="true"], button')?.focus()
    return () => {
      document.removeEventListener('mousedown', down)
      document.removeEventListener('keydown', key)
    }
  }, [])
  return (
    <div
      ref={ref}
      role="dialog"
      aria-label="Choose a sticker"
      onBlur={(e) => {
        // Tabbing out of the picker closes it, so no open popover is left behind.
        const to = e.relatedTarget as Node | null
        if (to && !ref.current?.parentElement?.contains(to)) onClose(false)
      }}
      className={cx(
        'absolute top-[calc(100%+6px)] z-20 w-[13.5rem] rounded-card border border-line bg-surface p-2 text-ink shadow-login',
        alignRight ? 'right-0' : 'left-0',
      )}
    >
      <div className="grid grid-cols-4 gap-1">
        {STICKERS.map((s) => (
          <button
            key={s.key}
            type="button"
            aria-pressed={s.key === current}
            title={s.name}
            onClick={() => onPick(s.key)}
            className={cx(
              'grid aspect-square place-items-center rounded-control hover:bg-canvas',
              s.key === current && 'bg-brand-wash ring-2 ring-brand',
            )}
          >
            <StickerImg sticker={s.key} size={32} label={s.name} />
          </button>
        ))}
      </div>
    </div>
  )
}
