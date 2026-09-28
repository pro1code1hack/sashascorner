/**
 * "What members see": the wallet card shown halfway to a free drink, drawn from the
 * programme as it is being edited (unsaved stamps and sticker order included).
 * Colours are the pass's own (blush background, olive ink), not the back office's.
 */
import type { StickerKey } from '../../../lib/types/loyalty'
import { StickerImg } from '../stickers'

const BLUSH = '#e9dcd6'
const OLIVE = '#474531'

/** A QR-ish glyph: the real pass carries the member's code here. */
function QrGlyph() {
  const on = [
    [0, 0], [1, 0], [2, 0], [0, 1], [2, 1], [0, 2], [1, 2], [2, 2],
    [4, 0], [4, 1], [3, 3], [4, 3], [1, 4], [3, 4], [4, 4], [0, 4],
  ]
  return (
    <span className="grid size-9 flex-none place-items-center rounded-[6px] bg-surface" aria-hidden="true">
      <svg viewBox="0 0 5 5" width="26" height="26" shapeRendering="crispEdges">
        {on.map(([x, y]) => (
          <rect key={`${x}-${y}`} x={x} y={y} width="1" height="1" fill={OLIVE} />
        ))}
      </svg>
    </span>
  )
}

export function CardPreview({
  required,
  stickers,
  rewardText,
}: {
  required: number
  /** Enabled stickers in scanner order; slot i shows stickers[i % n]. */
  stickers: StickerKey[]
  rewardText: string
}) {
  const filled = Math.floor(required / 2)
  const left = required - filled
  const cols = required <= 8 ? 4 : required <= 10 ? 5 : 6
  return (
    <figure className="m-0">
      <div
        className="mx-auto w-full max-w-[20rem] rounded-[18px] px-4 pb-4 pt-4 shadow-raised"
        style={{ background: BLUSH, color: OLIVE }}
        role="img"
        aria-label={`Card preview: ${filled} of ${required} stamps; ${left} more for the reward (${rewardText.trim() || 'a free drink'}).`}
      >
        <div className="flex items-start justify-between gap-3">
          <span className="pt-0.5 text-[11px] font-extrabold uppercase tracking-[.14em]">Sasha's Corner</span>
          <span className="text-right leading-none">
            <span className="block text-[10px] font-extrabold uppercase tracking-[.12em]">Stamps</span>
            <span className="fig text-xl font-extrabold">
              {filled}/{required}
            </span>
          </span>
        </div>
        <p className="mb-3 mt-2 text-xl font-extrabold leading-tight tracking-[-.01em]">A free drink after {left} more</p>
        <div className="grid gap-2" style={{ gridTemplateColumns: `repeat(${cols}, minmax(0, 1fr))` }}>
          {Array.from({ length: required }, (_, i) =>
            i < filled ? (
              <span key={i} className="grid aspect-square place-items-center rounded-full bg-surface">
                <StickerImg sticker={stickers[i % Math.max(stickers.length, 1)]} size={30} className="size-[62%]" />
              </span>
            ) : (
              <span
                key={i}
                className="fig grid aspect-square place-items-center rounded-full border-[1.5px] border-dashed text-lg font-semibold"
                style={{ borderColor: 'rgb(71 69 49 / .35)', color: 'rgb(71 69 49 / .7)' }}
              >
                {i + 1}
              </span>
            ),
          )}
        </div>
        <div className="mt-3.5 flex items-end justify-between gap-3 border-t pt-2.5" style={{ borderColor: 'rgb(71 69 49 / .18)' }}>
          <span className="leading-tight">
            <span className="block text-[10px] font-extrabold uppercase tracking-[.12em]">Member</span>
            <span className="text-base font-bold">You</span>
          </span>
          <QrGlyph />
        </div>
      </div>
      <figcaption className="mt-2.5 text-center text-sm text-ink-2">Shown halfway to a free drink, in Apple and Google Wallet.</figcaption>
    </figure>
  )
}
