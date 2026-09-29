// /order/status/<code>: the confirmation and the live status. The bearer arrives once
// as `?t=` (the confirmation link) and is kept on this device under the code, so the
// page survives a reload; without it the order is not shown. Polls every 20 s while
// the order is still moving and the tab is visible (CONTRACT §6).
import { useEffect, useRef, useState } from 'preact/hooks';
import { shopApi, shopError } from '../api';
import { gbp } from '../format';
import { paths, route } from '../router';
import { basket, config } from '../store';
import { member, signedIn } from '../member';
import { accountHref } from '../account/SignInWall';
import { useTitle } from '../views/useTitle';
import type { OrderView } from '../types';
import { Button, Notice, Sheet } from '../ui';
import { EmptyState, PageHead, SectionLabel, Statement, displayCode, loadOrderToken, localTime, saveOrderToken, takePending, useShopData } from '../checkout/common';
import { currentSubscription, iosNeedsInstall, pushConfig, pushSupported, subscribeToOrder, unsubscribeFromOrder, type OrderViewX } from '../checkout/notify';

const POLL_MS = 20_000;
const STEPS = ['Received', 'Accepted', 'Being made', 'Ready', 'Collected'];
const FINAL = new Set(['COLLECTED', 'CANCELLED', 'REJECTED']);

type State = 'loading' | 'ok' | 'notoken' | 'notfound' | 'error';

