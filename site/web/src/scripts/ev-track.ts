// Analytics calls from the events and SEO pages, through ./track.ts (the site's one
// Swetrix wrapper). Imported through a glob so these pages still build, and simply
// send nothing, when track.ts is absent. Never pass anything personal.
type TrackFn = (event: string, props?: Record<string, string>) => void;

const mods = import.meta.glob<{ track?: TrackFn }>('./track.ts');

export function track(event: string, props?: Record<string, string>): void {
  const load = mods['./track.ts'];
  if (!load) return;
  load()
    .then((m) => m.track?.(event, props))
    .catch(() => undefined);
}

/** Wire every `a[data-track]` under `root`: data-track is the event name and any
 *  data-track-<key> attributes become its properties. */
export function trackLinks(root: ParentNode = document): void {
  root.querySelectorAll<HTMLAnchorElement>('a[data-track]').forEach((a) => {
    if (a.dataset.trackWired) return;
    a.dataset.trackWired = '1';
    a.addEventListener('click', () => {
      const props: Record<string, string> = { page: location.pathname };
      for (const [k, v] of Object.entries(a.dataset)) {
        if (k.startsWith('track') && k !== 'track' && k !== 'trackWired' && v) {
          props[k.slice(5).replace(/^./, (c) => c.toLowerCase())] = v;
        }
      }
      track(a.dataset.track!, props);
    });
  });
}
