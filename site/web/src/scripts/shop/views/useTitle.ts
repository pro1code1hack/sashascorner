import { useEffect } from 'preact/hooks';

/**
 * The tab title for a screen; null is the overview. `site` drops the "Order ahead"
 * middle for screens that are not about the shop itself (the account, an order's status).
 */
export function useTitle(part: string | null, site = false): void {
  useEffect(() => {
    document.title = part ? (site ? `${part} · Sasha's Corner` : `${part} · Order ahead · Sasha's Corner`) : "Order ahead for takeaway · Sasha's Corner, Dundee";
  }, [part, site]);
}
