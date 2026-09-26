/**
 * The shared kit. Every screen is built from these so seven screens look like
 * one product rather than seven.
 *
 * The surface language is measured from finsepa.com, not invented: zinc-950
 * ground, zinc-900 cards, zinc-800 for anything raised, one blue accent, and
 * green/red reserved for states the backend has actually classified. Borrowed
 * because a borrowed system is coherent -- someone already did the work of
 * making it so.
 *
 * Rules that matter more than they look:
 *  - Numbers go through <Fig>. Tabular figures are what make a column scannable.
 *  - Money arrives as integer pence or an exact decimal string of pence; it is
 *    formatted at the edge and never arithmetic'd in the browser.
 *  - A missing value is `null`, never 0. <Fig missing> renders the reason in
 *    place of the number, because a zero meaning "unknown" is the flattering
 *    kind of wrong.
 *  - Column headings are sentence case. Tracked-out capitals are a decoration
 *    that costs legibility at 11px.
 */
import type { ReactNode } from 'react'

/* ---------------------------------------------------------------- layout */

export function Card({
  title,
  subtitle,
  right,
  children,
  pad = true,
  className = '',
}: {
  title?: ReactNode
  subtitle?: ReactNode
  right?: ReactNode
  children: ReactNode
  pad?: boolean
  className?: string
}) {
  return (
    <section
      className={`bg-surface border border-line rounded-surface shadow-card ${className}`}
    >
      {(title || right) && (
        <header className="flex items-start justify-between gap-4 px-5 pt-4 pb-3">
          <div className="min-w-0">
            {title && (
              <h2 className="text-[1.125rem] font-bold text-ink leading-[28px] tracking-[-0.015em]">{title}</h2>
            )}
            {subtitle && <p className="text-[0.75rem] text-ink-4 mt-1">{subtitle}</p>}
          </div>
          {right && <div className="shrink-0">{right}</div>}
        </header>
      )}
      <div className={pad ? 'px-5 pt-1 pb-5' : ''}>{children}</div>
    </section>
  )
}

export function PageHeader({
  title,
  lede,
  right,
}: {
  title: string
  lede?: ReactNode
  right?: ReactNode
}) {
  return (
    <div className="flex flex-wrap items-end justify-between gap-4 mb-7">
      <div className="min-w-0">
        <h1 className="text-[1.5rem] font-bold tracking-[-0.025em] text-ink leading-[32px]">
          {title}
        </h1>
        {lede && <p className="text-ink-4 mt-1.5 max-w-2xl text-[0.875rem]">{lede}</p>}
      </div>
      {right && <div className="shrink-0">{right}</div>}
    </div>
  )
}

/** A small grey heading above a group of cards. Sentence case, never tracked caps. */
export function SectionLabel({ children, right }: { children: ReactNode; right?: ReactNode }) {
  return (
    // `right` wraps beneath at narrow widths rather than staying `shrink-0` and
    // clipping: a long meta line ("Largest first, as the API sorts them") lost its
    // tail at 375px, and a sentence cut mid-word is worse than one on its own row.
    <div className="flex flex-wrap items-baseline justify-between gap-x-4 gap-y-1 mb-3 mt-8 first:mt-0">
      <h2 className="text-[0.75rem] font-semibold text-ink-3 min-w-0">{children}</h2>
      {right && <div className="text-[0.6875rem] text-ink-4 min-w-0">{right}</div>}
    </div>
  )
}

/** A wide table or panel scrolls inside itself rather than off the page. */
export function ScrollX({ children }: { children: ReactNode }) {
  return <div className="scroll-x -mx-5 px-5">{children}</div>
}

/* ----------------------------------------------------------------- stats */

