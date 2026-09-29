/**
 * The Live orders board rings when an order arrives (owner, 2026-09-29): a
 * two-tone chime made with the Web Audio API (no asset, no CDN), three times per
 * ring, then again every 30 s while any order is still NEW, like a till. "Snooze
 * 5 min" silences the repeat without acknowledging anything. The document title
 * flashes "● 2 new orders" while NEW orders wait, and a Notification is shown
 * when the tab is hidden. All of it is best effort and silent when unsupported.
 *
 * Browsers block audio until a person touches the page. The first time, a bar
 * offers "Turn on"; that click resumes the AudioContext, plays a test ding,
 * asks for notification permission and remembers `cafeops.shop.sound = on`
 * (localStorage). On later loads the first pointerdown / keydown re-arms it
 * silently. Which orders have already rung lives in sessionStorage so a reload
 * doesn't ring again for old ones.
 */
import { useCallback, useEffect, useRef, useState } from 'react'
import type { OrderAdmin } from '../../lib/types/shop'

const SOUND_KEY = 'cafeops.shop.sound'
const SEEN_KEY = 'cafeops.shop.seen'
const REPEAT_MS = 30_000
const SNOOZE_MS = 5 * 60 * 1000
/** A NEW order older than this on first load is waiting: ring once for it. */
const WAITING_MS = 2 * 60 * 1000
const TITLE_FLASH_MS = 1200

/* ------------------------------------------------------------- storage --- */

function readPref(): boolean {
  try {
    return localStorage.getItem(SOUND_KEY) === 'on'
  } catch {
    return false
  }
}

function writePref(on: boolean): void {
  try {
    localStorage.setItem(SOUND_KEY, on ? 'on' : 'off')
  } catch {
    /* private mode: the choice lasts this page only */
  }
}

function readSeen(): Set<number> {
  try {
    const raw = sessionStorage.getItem(SEEN_KEY)
    return new Set(raw ? (JSON.parse(raw) as number[]) : [])
  } catch {
    return new Set()
  }
}

function writeSeen(s: Set<number>): void {
  try {
    sessionStorage.setItem(SEEN_KEY, JSON.stringify([...s]))
  } catch {
    /* ignore */
  }
}

/* --------------------------------------------------------------- audio --- */

let ctx: AudioContext | null = null

function audio(): AudioContext | null {
  if (ctx) return ctx
  const Ctor = window.AudioContext ?? (window as unknown as { webkitAudioContext?: typeof AudioContext }).webkitAudioContext
  if (!Ctor) return null
  try {
    ctx = new Ctor()
  } catch {
    return null
  }
  return ctx
}

export function audioState(): AudioContextState | 'none' {
  return ctx ? ctx.state : 'none'
}

/** One tone: a sine at `hz` for `dur` seconds with a quick decay, starting at `at`. */
function tone(c: AudioContext, hz: number, at: number, dur: number, gain = 0.25): void {
  const osc = c.createOscillator()
  const g = c.createGain()
  osc.type = 'sine'
  osc.frequency.setValueAtTime(hz, at)
  g.gain.setValueAtTime(0.0001, at)
  g.gain.exponentialRampToValueAtTime(gain, at + 0.012)
  g.gain.exponentialRampToValueAtTime(0.0001, at + dur)
  osc.connect(g)
  g.connect(c.destination)
  osc.start(at)
  osc.stop(at + dur + 0.02)
}

/** "Ding-dong" (880 Hz then 660 Hz, 0.18 s each), `times` over. */
export function chime(times = 3): boolean {
  const c = audio()
  if (!c || c.state !== 'running') return false
  const t0 = c.currentTime + 0.02
  for (let i = 0; i < times; i++) {
    const base = t0 + i * 0.6
    tone(c, 880, base, 0.18)
    tone(c, 660, base + 0.2, 0.18)
  }
  return true
}

async function arm(): Promise<boolean> {
  const c = audio()
  if (!c) return false
  try {
    if (c.state !== 'running') await c.resume()
  } catch {
    return false
  }
  return c.state === 'running'
}

/* ------------------------------------------------------- notifications --- */

function notify(o: OrderAdmin): void {
  if (typeof Notification === 'undefined' || Notification.permission !== 'granted') return
  const items = o.lines.reduce((n, l) => n + l.qty, 0)
  try {
    const n = new Notification(`New order ${o.code_display} · ${o.customer_name} · ${items} ${items === 1 ? 'item' : 'items'} · ${o.requested_local}`, {
      tag: `shop-order-${o.id}`,
      body: o.lines.map((l) => `${l.qty}× ${l.name}`).join(', '),
    })
    n.onclick = () => {
      window.focus()
      window.location.hash = `#/shop/orders/${o.id}`
      n.close()
    }
  } catch {
    /* unsupported or blocked */
  }
}

