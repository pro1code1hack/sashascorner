// Promo banners: a scroll-snap strip with dots. Swipe on a phone, dots or Tab on a
// keyboard. Nothing auto-advances (reduced motion, and a moving hero is a distraction).
import { useEffect, useRef, useState } from 'preact/hooks';
import type { Banner } from '../types';
import { Photo } from './Photo';

export function Carousel({ banners }: { banners: Banner[] }) {
  const track = useRef<HTMLDivElement>(null);
  const [active, setActive] = useState(0);
  useEffect(() => {
    const el = track.current;
    if (!el) return;
    let raf = 0;
    const onScroll = () => {
      cancelAnimationFrame(raf);
      raf = requestAnimationFrame(() => {
        const w = el.clientWidth || 1;
        setActive(Math.min(banners.length - 1, Math.max(0, Math.round(el.scrollLeft / w))));
      });
    };
    el.addEventListener('scroll', onScroll, { passive: true });
    return () => {
      el.removeEventListener('scroll', onScroll);
      cancelAnimationFrame(raf);
    };
  }, [banners.length]);
  if (banners.length === 0) return null;
  const goTo = (i: number) => {
    const el = track.current;
    if (!el) return;
    const reduce = matchMedia('(prefers-reduced-motion: reduce)').matches;
    el.scrollTo({ left: i * el.clientWidth, behavior: reduce ? 'auto' : 'smooth' });
    setActive(i);
  };
  return (
    <section class="sh-carousel" aria-roledescription="carousel" aria-label="Offers">
      <div class="sh-carousel__track" ref={track}>
        {banners.map((b, i) => {
          const body = (
            <>
              <Photo src={b.photo_url} alt="" name={b.title} class="sh-carousel__img" eager={i === 0} sizes="(min-width: 900px) 900px, 100vw" />
              <span class="sh-carousel__band">
                <span class="sh-carousel__title">{b.title}</span>
                {b.subtitle && <span class="sh-carousel__sub">{b.subtitle}</span>}
              </span>
            </>
          );
          return (
            <div class="sh-carousel__slide" role="group" aria-roledescription="slide" aria-label={`${i + 1} of ${banners.length}`} key={b.id}>
              {b.link_href ? (
                <a class="sh-carousel__link" href={b.link_href} tabIndex={i === active ? 0 : -1}>
                  {body}
                </a>
              ) : (
                <div class="sh-carousel__link">{body}</div>
              )}
            </div>
          );
        })}
      </div>
      {banners.length > 1 && (
        <div class="sh-carousel__dots" role="tablist" aria-label="Choose a slide">
          {banners.map((b, i) => (
            <button
              key={b.id}
              type="button"
              role="tab"
              aria-selected={i === active}
              aria-label={`Slide ${i + 1}: ${b.title}`}
              class={['sh-carousel__dot', i === active && 'is-on'].filter(Boolean).join(' ')}
              onClick={() => goTo(i)}
            />
          ))}
        </div>
      )}
    </section>
  );
}