export function Stat({
  label,
  value,
  sub,
  tone = 'plain',
}: {
  label: string
  value: ReactNode
  sub?: ReactNode
  tone?: Tone
}) {
  return (
    <div className="min-w-0">
      <div className="text-[0.6875rem] font-medium text-ink-4">{label}</div>
      <div
        className={`fig text-[1.25rem] font-semibold leading-[16px] mt-1.5 tracking-[-0.02em] ${toneText(tone)}`}
      >
        {value}
      </div>
      {sub && <div className="text-[0.75rem] text-ink-4 mt-1 leading-[16px]">{sub}</div>}
    </div>
  )
}

export function StatRow({ children }: { children: ReactNode }) {
  return <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-4">{children}</div>
}

/**
 * The signature card: label, figure, an optional delta pill on the same line,
 * and an optional sparkline beneath. Each is its own card rather than cells in
 * one panel, which is what makes a row of them scan.
 */
export function StatCard({
  label,
  value,
  delta,
  sub,
  tone = 'plain',
  spark,
  sparkTone,
  missing,
}: {
  label: string
  value?: ReactNode
  delta?: ReactNode
  sub?: ReactNode
  tone?: Tone
  spark?: number[]
  sparkTone?: Tone
  /**
   * Why there is no figure. Renders the reason IN PLACE of the number, and
   * suppresses the delta pill and sparkline -- a trend beside an absent value is
   * a trend in what, exactly.
   *
   * Here rather than in each caller because three screens had written the same
   * wrapper to get invariant-8 behaviour onto a card, and a rule re-implemented
   * per screen is a rule one screen will eventually get wrong.
   */
  missing?: string
}) {
  if (missing !== undefined) {
    return (
      <div className="bg-surface border border-line rounded-card-lg px-4 py-3.5 min-w-0">
        <div className="text-[0.6875rem] font-medium text-ink-4 truncate">{label}</div>
        <div className="mt-1.5">
          <Fig missing={missing} />
        </div>
        {sub && <div className="text-[0.6875rem] text-ink-4 mt-1.5 leading-[16px]">{sub}</div>}
      </div>
    )
  }
  return (
    <div className="bg-surface border border-line rounded-card-lg px-4 py-3.5 min-w-0">
      <div className="text-[0.6875rem] font-medium text-ink-4 truncate">{label}</div>
      <div className="flex items-baseline gap-2 mt-1.5 flex-wrap">
        <span
          className={`fig text-[1.25rem] font-semibold leading-none tracking-[-0.02em] ${toneText(tone)}`}
        >
          {value}
        </span>
        {delta}
      </div>
      {sub && <div className="text-[0.6875rem] text-ink-4 mt-1.5 leading-[16px]">{sub}</div>}
      {spark && spark.length > 1 && (
        <div className="mt-3">
          <Sparkline values={spark} tone={sparkTone ?? tone} />
        </div>
      )}
    </div>
  )
}

/**
 * A signed change in a tinted pill. `value` is pre-formatted -- this does no
 * arithmetic and no rounding, because money and percentages are exact upstream.
 */
export function DeltaPill({ value, tone }: { value: ReactNode; tone: Tone }) {
  const s: Record<Tone, string> = {
    plain: 'bg-raised text-ink-2',
    ok: 'bg-ok-wash text-ok-ink',
    warn: 'bg-warn-wash text-warn-ink',
    bad: 'bg-bad-wash text-bad-ink',
    info: 'bg-info-wash text-info-ink',
    muted: 'bg-raised text-ink-4',
  }
  return (
    <span
      className={`fig inline-flex items-center rounded-chip px-1.5 py-0.5 text-[0.6875rem] font-medium whitespace-nowrap ${s[tone]}`}
    >
      {value}
    </span>
  )
}

/**
 * A shape, not a figure. Deliberately unlabelled and unreadable to the penny:
 * it shows direction and volatility, and the number beside it is the thing you
 * actually read. Flat input renders a flat line rather than dividing by zero.
 */