/* ---------------------------------------------------------------- hook --- */

export interface ShopAlert {
  /** The person turned sound on (remembered on this device). */
  soundOn: boolean
  /** Sound is on and the browser lets us play (a gesture has happened). */
  armed: boolean
  /** How many orders are NEW right now. */
  newCount: number
  /** The repeat is snoozed until this time (ms), or null. */
  snoozedUntil: number | null
  turnOn: () => Promise<void>
  turnOff: () => void
  snooze: () => void
}

/**
 * Watch the live orders for arrivals. `orders` is the board's polled list
 * (undefined until the first answer); `title` is the page's own document title.
 */
export function useNewOrderAlert(orders: OrderAdmin[] | undefined, title: string): ShopAlert {
  const [soundOn, setSoundOn] = useState<boolean>(readPref)
  const [armed, setArmed] = useState<boolean>(() => audioState() === 'running')
  const [snoozedUntil, setSnoozedUntil] = useState<number | null>(null)
  const [newCount, setNewCount] = useState(0)
  const seeded = useRef(false)
  const lastRing = useRef(0)

  const ring = useCallback(() => {
    if (!readPref()) return
    if (chime(3)) lastRing.current = Date.now()
  }, [])

  // Arrivals: ring once for anything NEW we have not seen this session.
  useEffect(() => {
    if (orders === undefined) return
    const fresh = orders.filter((o) => o.status === 'NEW')
    setNewCount(fresh.length)
    const seen = readSeen()
    if (!seeded.current) {
      seeded.current = true
      const waiting = fresh.some((o) => !seen.has(o.id) && Date.now() - Date.parse(o.placed_at) > WAITING_MS)
      for (const o of fresh) seen.add(o.id)
      writeSeen(seen)
      if (waiting) ring()
      return
    }
    const arrivals = fresh.filter((o) => !seen.has(o.id))
    if (arrivals.length === 0) return
    for (const o of fresh) seen.add(o.id)
    writeSeen(seen)
    ring()
    if (document.hidden) for (const o of arrivals) notify(o)
  }, [orders, ring])

  // Keep ringing every 30 s while anything is NEW, unless snoozed.
  useEffect(() => {
    if (newCount === 0) return
    const t = window.setInterval(() => {
      if (snoozedUntil !== null && Date.now() < snoozedUntil) return
      if (Date.now() - lastRing.current >= REPEAT_MS - 500) ring()
    }, 5_000)
    return () => window.clearInterval(t)
  }, [newCount, snoozedUntil, ring])

  // The snooze ends by itself.
  useEffect(() => {
    if (snoozedUntil === null) return
    const t = window.setTimeout(() => setSnoozedUntil(null), Math.max(0, snoozedUntil - Date.now()))
    return () => window.clearTimeout(t)
  }, [snoozedUntil])

  // Title flash while NEW orders wait.
  useEffect(() => {
    if (newCount === 0) {
      document.title = title
      return
    }
    let on = false
    const flash = () => {
      on = !on
      document.title = on ? `● ${newCount} new ${newCount === 1 ? 'order' : 'orders'}` : title
    }
    flash()
    const t = window.setInterval(flash, TITLE_FLASH_MS)
    return () => {
      window.clearInterval(t)
      document.title = title
    }
  }, [newCount, title])

  // Sound remembered on: the first touch re-arms the context silently.
  useEffect(() => {
    if (!soundOn || armed) return
    const onGesture = () => {
      void arm().then((ok) => {
        if (ok) setArmed(true)
      })
    }
    window.addEventListener('pointerdown', onGesture, { once: true })
    window.addEventListener('keydown', onGesture, { once: true })
    return () => {
      window.removeEventListener('pointerdown', onGesture)
      window.removeEventListener('keydown', onGesture)
    }
  }, [soundOn, armed])

  const turnOn = useCallback(async () => {
    writePref(true)
    setSoundOn(true)
    const ok = await arm()
    setArmed(ok)
    if (ok) chime(1)
    if (typeof Notification !== 'undefined' && Notification.permission === 'default') {
      try {
        await Notification.requestPermission()
      } catch {
        /* unsupported */
      }
    }
  }, [])

  const turnOff = useCallback(() => {
    writePref(false)
    setSoundOn(false)
  }, [])

  const snooze = useCallback(() => setSnoozedUntil(Date.now() + SNOOZE_MS), [])

  return { soundOn, armed, newCount, snoozedUntil, turnOn, turnOff, snooze }
}
