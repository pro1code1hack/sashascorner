// The account island for the site-wide /account page (pages/account.astro): the same
// Account screen the ordering app used at /order/account, mounted on its own. Links
// and `go()` are full page loads here (router.ts only pushes history under /order),
// so "Back to checkout" and "Order again" land in the shop with the basket intact.
import { useEffect } from 'preact/hooks';
import { Account } from './account/Account';
import { isMock } from './api';
import { loadMember } from './member';
import { toast } from './toast';

export default function AccountApp() {
  useEffect(() => {
    void loadMember();
    if (isMock()) console.info('[account] mock mode');
  }, []);
  const t = toast.value;
  return (
    <div class="sh-app" data-view="account">
      <Account />
      <div class="sh-toast-region" aria-live="polite" aria-atomic="true">
        {t && (
          <div class="sh-toast on-dark" key={t.text}>
            <span>{t.text}</span>
            {t.action && <a href={t.action.href}>{t.action.label}</a>}
          </div>
        )}
      </div>
    </div>
  );
}
