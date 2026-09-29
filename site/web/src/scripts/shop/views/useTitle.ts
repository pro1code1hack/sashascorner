import { useEffect } from 'preact/hooks';

/** The tab title for a screen; null is the overview. */
export function useTitle(part: string | null): void {
  useEffect(() => {
    document.title = part ? `${part} · Order ahead · Sasha's Corner` : "Order ahead for takeaway · Sasha's Corner, Dundee";
  }, [part]);
}
