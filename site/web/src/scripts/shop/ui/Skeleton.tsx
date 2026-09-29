// Placeholders in the shape of what is coming (a grid of tiles, an item page, a total),
// not a spinner. A quiet shimmer that stops under prefers-reduced-motion (shop.css).
// One live "Loading…" line per skeleton is enough for a screen reader.

interface Props {
  /** What is loading, for the status line: "the menu", "this item". */
  what?: string;
  class?: string;
}

const Bar = ({ w, h = '1em', class: cls }: { w: string; h?: string; class?: string }) => (
  <span class={['sh-skel', cls].filter(Boolean).join(' ')} style={`width:${w};height:${h}`} aria-hidden="true" />
);

/** Status line every skeleton carries once. */
export function Loading({ what = 'the menu' }: Props) {
  return (
    <p class="sr-only" role="status">
      Loading {what}…
    </p>
  );
}

/** The category tile grid (overview). */
export function SkeletonTiles({ count = 8 }: { count?: number }) {
  return (
    <div class="sh-skel-grid sh-cats" aria-hidden="true">
      {Array.from({ length: count }, (_, i) => (
        <span class="sh-skel sh-skel--tile" key={i} />
      ))}
    </div>
  );
}

/** The product grid (category page). */
export function SkeletonCards({ count = 6 }: { count?: number }) {
  return (
    <div class="sh-skel-grid sh-grid" aria-hidden="true">
      {Array.from({ length: count }, (_, i) => (
        <span class="sh-skel-card" key={i}>
          <span class="sh-skel sh-skel--media" />
          <Bar w="80%" h="1.1rem" />
          <Bar w="40%" h="0.9rem" />
        </span>
      ))}
    </div>
  );
}

/** The item page: photo and name on the left, three option groups on the right. */
export function SkeletonItem() {
  return (
    <div class="sh-item" aria-hidden="true">
      <div class="sh-item__info">
        <span class="sh-skel sh-skel--photo" />
        <Bar w="70%" h="2rem" />
        <Bar w="95%" />
        <Bar w="60%" />
      </div>
      <div class="sh-item__options">
        {[3, 6, 4].map((n, i) => (
          <div class="sh-group" key={i}>
            <Bar w="30%" h="1.3rem" />
            <div class="sh-tiles">
              {Array.from({ length: n }, (_, j) => (
                <span class="sh-skel sh-skel--tilebtn" key={j} />
              ))}
            </div>
          </div>
        ))}
      </div>
    </div>
  );
}

/** Basket lines and the total. */
export function SkeletonLines({ count = 2 }: { count?: number }) {
  return (
    <div aria-hidden="true">
      <ul class="sh-lines">
        {Array.from({ length: count }, (_, i) => (
          <li class="sh-line" key={i}>
            <span class="sh-line__main">
              <Bar w="60%" h="1.2rem" />
              <Bar w="40%" h="0.9rem" />
              <Bar w="132px" h="44px" class="sh-skel--pill" />
            </span>
            <Bar w="3.5em" h="1.2rem" />
          </li>
        ))}
      </ul>
      <dl class="sh-total">
        <dt>Total</dt>
        <dd>
          <Bar w="4.5em" h="1.3em" />
        </dd>
      </dl>
    </div>
  );
}
