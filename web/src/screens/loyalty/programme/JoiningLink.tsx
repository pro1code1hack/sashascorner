/**
 * The joining link with a `?src=` tag, so Insights can say where members came from.
 * Tags are attribution slugs: lower-case letters, digits, - and _, 40 at most (the
 * server cleans them the same way, services/loyalty/join._clean_src).
 */
import { useEffect, useRef, useState } from 'react'
import { Input, cx } from '../../../components/ui'
import { sourceLabel } from '../members/bits'

const PRESETS = ['counter-qr', 'window-sticker', 'instagram', 'website'] as const

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
  const [tag, setTag] = useState<string>('counter-qr')
  const [custom, setCustom] = useState('')
  const [copied, setCopied] = useState<'ok' | 'failed' | null>(null)
  const timer = useRef<number | null>(null)
  useEffect(() => () => {
    if (timer.current) window.clearTimeout(timer.current)
  }, [])

  const usingCustom = !(PRESETS as readonly string[]).includes(tag)
  const effective = usingCustom ? cleanTag(custom) : tag
  const url = effective ? `${joinUrl}?src=${effective}` : joinUrl

  const copy = async () => {
    const ok = await copyText(url)
    setCopied(ok ? 'ok' : 'failed')
    if (timer.current) window.clearTimeout(timer.current)
    timer.current = window.setTimeout(() => setCopied(null), 2500)
  }

  const known = [...sources].sort((a, b) => b.members - a.members)

  return (
    <div className="flex flex-col gap-3">
      <div className="flex flex-wrap gap-1.5" role="radiogroup" aria-label="Where the link goes">
        {[...PRESETS, 'custom'].map((p) => {
          const on = p === 'custom' ? usingCustom : tag === p
          return (
            <button
              key={p}
              type="button"
              role="radio"
              aria-checked={on}
              onClick={() => setTag(p === 'custom' ? '' : p)}
              className={cx(
                'h-8 rounded-full px-3 text-sm transition-colors',
                on ? 'bg-brand-wash font-bold text-brand-ink' : 'border border-line-control bg-surface font-medium text-ink hover:bg-canvas',
              )}
            >
              {p === 'custom' ? 'Your own tag' : p}
            </button>
          )
        })}
      </div>
      {usingCustom && (
        <Input
          size="sm"
          value={custom}
          onChange={(e) => setCustom(cleanTag(e.target.value))}
          placeholder="flyer-uni"
          aria-label="Your own tag: letters, digits and dashes"
          maxLength={40}
        />
      )}
      <div className="flex min-w-0 items-center gap-2 rounded-control border border-line-control bg-canvas-2 py-1.5 pl-3 pr-1.5">
        <code className="min-w-0 flex-1 truncate font-mono text-sm text-ink" title={url}>
          {url}
        </code>
        <button
          type="button"
          onClick={() => void copy()}
          className="h-8 flex-none rounded-[8px] px-2.5 text-sm font-bold text-brand-ink hover:bg-brand-wash"
        >
          {copied === 'ok' ? 'Copied' : 'Copy'}
        </button>
      </div>
      <span className="sr-only" role="status">
        {copied === 'ok' ? 'Link copied' : copied === 'failed' ? 'Could not copy; select the link and copy it by hand' : ''}
      </span>
      {copied === 'failed' && <p className="text-sm text-bad-ink">The browser would not copy it. Select the link and copy it by hand.</p>}
      <div>
        <div className="mb-1 text-label font-bold uppercase tracking-[.06em] text-ink-3">Members by tag</div>
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