export function Sparkline({
  values,
  tone = 'plain',
  w = 120,
  h = 28,
}: {
  values: number[]
  tone?: Tone
  w?: number
  h?: number
}) {
  if (values.length < 2) return null
  const min = Math.min(...values)
  const max = Math.max(...values)
  const span = max - min
  const x = (i: number) => (i / (values.length - 1)) * w
  const y = (v: number) => (span === 0 ? h / 2 : h - ((v - min) / span) * (h - 2) - 1)
  const line = values.map((v, i) => `${i === 0 ? 'M' : 'L'}${x(i).toFixed(1)},${y(v).toFixed(1)}`).join(' ')
  const area = `${line} L${w},${h} L0,${h} Z`
  const stroke: Record<Tone, string> = {
    plain: 'var(--color-ink-3)',
    ok: 'var(--color-ok)',
    warn: 'var(--color-warn)',
    bad: 'var(--color-bad)',
    info: 'var(--color-info)',
    muted: 'var(--color-ink-5)',
  }
  const id = `sg-${tone}-${values.length}-${Math.round(min)}-${Math.round(max)}`
  return (
    <svg
      viewBox={`0 0 ${w} ${h}`}
      width="100%"
      height={h}
      preserveAspectRatio="none"
      aria-hidden="true"
      className="block"
    >
      <defs>
        <linearGradient id={id} x1="0" y1="0" x2="0" y2="1">
          <stop offset="0%" stopColor={stroke[tone]} stopOpacity="0.22" />
          <stop offset="100%" stopColor={stroke[tone]} stopOpacity="0" />
        </linearGradient>
      </defs>
      <path d={area} fill={`url(#${id})`} />
      <path d={line} fill="none" stroke={stroke[tone]} strokeWidth="1.5" strokeLinejoin="round" />
    </svg>
  )
}

/* ------------------------------------------------------------- semantics */

export type Tone = 'plain' | 'ok' | 'warn' | 'bad' | 'info' | 'muted'

/** The ink for a tone, with no chrome around it. */
export function toneText(t: Tone) {
  return {
    plain: 'text-ink',
    ok: 'text-ok-ink',
    warn: 'text-warn-ink',
    bad: 'text-bad-ink',
    info: 'text-info-ink',
    muted: 'text-ink-4',
  }[t]
}

export function Badge({ tone = 'plain', children }: { tone?: Tone; children: ReactNode }) {
  const styles: Record<Tone, string> = {
    plain: 'bg-raised text-ink-2',
    ok: 'bg-ok-wash text-ok-ink',
    warn: 'bg-warn-wash text-warn-ink',
    bad: 'bg-bad-wash text-bad-ink',
    info: 'bg-info-wash text-info-ink',
    muted: 'bg-raised text-ink-4',
  }
  return (
    <span
      className={`inline-flex items-center gap-1 rounded-chip px-2 py-0.5 text-[0.6875rem] font-medium whitespace-nowrap ${styles[tone]}`}
    >
      {children}
    </span>
  )
}

/** A name from the data, set as a chip so a list of them scans as a set. */
export function Chip({ children, tone = 'plain' }: { children: ReactNode; tone?: Tone }) {
  const s: Record<Tone, string> = {
    plain: 'bg-raised text-ink-2 border-line-2',
    ok: 'bg-ok-wash text-ok-ink border-ok/20',
    warn: 'bg-warn-wash text-warn-ink border-warn/20',
    bad: 'bg-bad-wash text-bad-ink border-bad/20',
    info: 'bg-info-wash text-info-ink border-info/20',
    muted: 'bg-raised text-ink-4 border-line',
  }
  return (
    <span
      className={`inline-flex items-center rounded-chip border px-2 py-[3px] text-[0.6875rem] ${s[tone]}`}
    >
      {children}
    </span>
  )
}

/** A short explanation attached to a number, in the ink of its severity. */
export function Note({ tone = 'muted', children }: { tone?: Tone; children: ReactNode }) {
  const c: Record<Tone, string> = {
    plain: 'text-ink-2',
    ok: 'text-ok-ink',
    warn: 'text-warn-ink',
    bad: 'text-bad-ink',
    info: 'text-info-ink',
    muted: 'text-ink-4',
  }
  return <p className={`text-[0.75rem] leading-[16px] ${c[tone]}`}>{children}</p>
}

