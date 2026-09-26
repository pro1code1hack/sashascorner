// Speech bubbles over the café's people.
//
// A bubble belongs to a speaker (anything with a head in the 3D scene). Every
// frame the scene projects each speaker's head to screen space, the same way it
// places the pins, and this module puts the bubble above it.
//
// Rules: never more than two bubbles; a conversation is a short script played on
// a "channel" (the counter, or the room), one line at a time, so two channels
// can never show more than two bubbles. A bubble that would sit on a pin is
// nudged, and if no nudge clears it, it waits hidden until the pin is clear. The
// clock is the scene's, so everything pauses when the model is off screen.
import './diorama.css';

export interface Speaker {
  /** Head position in screen px (relative to the host), or null when not showable. */
  where(): { x: number; y: number } | null;
  staff?: boolean;
}

type Line = [Speaker, string];
interface Bubble {
  el: HTMLDivElement;
  inner: HTMLSpanElement;
  speaker: Speaker;
  w: number;
  h: number;
  life: number; // seconds left
  shown: boolean;
}
interface Script {
  lines: Line[];
  i: number;
  wait: number; // pause before the next line
  bubble: Bubble | null;
  done?: () => void;
}

export const MAX_BUBBLES = 2;
const GAP = 0.45;

export class Talk {
  private layer: HTMLDivElement;
  private channels = new Map<string, Script>();
  private live: Bubble[] = [];

  constructor(host: HTMLElement) {
    this.layer = document.createElement('div');
    this.layer.className = 'dio-talk';
    this.layer.setAttribute('aria-hidden', 'true');
    // Before the pins in document order, so a pin always paints over a bubble.
    const pins = host.querySelector('.diorama__pins');
    host.insertBefore(this.layer, pins);
  }

  busy(channel: string) {
    return this.channels.has(channel);
  }

  /** Play a script on a channel. `force` replaces whatever the channel was saying. */
  play(channel: string, lines: Line[], opts: { force?: boolean; done?: () => void } = {}) {
    const cur = this.channels.get(channel);
    if (cur && !opts.force) return false;
    if (cur) this.drop(cur);
    this.channels.set(channel, { lines, i: 0, wait: 0, bubble: null, done: opts.done });
    return true;
  }

  /** A bubble that just stays (reduced motion). */
  pin(speaker: Speaker, text: string) {
    this.channels.set('static', { lines: [[speaker, text]], i: 0, wait: 0, bubble: null });
    const b = this.make(speaker, text, Infinity);
    this.channels.get('static')!.bubble = b;
  }

  private make(speaker: Speaker, text: string, life: number): Bubble {
    const el = document.createElement('div');
    el.className = 'dio-bubble' + (speaker.staff ? ' dio-bubble--staff' : '');
    const inner = document.createElement('span');
    inner.className = 'dio-bubble__in';
    inner.textContent = text;
    el.appendChild(inner);
    el.style.visibility = 'hidden';
    this.layer.appendChild(el);
    const b: Bubble = { el, inner, speaker, w: inner.offsetWidth, h: inner.offsetHeight, life, shown: false };
    el.style.visibility = '';
    this.live.push(b);
    return b;
  }

  private kill(b: Bubble) {
    b.el.classList.remove('is-on');
    this.live = this.live.filter((o) => o !== b);
    setTimeout(() => b.el.remove(), 320);
  }

  private drop(s: Script) {
    if (s.bubble) this.kill(s.bubble);
    s.bubble = null;
  }

  /** Advance the scripts by `dt` seconds. */
  tick(dt: number) {
    for (const [key, s] of this.channels) {
      if (s.bubble) {
        s.bubble.life -= dt;
        if (s.bubble.life > 0) continue;
        this.drop(s);
        s.i++;
        s.wait = GAP;
      }
      if (s.i >= s.lines.length) {
        this.channels.delete(key);
        s.done?.();
        continue;
      }
      s.wait -= dt;
      if (s.wait > 0 || this.live.length >= MAX_BUBBLES) continue;
      const [who, text] = s.lines[s.i];
      s.bubble = this.make(who, text, Math.min(4.4, Math.max(2.2, 1.1 + text.length * 0.05)));
    }
  }

  /**
   * Place every bubble over its speaker. `avoid` are the pins' boxes in host px
   * (the hot one already padded); a bubble that can't clear them hides.
   */
  place(w: number, h: number, avoid: DOMRect[]) {
    for (const b of this.live) {
      const p = b.speaker.where();
      let ok = !!p && p.x > 0 && p.x < w && p.y > 0 && p.y < h;
      let left = 0;
      let top = 0;
      if (p && ok) {
        ok = false;
        for (const [dx, dy] of NUDGES) {
          left = Math.min(w - 6 - b.w, Math.max(6, p.x - b.w / 2 + dx));
          top = p.y - b.h - 10 + dy;
          if (top < 4) continue;
          if (avoid.some((r) => left < r.right && left + b.w > r.left && top < r.bottom && top + b.h > r.top)) continue;
          ok = true;
          break;
        }
      }
      if (ok && p) {
        b.el.style.transform = `translate3d(${left.toFixed(1)}px, ${top.toFixed(1)}px, 0)`;
        b.inner.style.setProperty('--tail', `${Math.min(b.w - 14, Math.max(14, p.x - left)).toFixed(0)}px`);
      }
      if (ok !== b.shown) {
        b.shown = ok;
        b.el.classList.toggle('is-on', ok);
      }
    }
  }

  get count() {
    return this.live.length;
  }

  destroy() {
    this.layer.remove();
  }
}

// Where to try a bubble, relative to straight above the head: as is, a little
// higher, to either side, then higher still.
const NUDGES: Array<[number, number]> = [
  [0, 0],
  [0, -26],
  [-70, -6],
  [70, -6],
  [-110, -30],
  [110, -30],
];