export function Status() {
  useShopData();
  const code = route.value.params.code ?? '';
  useTitle(`Order ${displayCode(code)}`, true);
  const [paid] = useState(() => route.value.query.get('paid') === '1');
  const [token] = useState<string | null>(() => {
    const t = route.value.query.get('t');
    if (t) saveOrderToken(code, t);
    return t ?? loadOrderToken(code);
  });
  const [order, setOrder] = useState<OrderView | null>(null);
  const [state, setState] = useState<State>(token ? 'loading' : 'notoken');
  const [err, setErr] = useState<string | null>(null);
  const [confirm, setConfirm] = useState(false);
  const [cancelling, setCancelling] = useState(false);
  const timer = useRef<number | null>(null);

  // The token and the paid flag never stay in the address bar (or the history).
  useEffect(() => {
    const url = new URL(location.href);
    if (url.searchParams.has('t') || url.searchParams.has('paid')) {
      url.searchParams.delete('t');
      url.searchParams.delete('paid');
      history.replaceState(null, '', url.pathname + url.search + url.hash);
    }
  }, []);

  const load = async () => {
    if (!token) return;
    const r = await shopApi.order(code, token);
    if (r.ok && r.data) {
      setOrder(r.data);
      setState('ok');
      setErr(null);
      // An online-paid order clears the basket once payment has landed.
      if (paid || (r.data.status !== 'PENDING_PAYMENT' && takePending(code))) basket.clear();
      return;
    }
    if (r.status === 401 || r.status === 403 || r.status === 404) {
      setState('notfound');
      return;
    }
    if (!order) setState('error');
    setErr(shopError(r));
  };

  useEffect(() => {
    void load();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [token, code]);

  // Poll while the order is moving and the page is in view.
  const status = order?.status;
  useEffect(() => {
    const stop = () => {
      if (timer.current !== null) clearInterval(timer.current);
      timer.current = null;
    };
    const start = () => {
      stop();
      if (!status || FINAL.has(status) || document.visibilityState !== 'visible') return;
      timer.current = window.setInterval(() => void load(), POLL_MS);
    };
    const onVis = () => {
      if (document.visibilityState === 'visible') void load();
      start();
    };
    start();
    document.addEventListener('visibilitychange', onVis);
    return () => {
      stop();
      document.removeEventListener('visibilitychange', onVis);
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [status]);

  const cancel = async () => {
    if (!token) return;
    setCancelling(true);
    try {
      const r = await shopApi.cancel(code, token);
      if (r.ok && r.data) {
        setOrder(r.data);
        setConfirm(false);
      } else {
        setErr(shopError(r));
        setConfirm(false);
        void load();
      }
    } finally {
      setCancelling(false);
    }
  };

  const shown = displayCode(code);

  // ---- push: "tell me when it's ready" on this device ----
  const push = pushConfig(config.value);
  const [pushState, setPushState] = useState<'idle' | 'busy' | 'on' | 'denied' | 'failed'>('idle');
  useEffect(() => {
    if (!push) return;
    if (!pushSupported()) {
      console.info('[shop] push: not supported here' + (iosNeedsInstall() ? ' (iOS: install to Home Screen first)' : ''));
      return;
    }
    void currentSubscription().then((sub) => {
      if (sub) setPushState('on');
    });
  }, [push]);
  const notify = (order as OrderViewX | null)?.notify ?? null;
  useEffect(() => {
    if (notify?.push_subscribed && pushState === 'idle') setPushState('on');
  }, [notify?.push_subscribed, pushState]);
  const turnOn = async () => {
    if (!push || !token) return;
    setPushState('busy');
    const r = await subscribeToOrder(code, token, push);
    if (r.ok) setPushState('on');
    else {
      console.info('[shop] push subscribe:', r.reason, r.detail ?? '');
      setPushState(r.reason === 'denied' ? 'denied' : 'failed');
    }
  };
  const turnOff = async () => {
    if (!token) return;
    setPushState('busy');
    const r = await unsubscribeFromOrder(code, token);
    setPushState(r.ok ? 'idle' : 'on');
  };

  if (state === 'notoken' || state === 'notfound') {
    return (
      <div class="sd-page">
        <PageHead back={paths.overview()} backLabel="Order online" title="We can't show this order" />
        <EmptyState
          title={shown}
          action={
            <>
              <Button tone="caramel" href={paths.overview()}>
                Order online
              </Button>
              <Button tone="ghost" href={paths.account()}>
                Your account
              </Button>
            </>
          }
        >
          <p>
            {state === 'notoken'
              ? "This order isn't saved on this device. Open the link from your confirmation on the phone you ordered with, or ask at the counter with your name."
              : "That link has expired or doesn't match an order. Ask at the counter with your name and we'll find it."}
          </p>
        </EmptyState>
      </div>
    );
  }
  if (state === 'loading' || (state === 'error' && !order)) {
    return (
      <div class="sd-page">
        <PageHead back={paths.overview()} backLabel="Order online" title="Your order" />
        <p class="sd-code num">{shown}</p>
        {state === 'error' ? (
          <Notice tone="error">
            {err}{' '}
            <button type="button" class="sd-linkbtn" onClick={() => void load()}>
              Try again
            </button>
          </Notice>
        ) : (
          <p class="sd-muted" role="status">
            Loading…
          </p>
        )}
      </div>
    );
  }
  if (!order) return null;

  const off = order.status === 'CANCELLED' || order.status === 'REJECTED';
  const pending = order.status === 'PENDING_PAYMENT';
  const step = Math.max(0, Math.min(4, order.status_step));
  const when = order.requested_local || localTime(order.requested_at, true);
  const payWords = paymentWords(order);
  const cafe = config.value?.cafe;

  return (
    <div class="sd-page">
      <PageHead back={paths.overview()} backLabel="Order online" title={off ? 'Order ' + (order.status === 'CANCELLED' ? 'cancelled' : 'not accepted') : pending ? 'Almost there' : 'Thanks, ' + order.customer.name.split(' ')[0] + '.'}>
        <p class="sd-lede">
          {off
            ? order.status === 'CANCELLED'
              ? 'This order was cancelled. Nothing is owed unless it was already paid, in which case we refund it.'
              : "We couldn't accept this order. If you paid online we refund it; ask at the counter if you'd like to know more."
            : pending
              ? paid
                ? 'Payment received. We are confirming it with the till, which takes a moment.'
                : 'Your payment has not come through yet. Finish paying, or the order lapses after 30 minutes.'
              : 'Your order is in. Show this code or say your name at the counter.'}
        </p>
      </PageHead>

      {paid && !pending && <Notice tone="info">Payment received. Thank you.</Notice>}
      {err && (
        <Notice tone="error" onDismiss={() => setErr(null)}>
          {err}
        </Notice>
      )}

      <p class="sd-code num" aria-label={`Order code ${shown}`}>
        {shown}
      </p>

      <section class="sd-sec" aria-label="Progress">
        {off || pending ? (
          <p class={['sd-state', off && 'sd-state--off'].filter(Boolean).join(' ')} role="status">
            {order.status_label}
          </p>
        ) : (
          <>
            <p class="sd-state" role="status">
              {order.status_label}
            </p>
            <ol class="sd-steps" aria-label={`Step ${step + 1} of 5: ${STEPS[step]}`} style="margin-top:14px">
              {STEPS.map((s, i) => (
                <li key={s} class={['sd-step', i < step && 'is-done', i === step && 'is-now'].filter(Boolean).join(' ')} aria-current={i === step ? 'step' : undefined}>
                  <span>{s}</span>
                </li>
              ))}
            </ol>
            {!FINAL.has(order.status) && <p class="sd-poll" style="margin-top:12px">Updates by itself while your order is being made.</p>}
          </>
        )}
        {notify && (notify.email || notify.sms || notify.push_subscribed) && (
          <p class="sd-muted" style="margin-top:10px">
            Updates: {[notify.email && 'email', notify.sms && 'text', notify.push_subscribed && 'this device'].filter(Boolean).join(' · ')}
          </p>
        )}
        {push && !FINAL.has(order.status) && !pending && (
          <div class="sd-push" style="margin-top:14px">
            {pushSupported() ? (
              pushState === 'on' ? (
                <div class="sd-push__row">
                  <span>You'll get a notification on this device when it's ready.</span>
                  <button type="button" class="sd-linkbtn" onClick={() => void turnOff()}>
                    Undo
                  </button>
                </div>
              ) : (
                <div class="sd-push__row">
                  <Button tone="ghost" size="sm" busy={pushState === 'busy'} onClick={() => void turnOn()}>
                    Get a notification when it's ready
                  </Button>
                  {pushState === 'denied' && <span class="sd-muted">Notifications are blocked for this site in your browser settings.</span>}
                  {pushState === 'failed' && <span class="sd-muted">Couldn't set that up on this device. The page still updates by itself.</span>}
                </div>
              )
            ) : iosNeedsInstall() ? (
              <p class="sd-muted">Add this page to your Home Screen to get notifications when it's ready.</p>
            ) : null}
          </div>
        )}
      </section>

      <section class="sd-sec" aria-labelledby="sd-when">
        <SectionLabel id="sd-when">Collection</SectionLabel>
        <div class="sd-when">
          <span class="sd-when__big">{order.asap ? `As soon as it's ready · about ${when}` : when}</span>
          <span class="sd-muted">
            {order.dining === 'eat_in'
              ? order.table
                ? `Table ${order.table} · eat in`
                : 'Eat in · collect from the counter'
              : 'Takeaway / collection from the counter, not delivery'}
          </span>
        </div>
        <p style="margin-top:12px">
          {order.collection_note || `Collect from ${cafe ? `${cafe.address_line}, ${cafe.postcode}` : '23 Commercial Street, DD1 3DD'}. Say your name or show the order code.`}
        </p>
        {signedIn.value && member.value ? (
          <p class="sd-muted" style="margin-top:10px">
            Signed in as {member.value.first_name} · stamps added when you collect
          </p>
        ) : !signedIn.value && config.value && !config.value.require_account ? (
          <p class="sd-muted" style="margin-top:10px">
            <a class="sd-link" href={accountHref('join', null)}>
              Create an account
            </a>{' '}
            to earn stamps on your next order.
          </p>
        ) : null}
      </section>

      <section class="sd-sec" aria-labelledby="sd-lines">
        <SectionLabel id="sd-lines">Your order</SectionLabel>
        <Statement lines={order.lines} subtotal={order.subtotal_pence} discount={order.discount_pence} total={order.total_pence} discountText="Free drink" gbp={gbp} />
        <dl class="sd-kv" style="margin-top:14px">
          <dt>Payment</dt>
          <dd>{payWords}</dd>
          <dt>Placed</dt>
          <dd class="num">{localTime(order.placed_at, true)}</dd>
        </dl>
      </section>

      {order.cancel_allowed && (
        <section class="sd-sec">
          <div class="sd-actions">
            <Button tone="ghost" onClick={() => setConfirm(true)}>
              Cancel this order
            </Button>
            <span class="sd-muted">You can cancel until we start making it.</span>
          </div>
        </section>
      )}

      {order.events.length > 0 && (
        <section class="sd-sec" aria-labelledby="sd-events">
          <SectionLabel id="sd-events">What happened</SectionLabel>
          <ol class="sd-events">
            {order.events.map((e, i) => (
              <li key={i}>
                <time class="num" dateTime={e.at}>
                  {localTime(e.at)}
                </time>
                <span>{eventWords(e.kind, e.detail)}</span>
              </li>
            ))}
          </ol>
        </section>
      )}

      <Sheet
        open={confirm}
        onClose={() => setConfirm(false)}
        title="Cancel this order?"
        actions={
          <>
            <Button tone="ghost" onClick={() => setConfirm(false)} disabled={cancelling}>
              Keep it
            </Button>
            <Button tone="caramel" onClick={() => void cancel()} busy={cancelling}>
              Yes, cancel
            </Button>
          </>
        }
      >
        <p>
          {shown} will be cancelled and nothing made.{up(order.payment.status) === 'PAID' ? ' You paid by card: we refund it, which can take a few days to show.' : ''}
        </p>
      </Sheet>
    </div>
  );
}

// The live API spells these in lower case ("unpaid"); the contract in upper. Accept both.
const up = (s: string) => String(s ?? '').toUpperCase();

function paymentWords(o: OrderView): string {
  const method = up(o.payment.method);
  const status = up(o.payment.status);
  if (status === 'REFUNDED') return 'Refunded';
  if (status === 'FAILED') return 'Card payment failed';
  if (method === 'ONLINE') return status === 'PAID' ? 'Paid by card' : 'Card payment pending';
  return status === 'PAID' ? 'Paid at the counter' : 'Pay at the counter when you collect';
}

function eventWords(kind: string, detail: string | null): string {
  const words: Record<string, string> = {
    placed: 'Order placed',
    paid: 'Payment received',
    accepted: 'Accepted by the café',
    preparing: 'Being made',
    ready: 'Ready to collect',
    collected: 'Collected',
    cancelled: 'Cancelled',
    rejected: 'Not accepted',
    note: 'Note from the café',
    notified: 'Message sent',
    stamped: 'Stamps added to your card',
    sale_recorded: 'Recorded at the till',
  };
  const w = words[kind] ?? kind.replace(/_/g, ' ');
  return detail && (kind === 'note' || kind === 'cancelled' || kind === 'rejected') ? `${w}: ${detail}` : w;
}

export default Status;
