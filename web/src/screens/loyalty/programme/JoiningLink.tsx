/**
 * The joining link with a `?src=` tag, so Insights can say where members came from.
 * Tags are attribution slugs: lower-case letters, digits, - and _, 40 at most (the
 * server cleans them the same way, services/loyalty/join._clean_src).
 */
import { useEffect, useMemo, useRef, useState } from 'react'
import { Button, Field, Input, LinkButton, Segmented, StatusLine } from '../../../components/ui'
import type { Outcome } from '../../../components/ui'
import { sourceLabel } from '../members/bits'
import { qrModules, qrSvgPath } from './qr'

const PRESETS = ['counter-qr', 'window-sticker', 'instagram', 'website'] as const
type Choice = (typeof PRESETS)[number] | 'custom'

export function cleanTag(raw: string): string {
  return raw
    .toLowerCase()
    .replace(/\s+/g, '-')
    .replace(/[^a-z0-9_-]/g, '')
    .slice(0, 40)
}

async function copyText(text: string): Promise<boolean> {
  try {
    await navigator.clipboard.writeText(text)
    return true
  } catch {
    const ta = document.createElement('textarea')
    ta.value = text
    ta.setAttribute('readonly', '')
    ta.style.position = 'fixed'
    ta.style.opacity = '0'
    document.body.appendChild(ta)
    ta.select()
    let ok = false
    try {
      ok = document.execCommand('copy')
    } catch {
      ok = false
    }
    ta.remove()
    return ok
  }
}

export function JoiningLink({ joinUrl, sources }: { joinUrl: string; sources: { source: string; members: number }[] }) {
  const [choice, setChoice] = useState<Choice>('counter-qr')
  const [custom, setCustom] = useState('')
  const [outcome, setOutcome] = useState<Outcome | null>(null)
  const timer = useRef<number | null>(null)
  useEffect(() => () => {
    if (timer.current) window.clearTimeout(timer.current)
  }, [])

  const effective = choice === 'custom' ? cleanTag(custom) : choice
  const url = effective ? `${joinUrl}?src=${effective}` : joinUrl

  const copy = async () => {
    const ok = await copyText(url)
    setOutcome(
      ok
        ? { kind: 'ok', text: 'Link copied.' }
        : { kind: 'error', text: 'The browser would not copy it. Select the link and copy it by hand.' },
    )
    if (timer.current) window.clearTimeout(timer.current)
    timer.current = window.setTimeout(() => setOutcome(null), 4000)
  }

  // The QR for the counter: drawn here (qr.ts), so printing one needs no service.
  const qr = useMemo(() => {
    try {
      const { d, size } = qrSvgPath(qrModules(url))
      const svg = `<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 ${size} ${size}" shape-rendering="crispEdges"><rect width="100%" height="100%" fill="white"/><path d="${d}" fill="black"/></svg>`
      return { d, size, file: `data:image/svg+xml;charset=utf-8,${encodeURIComponent(svg)}` }
    } catch {
      return null
    }
  }, [url])

  const known = [...sources].sort((a, b) => b.members - a.members)

  return (
    <div className="flex flex-col gap-3">
      <Segmented<Choice>
        label="Where the link goes"
        className="h-auto! max-w-full flex-wrap"
        value={choice}
        onChange={setChoice}
        options={[...PRESETS.map((p) => ({ value: p, label: sourceLabel(p) })), { value: 'custom' as const, label: 'Your own tag' }]}
      />
      {choice === 'custom' && (
        <Field label="Your own tag" hint="Letters, digits and dashes, like flyer-uni.">
          <Input size="sm" value={custom} onChange={(e) => setCustom(cleanTag(e.target.value))} placeholder="flyer-uni" maxLength={40} />
        </Field>
      )}
      <div className="flex min-w-0 items-center gap-2 rounded-control border border-line-control bg-canvas-2 py-1.5 pl-3 pr-1.5">
        <span className="fig min-w-0 flex-1 break-all text-sm text-ink">{url}</span>
        <Button size="sm" variant="ghost" className="flex-none font-bold text-brand-ink" onClick={() => void copy()}>
          Copy
        </Button>
      </div>
      <StatusLine outcome={outcome} />
      {qr && (
        <div className="flex flex-wrap items-center gap-4">
          <svg
            viewBox={`0 0 ${qr.size} ${qr.size}`}
            width={132}
            height={132}
            shapeRendering="crispEdges"
            role="img"
            aria-label={`QR code for ${url}`}
            className="flex-none rounded-xs border border-line bg-surface"
          >
            <path d={qr.d} className="fill-ink" />
          </svg>
          <div className="flex min-w-0 flex-col gap-1.5">
            <p className="text-sm text-ink-2">Scanning it opens this link, tag and all. Print it at least 3 cm wide.</p>
            <LinkButton variant="outline" size="sm" href={qr.file} download={`join-${effective || 'untagged'}.svg`} className="self-start">
              Download the QR code
            </LinkButton>
          </div>
        </div>
      )}
      <div>
        <div className="mb-1 text-label font-bold uppercase tracking-[.06em] text-ink-2">Members by tag</div>
        {known.length === 0 ? (
          <p className="text-sm text-ink-2">Nobody has joined through a tagged link yet.</p>
        ) : (
          <ul className="m-0 list-none p-0">
            {known.map((s) => (
              <li key={s.source} className="flex items-baseline justify-between gap-3 border-b border-line-row py-1.5 text-base last:border-0">
                <span className="min-w-0 truncate">{sourceLabel(s.source)}</span>
                <span className="fig flex-none text-ink-2">
                  {s.members} {s.members === 1 ? 'member' : 'members'}
                </span>
              </li>
            ))}
          </ul>
        )}
      </div>
    </div>
  )
}