/**
 * A bordered block with a coloured left rule, for a paragraph the backend wrote
 * that must not be skimmed past. Four screens hand-rolled this; it lives here now.
 */
export function Panel({
  tone = 'plain',
  title,
  right,
  children,
}: {
  tone?: Tone
  title?: ReactNode
  right?: ReactNode
  children: ReactNode
}) {
  const s: Record<Tone, string> = {
    plain: 'border-line bg-sunk border-l-ink-5',
    ok: 'border-ok/20 bg-ok-wash border-l-ok',
    warn: 'border-warn/20 bg-warn-wash border-l-warn',
    bad: 'border-bad/25 bg-bad-wash border-l-bad',
    info: 'border-info/20 bg-info-wash border-l-info',
    muted: 'border-line bg-sunk border-l-line-2',
  }
  return (
    <div className={`rounded-card-lg border border-l-2 px-4 py-3 ${s[tone]}`}>
      {(title || right) && (
        <div className="flex items-baseline justify-between gap-3 mb-1.5">
          {title && (
            <div className={`text-[1.125rem] font-semibold leading-[28px] ${toneText(tone)}`}>
              {title}
            </div>
          )}
          {right && <div className="shrink-0 text-[0.6875rem] text-ink-4">{right}</div>}
        </div>
      )}
      <div className="text-[0.75rem] text-ink-2 leading-[20px]">{children}</div>
    </div>
  )
}

/* ------------------------------------------------------------- the number */

export function Fig({
  children,
  tone = 'plain',
  size = 'base',
  missing,
  className = '',
}: {
  children?: ReactNode
  tone?: Tone
  size?: 'sm' | 'base' | 'lg' | 'xl'
  /** Why there is no number. Renders in place of it -- never beside it. */
  missing?: string
  className?: string
}) {
  const sizes = {
    sm: 'text-[0.75rem]',
    base: 'text-[0.875rem]',
    lg: 'text-[1rem] font-semibold',
    xl: 'text-[1.25rem] font-semibold tracking-[-0.02em]',
  }
  if (missing !== undefined) {
    return (
      <span className={`text-ink-5 text-[0.75rem] italic ${className}`} title={missing}>
        — {missing}
      </span>
    )
  }
  return <span className={`fig ${sizes[size]} ${toneText(tone)} ${className}`}>{children}</span>
}

/* -------------------------------------------------------------- controls */

/** Horizontal tabs. The active one is a filled pill, as on the reference. */
export function Tabs<T extends string>({
  tabs,
  active,
  onChange,
}: {
  tabs: { key: T; label: ReactNode; count?: number }[]
  active: T
  onChange: (k: T) => void
}) {
  return (
    <div className="flex flex-wrap items-center gap-1" role="tablist">
      {tabs.map((t) => {
        const on = t.key === active
        return (
          <button
            key={t.key}
            type="button"
            role="tab"
            aria-selected={on}
            onClick={() => onChange(t.key)}
            className={`rounded-control px-3 py-1.5 text-[0.75rem] font-medium transition-colors ${
              on ? 'bg-raised text-ink' : 'text-ink-4 hover:text-ink-2 hover:bg-surface'
            }`}
          >
            {t.label}
            {t.count !== undefined && (
              <span className={`fig ml-1.5 ${on ? 'text-ink-3' : 'text-ink-5'}`}>{t.count}</span>
            )}
          </button>
        )
      })}
    </div>
  )
}

export function Button({
  children,
  onClick,
  variant = 'default',
  disabled,
  type = 'button',
}: {
  children: ReactNode
  onClick?: () => void
  variant?: 'default' | 'primary' | 'danger' | 'ghost'
  disabled?: boolean
  type?: 'button' | 'submit'
}) {
  const v = {
    default: 'bg-raised border-line-2 text-ink hover:bg-line-2',
    primary: 'bg-brand border-brand text-white hover:bg-brand-2',
    danger: 'bg-bad border-bad text-white hover:opacity-90',
    ghost: 'bg-transparent border-transparent text-ink-3 hover:bg-raised hover:text-ink',
  }[variant]
  return (
    <button
      type={type}
      onClick={onClick}
      disabled={disabled}
      className={`inline-flex items-center gap-1 rounded-control border px-3 py-1.5 text-[0.75rem] font-medium transition-colors disabled:opacity-40 disabled:cursor-not-allowed ${v}`}
    >
      {children}
    </button>
  )
}

