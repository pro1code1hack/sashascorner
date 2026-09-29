// The sign-in wall (owner decision 2026-09-29, like Black Sheep): a Rewards card is
// needed to PLACE an order; browsing and the basket stay open. One calm panel, shown
// by the basket and the checkout when `config.require_account` and no card is on this
// device, and again inline when placing the order answers 401 `sign_in_required`.
import { paths, withKept } from '../router';
import { Button } from '../ui';

export const accountHref = (tab: 'signin' | 'join', next: 'checkout' | null = 'checkout') => withKept(`/account?${next ? `next=${next}&` : ''}tab=${tab}`);

export function SignInWall({ reason, compact }: { reason?: string | null; compact?: boolean }) {
  return (
    <section class={['sh-wall', compact && 'sh-wall--compact'].filter(Boolean).join(' ')} aria-labelledby="sh-wall-title">
      <p class="sh-wall__kicker">Rewards</p>
      <h2 class="sh-wall__title" id="sh-wall-title">
        Sign in to place your order
      </h2>
      {reason && (
        <p class="sh-wall__reason" role="status">
          {reason}
        </p>
      )}
      <p class="sh-wall__why">
        Your account is your Rewards card: stamps on every order, your details remembered, a free drink when you've earned it.
        No password: we send a code to your email or mobile.
      </p>
      <div class="sh-wall__actions">
        <Button tone="caramel" href={accountHref('signin')}>
          Sign in
        </Button>
        <Button tone="ghost" href={accountHref('join')}>
          Create account
        </Button>
      </div>
      <p class="sh-wall__back">
        <a href={paths.overview()}>Back to the menu</a>
        {' · your order stays in the basket.'}
      </p>
    </section>
  );
}