/** A command the user is meant to run. Selectable, because the artifact sandbox
 *  and plain http both make `navigator.clipboard` unreliable. */
export function CopyBlock({ children }: { children: string }) {
  return (
    <code className="block select-all rounded-control border border-line bg-sunk px-3 py-2 font-mono text-[0.6875rem] text-ink-2 overflow-x-auto whitespace-pre">
      {children}
    </code>
  )
}

/* ------------------------------------------------------------- feedback */

export function Empty({ title, children }: { title: string; children?: ReactNode }) {
  return (
    <div className="text-center py-12 px-6">
      <p className="font-medium text-ink">{title}</p>
      {children && (
        <div className="text-[0.875rem] text-ink-4 mt-2 max-w-md mx-auto leading-[20px]">
          {children}
        </div>
      )}
    </div>
  )
}

export function Loading({ what = 'Loading' }: { what?: string }) {
  return <div className="py-12 text-center text-ink-4 text-[0.875rem]">{what}…</div>
}

export function ErrorBox({ error }: { error: unknown }) {
  const msg = error instanceof Error ? error.message : String(error)
  return (
    <div className="rounded-card-lg border border-bad/25 bg-bad-wash p-4">
      <p className="font-medium text-bad-ink">That did not load.</p>
      <p className="text-[0.75rem] text-ink-2 mt-1 break-words">{msg}</p>
      <p className="text-[0.75rem] text-ink-4 mt-2">
        If every screen is blank, the API is probably refusing to serve: it fails closed
        without <code className="fig">CAFEOPS_API_PASSWORD</code>.
      </p>
    </div>
  )
}

/* --------------------------------------------------------------- tables */

export function Table({ children }: { children: ReactNode }) {
  // Row hover is on the table, not on each <tr>: a hover class per row is a class
  // per row to forget. `[&_tbody_tr:hover]` reaches every body row and leaves the
  // header alone.
  return (
    <table className="w-full text-left border-collapse [&_tbody_tr:hover]:bg-raised/40 [&_tbody_tr]:transition-colors">
      {children}
    </table>
  )
}

export function Th({
  children,
  align = 'left',
  className = '',
  ...rest
}: {
  children?: ReactNode
  align?: 'left' | 'right'
  className?: string
} & React.ThHTMLAttributes<HTMLTableCellElement>) {
  return (
    <th
      {...rest}
      className={`text-[0.6875rem] font-medium text-ink-4 pb-2 pt-1 px-3 whitespace-nowrap border-b border-line ${
        align === 'right' ? 'text-right' : ''
      } ${className}`}
    >
      {children}
    </th>
  )
}

/** A sortable column head. `dir` drives `aria-sort` as well as the caret. */
export function SortTh({
  children,
  align = 'left',
  dir = null,
  onSort,
}: {
  children?: ReactNode
  align?: 'left' | 'right'
  dir?: 'asc' | 'desc' | null
  onSort?: () => void
}) {
  return (
    <Th align={align} aria-sort={dir === 'asc' ? 'ascending' : dir === 'desc' ? 'descending' : 'none'}>
      <button
        type="button"
        onClick={onSort}
        className={`inline-flex items-center gap-1 hover:text-ink-2 transition-colors ${
          dir ? 'text-ink-2' : ''
        } ${align === 'right' ? 'flex-row-reverse' : ''}`}
      >
        {children}
        <span className="text-[0.625rem] leading-none">
          {dir === 'asc' ? '▲' : dir === 'desc' ? '▼' : '↕'}
        </span>
      </button>
    </Th>
  )
}

export function Td({
  children,
  align = 'left',
  className = '',
  ...rest
}: {
  children?: ReactNode
  align?: 'left' | 'right'
  className?: string
} & React.TdHTMLAttributes<HTMLTableCellElement>) {
  return (
    <td
      {...rest}
      className={`py-2.5 px-3 border-b border-line align-top text-[0.75rem] ${
        align === 'right' ? 'text-right' : ''
      } ${className}`}
    >
      {children}
    </td>
  )
}

/**
 * A name with a quieter second line beneath it, as in the reference's first
 * table column. Three screens hand-rolled this before it lived here.
 *
 * `wrap` turns truncation off. Reach for it whenever the text is DATA rather
 * than a label: an ellipsis inside an ingredient name or a shelf-life window is
 * data loss, and the data law outranks the component. When truncation is right,
 * the full string is still carried in `title` so it is recoverable.
 */
export function Cell({
  top,
  sub,
  wrap = false,
  title,
}: {
  top: ReactNode
  sub?: ReactNode
  wrap?: boolean
  title?: string
}) {
  const clip = wrap ? 'break-words' : 'truncate'
  return (
    <div className="min-w-0">
      <div
        className={`text-ink text-[0.875rem] leading-[16px] ${clip}`}
        title={title ?? (typeof top === 'string' ? top : undefined)}
      >
        {top}
      </div>
      {sub && (
        <div
          className={`text-ink-5 text-[0.6875rem] leading-[16px] mt-0.5 ${clip}`}
          title={typeof sub === 'string' ? sub : undefined}
        >
          {sub}
        </div>
      )}
    </div>
  )
}

/**
 * Underline tabs. Two tab styles earn their keep and they mean different things:
 * these switch WHAT a section is about (the reference's Stocks/Crypto/Indices
 * row, and swetrix's Country/Region/City inside a card header), while the pill
 * `Tabs` above filter a list that stays the same thing. Using one style for both
 * loses that distinction.
 */
export function TabsUnderline<T extends string>({
  tabs,
  active,
  onChange,
  size = 'base',
}: {
  tabs: { key: T; label: ReactNode }[]
  active: T
  onChange: (k: T) => void
  size?: 'sm' | 'base'
}) {
  return (
    <div
      className="scroll-x flex items-center gap-5 border-b border-line -mb-px"
      role="tablist"
    >
      {tabs.map((t) => {
        const on = t.key === active
        return (
          <button
            key={t.key}
            type="button"
            role="tab"
            aria-selected={on}
            onClick={() => onChange(t.key)}
            className={`relative whitespace-nowrap border-b-2 pb-2 transition-colors ${
              size === 'sm' ? 'text-[0.75rem]' : 'text-[0.875rem]'
            } ${
              on
                ? 'border-ink text-ink font-medium'
                : 'border-transparent text-ink-4 hover:text-ink-2'
            }`}
          >
            {t.label}
          </button>
        )
      })}
    </div>
  )
}

/** A bordered square icon button, as in the reference's top bar. */
export function IconButton({
  children,
  onClick,
  label,
  active = false,
}: {
  children: ReactNode
  onClick?: () => void
  label: string
  active?: boolean
}) {
  return (
    <button
      type="button"
      onClick={onClick}
      aria-label={label}
      title={label}
      className={`inline-flex h-8 w-8 shrink-0 items-center justify-center rounded-control border text-[0.875rem] transition-colors ${
        active
          ? 'border-line-2 bg-raised text-ink'
          : 'border-line bg-surface text-ink-4 hover:text-ink hover:border-line-2'
      }`}
    >
      {children}
    </button>
  )
}

/** A keyboard hint chip, as on the reference's search field. */
export function Kbd({ children }: { children: ReactNode }) {
  return (
    <kbd className="rounded border border-line-2 bg-raised px-1.5 py-0.5 font-mono text-[0.6875rem] text-ink-4">
      {children}
    </kbd>
  )
}
