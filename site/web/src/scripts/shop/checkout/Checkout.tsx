// /order/checkout: the allergy notice (once per session), collection time, the
// customer's details (from the Rewards card when signed in), the free drink, a note,
// how to pay, and PLACE ORDER with a total the server quoted (CONTRACT §3.3, §6).
import { useEffect, useMemo, useRef, useState } from 'preact/hooks';
import { session, shopApi, shopError, type ApiResult } from '../api';
import { allergenText, gbp } from '../format';
import { go, paths, route } from '../router';
import { basket, catalogue, config, online, productById } from '../store';
import type { Me, PlaceOrderBody, Quote, SlotsResponse } from '../types';
import { BottomBar, Button, Field, Notice, Sheet } from '../ui';
import {
  CollectionLine,
  EmptyState,
  PageHead,
  SectionLabel,
  Statement,
  accountPath,
  displayCode,
  looksLikeEmail,
  looksLikePhone,
  markPending,
  saveOrderToken,
  useShopData,
} from './common';
import { smsOffered } from './notify';

const ACK_KEY = 'sc.shop.allergy_ack.v1';
const readAck = () => {
  try {
    return sessionStorage.getItem(ACK_KEY) === '1';
  } catch {
    return false;
  }
};
const writeAck = () => {
  try {
    sessionStorage.setItem(ACK_KEY, '1');
  } catch {
    /* private mode: asked again next time, which is fine */
  }
};

type Mode = 'asap' | 'slot';
type Pay = 'counter' | 'online';

export function Checkout() {
  const ready = useShopData();
  const cfg = config.value;
  const cat = catalogue.value;
  const lines = basket.lines.value;
  const dining = basket.dining.value;
  const signedIn = !!session.token();
  const resume = route.value.query.get('resume');

  // ---- allergy notice ----
  const [ack, setAck] = useState(readAck);
  const cont = () => {
    writeAck();
    setAck(true);
  };

  // ---- collection time ----
  const [slots, setSlots] = useState<SlotsResponse | null>(null);
  const [slotsErr, setSlotsErr] = useState<string | null>(null);
  const [date, setDate] = useState<string | undefined>(undefined);
  const [mode, setMode] = useState<Mode>('asap');
  const [slotAt, setSlotAt] = useState<string | null>(null);
  const loadSlots = async (d?: string) => {
    setSlotsErr(null);
    const r = await shopApi.slots(d);
    if (!r.ok || !r.data) {
      setSlotsErr(shopError(r));
      return;
    }
    setSlots(r.data);
    setDate(r.data.date);
    // A time that is gone from the fresh list is no longer chosen.
    if (slotAt && !r.data.slots.some((s) => s.at === slotAt && s.available)) setSlotAt(null);
    if (!r.data.asap.available && mode === 'asap' && r.data.slots.some((s) => s.available)) setMode('slot');
  };
  useEffect(() => {
    void loadSlots();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  // ---- the member ----
  const [me, setMe] = useState<Me | null>(null);
  const [name, setName] = useState('');
  const [contact, setContact] = useState('');
  const [useReward, setUseReward] = useState(false);
  useEffect(() => {
    if (!signedIn) return;
    void shopApi.me().then((r) => {
      if (r.ok && r.data) {
        setMe(r.data);
        setName((n) => n || r.data!.first_name);
        setContact((c) => c || r.data!.phone || r.data!.email || '');
      } else if (r.status === 401) {
        session.clear();
      }
    });
  }, [signedIn]);

  // ---- order updates: email when there is an address, a text only by opt-in ----
  const contactIsPhone = looksLikePhone(contact.trim()) && !looksLikeEmail(contact.trim());
  const contactIsEmail = looksLikeEmail(contact.trim());
  const [smsOptIn, setSmsOptIn] = useState(false);
  const offerSms = smsOffered(cfg) && contactIsPhone;

  // ---- eat in: the table (optional), note, payment ----
  const [table, setTable] = useState('');
  const [note, setNote] = useState('');
  const payOptions = useMemo<Pay[]>(() => {
    if (!cfg) return [];
    const out: Pay[] = [];
    if (cfg.pay.counter) out.push('counter');
    if (cfg.pay.online) out.push('online');
    return out;
  }, [cfg]);
  const [pay, setPay] = useState<Pay | null>(null);
  useEffect(() => {
    if (pay === null && payOptions.length) setPay(payOptions[0]);
  }, [payOptions, pay]);

  // ---- the quote: re-priced by the server on every change ----
  const [quote, setQuote] = useState<Quote | null>(null);
  const [quoteErr, setQuoteErr] = useState<string | null>(null);
  const [quoting, setQuoting] = useState(false);
  const quoteSeq = useRef(0);
  const linesKey = JSON.stringify(lines);
  const wantReward = useReward && !!me?.reward;
  useEffect(() => {
    if (!ready || lines.length === 0) return;
    const seq = ++quoteSeq.current;
    setQuoting(true);
    const t = setTimeout(async () => {
      const r = await shopApi.quote({ dining, lines, reward: wantReward });
      if (seq !== quoteSeq.current) return;
      setQuoting(false);
      if (r.ok && r.data) {
        setQuote(r.data);
        setQuoteErr(null);
      } else {
        setQuoteErr(shopError(r));
      }
    }, 200);
    return () => clearTimeout(t);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [ready, linesKey, dining, wantReward]);

  // ---- placing ----
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const [fieldErr, setFieldErr] = useState<{ name?: string; contact?: string; time?: string }>({});
  const [priceChanged, setPriceChanged] = useState(false);
  const [closedMsg, setClosedMsg] = useState<string | null>(null);

  const validate = (): PlaceOrderBody | null => {
    const fe: typeof fieldErr = {};
    const n = name.trim();
    const c = contact.trim();
    if (!n) fe.name = 'Please add your name: we call it out when the order is ready.';
    let customer: PlaceOrderBody['customer'] | null = null;
    if (!c) fe.contact = 'Please add a mobile number or an email address.';
    else if (looksLikeEmail(c)) customer = { name: n, email: c };
    else if (looksLikePhone(c)) customer = { name: n, phone: c };
    else fe.contact = "That doesn't look like a mobile number or an email address.";
    if (mode === 'slot' && !slotAt) fe.time = 'Please pick a collection time.';
    setFieldErr(fe);
    if (Object.keys(fe).length || !customer || !quote || !pay) return null;
    return {
      dining,
      lines: lines.map((l) => ({ product_id: l.product_id, menu_item_id: l.menu_item_id, qty: l.qty, option_ids: l.option_ids })),
      reward: wantReward,
      asap: mode === 'asap',
      requested_at: mode === 'asap' ? null : slotAt,
      customer,
      ...(note.trim() ? { note: note.trim().slice(0, 300) } : {}),
      ...(dining === 'eat_in' && table.trim() ? { table: table.trim().slice(0, 20) } : {}),
      allergy_ack: true,
      payment: pay,
      expected_total_pence: quote.total_pence,
      website: '',
      ...(offerSms ? { sms_opt_in: smsOptIn } : {}),
    } as PlaceOrderBody;
  };

  const place = async () => {
    setErr(null);
    setPriceChanged(false);
    const body = validate();
    if (!body) {
      document.querySelector<HTMLElement>('.sd-page [aria-invalid="true"], .sd-page .sd-ferr')?.scrollIntoView({ block: 'center' });
      return;
    }
    setBusy(true);
    try {
      const r = await shopApi.placeOrder(body);
      if (r.ok && r.data) {
        const o = r.data;
        saveOrderToken(o.code, o.access_token);
        if (o.payment.checkout_url) {
          // The basket stays until payment lands: a cancelled Stripe page comes back here.
          markPending(o.code);
          if (o.payment.checkout_url.startsWith('/')) go(o.payment.checkout_url, { replace: true });
          else location.assign(o.payment.checkout_url);
          return;
        }
        basket.clear();
        go(`${paths.status(o.code)}?t=${encodeURIComponent(o.access_token)}`, { replace: true });
        return;
      }
      await failed(r);
    } finally {
      setBusy(false);
    }
  };

  const failed = async (r: ApiResult<unknown>) => {
    const code = r.error?.error ?? '';
    if (r.status === 409 && code === 'price_changed') {
      const fresh = await shopApi.quote({ dining, lines, reward: wantReward });
      if (fresh.ok && fresh.data) setQuote(fresh.data);
      setPriceChanged(true);
      setErr(shopError(r));
      return;
    }
    if (r.status === 409 && code === 'slot_full') {
      setSlotAt(null);
      setMode('slot');
      await loadSlots(date);
      setErr(shopError(r));
      return;
    }
    if (r.status === 403 && code === 'shop_closed') {
      setClosedMsg(r.error?.detail || cfg?.closed_message || 'Online ordering is closed right now.');
      return;
    }
    setErr(shopError(r));
  };

  // ---- states before the form ----
  if (!ready) {
    return (
      <div class="sd-page">
        <PageHead back={paths.basket()} backLabel="My order" title="Checkout" />
        <p class="sd-muted" role="status">
          Loading…
        </p>
      </div>
    );
  }
  if (!cfg || !cat) {
    return (
      <div class="sd-page">
        <PageHead back={paths.basket()} backLabel="My order" title="Checkout" />
        <Notice tone="error">Couldn't reach the café's ordering system. Please try again in a minute.</Notice>
      </div>
    );
  }
  if (!cfg.enabled || closedMsg) {
    return (
      <div class="sd-page">
        <PageHead back={paths.overview()} backLabel="Order online" title="Checkout" />
        <EmptyState title="Online ordering is closed" action={<Button href="/menu">See the menu</Button>}>
          <p>{closedMsg ?? cfg.closed_message}</p>
          {cfg.next_open_local && <p>Online orders open again {cfg.next_open_local}.</p>}
        </EmptyState>
      </div>
    );
  }
  if (lines.length === 0) {
    return (
      <div class="sd-page">
        <PageHead back={paths.overview()} backLabel="Order online" title="Checkout" />
        {resume && (
          <Notice tone="info">
            Payment for order {displayCode(resume)} wasn't completed, so it hasn't been placed.
          </Notice>
        )}
        <EmptyState title="Your order is empty" action={<Button tone="caramel" href={paths.overview()}>Choose something</Button>}>
          <p>Add a drink or a dish first; then come back here to pick a collection time.</p>
        </EmptyState>
      </div>
    );
  }

  const asap = slots?.asap;
  const availableSlots = slots?.slots.filter((s) => s.available) ?? [];
  const stampsDone = me ? Math.min(me.stamps_current, me.stamps_required) : 0;
  const total = quote?.total_pence ?? basket.subtotal(cat);
  const blocked = busy || quoting || !quote || !!quoteErr || (quote?.problems.length ?? 0) > 0 || quote?.lines.some((l) => l.problems.length) || !pay || !online.value;
  const quoteLines = quote?.lines.map((l) => {
    const p = productById(cat, l.product_id);
    return { ...l, allergens: p ? allergenText(p) : null };
  });

  return (
    <div class="sd-page sd-page--bar">
      <PageHead back={paths.basket()} backLabel="My order" title="Checkout">
        <CollectionLine />
      </PageHead>

      {resume && (
        <Notice tone="info">
          Payment for order {displayCode(resume)} wasn't completed, so that order hasn't been placed. Your basket is still
          here: place it again when you're ready, or choose to pay at the counter.
        </Notice>
      )}
      {basket.notice.value && <Notice tone="warn" onDismiss={() => (basket.notice.value = null)}>{basket.notice.value}</Notice>}

      {/* ---- collection time ---- */}
      <section class="sd-sec sd-sec--first" aria-labelledby="sd-time">
        <SectionLabel id="sd-time">Collection time</SectionLabel>
        {slotsErr && <Notice tone="error">{slotsErr}</Notice>}
        {slots && !slots.open && (
          <Notice tone="warn">
            {slots.reason ?? 'No collection times today.'}
            {cfg.next_open_local ? ` Online orders open again ${cfg.next_open_local}.` : ''}
          </Notice>
        )}
        {slots && (
          <div class="sd-choices" role="radiogroup" aria-label="When to collect">
            <label class={['sd-choice', mode === 'asap' && 'is-on', !asap?.available && 'is-off'].filter(Boolean).join(' ')}>
              <input type="radio" name="when" checked={mode === 'asap'} disabled={!asap?.available} onChange={() => setMode('asap')} />
              <span class="sd-choice__text">
                <span class="sd-choice__title">As soon as possible</span>
                <span class="sd-choice__sub">{asap?.available ? `Ready at about ${asap.local} today` : 'Not available right now'}</span>
              </span>
              {asap?.available && <span class="sd-choice__aside num">{asap.local}</span>}
            </label>
            <label class={['sd-choice', mode === 'slot' && 'is-on', !availableSlots.length && slots.days.length < 2 && 'is-off'].filter(Boolean).join(' ')}>
              <input
                type="radio"
                name="when"
                checked={mode === 'slot'}
                disabled={!availableSlots.length && slots.days.length < 2}
                onChange={() => setMode('slot')}
              />
              <span class="sd-choice__text">
                <span class="sd-choice__title">Pick a time</span>
                <span class="sd-choice__sub">{slotAt ? `Collect at ${slots.slots.find((s) => s.at === slotAt)?.local ?? ''}` : 'In ten-minute steps'}</span>
              </span>
            </label>
          </div>
        )}
        {slots && mode === 'slot' && (
          <div class="sd-slot-panel">
            {slots.days.length > 1 && (
              <ul class="sd-tabs" role="tablist" aria-label="Day">
                {slots.days.map((d) => (
                  <li key={d} role="presentation">
                    <button type="button" role="tab" class="sd-tab" aria-selected={d === date} onClick={() => void loadSlots(d)}>
                      {dayLabel(d)}
                    </button>
                  </li>
                ))}
              </ul>
            )}
            {availableSlots.length === 0 ? (
              <p class="sd-muted">No times left {slots.days.length > 1 ? 'on this day' : 'today'}.</p>
            ) : (
              <ul class="sd-chips" aria-label="Collection times">
                {slots.slots.map((s) => (
                  <li key={s.at}>
                    <button
                      type="button"
                      class="sd-chip num"
                      aria-pressed={s.at === slotAt}
                      disabled={!s.available}
                      onClick={() => {
                        setSlotAt(s.at);
                        setFieldErr((f) => ({ ...f, time: undefined }));
                      }}
                    >
                      {s.local}
                    </button>
                  </li>
                ))}
              </ul>
            )}
            {fieldErr.time && (
              <p class="sd-ferr" role="alert">
                {fieldErr.time}
              </p>
            )}
          </div>
        )}
      </section>

      {/* ---- your details ---- */}
      <section class="sd-sec" aria-labelledby="sd-you">
        <SectionLabel id="sd-you">Your details</SectionLabel>
        {signedIn && me ? (
          <p class="sd-muted">Signed in with your Rewards card{me.first_name ? `, ${me.first_name}` : ''}. Change anything below if you need to.</p>
        ) : (
          <p class="sd-muted">
            <a class="sd-link" href={accountPath('checkout')}>
              Sign in or join Rewards
            </a>{' '}
            to earn stamps on this order and skip typing next time.
          </p>
        )}
        <div class="sd-form" style="margin-top:14px">
          <div class="sd-two">
            <Field
              label="Name"
              autocomplete="name"
              autocapitalize="words"
              maxLength={80}
              value={name}
              error={fieldErr.name}
              onInput={(e) => setName((e.currentTarget as HTMLInputElement).value)}
            />
            <Field
              label="Mobile or email"
              hint="So we can reach you about the order. Nothing else."
              autocomplete="tel"
              inputMode="email"
              autocapitalize="off"
              spellcheck={false}
              maxLength={254}
              value={contact}
              error={fieldErr.contact}
              onInput={(e) => setContact((e.currentTarget as HTMLInputElement).value)}
            />
          </div>
          {(offerSms || contactIsEmail) && (
            <div class="sd-updates">
              {offerSms && (
                <label class="sd-check">
                  <input type="checkbox" checked={smsOptIn} onChange={(e) => setSmsOptIn((e.currentTarget as HTMLInputElement).checked)} />
                  <span>Text me when it's ready (one message).</span>
                </label>
              )}
              {contactIsEmail && <p class="sd-muted">We'll email you updates about this order.</p>}
            </div>
          )}
        </div>
      </section>

      {/* ---- rewards ---- */}
      {signedIn && me && (
        <section class="sd-sec" aria-labelledby="sd-rw">
          <SectionLabel id="sd-rw">Rewards</SectionLabel>
          <div class="sd-rw">
            <div class="sd-rw__head">
              <span class="sd-rw__name">{cfg.loyalty.program_name}</span>
              <span class="sd-rw__count">
                <span class="num">{me.stamps_current}</span> of <span class="num">{me.stamps_required}</span> stamps
              </span>
            </div>
            <ul class="sd-stamps" aria-label={`${me.stamps_current} of ${me.stamps_required} stamps`}>
              {Array.from({ length: me.stamps_required }, (_, i) => (
                <li key={i} class={i < stampsDone ? 'is-on' : undefined} />
              ))}
            </ul>
            {me.reward ? (
              <label class="sd-toggle">
                <span class="sd-toggle__text">
                  <strong>Use my free drink</strong>
                  <small>
                    {me.reward.text}
                    {wantReward && quote && !quote.reward.applied ? ` — ${quote.reward.text ?? 'no eligible drink in this order'}` : ''}
                  </small>
                </span>
                <input type="checkbox" role="switch" checked={useReward} aria-checked={useReward} onChange={(e) => setUseReward((e.currentTarget as HTMLInputElement).checked)} />
              </label>
            ) : (
              <p class="sd-muted">Drinks in this order add stamps to your card once it's collected.</p>
            )}
          </div>
        </section>
      )}

      {/* ---- eat in: table ---- */}
      {dining === 'eat_in' && (
        <section class="sd-sec" aria-labelledby="sd-table">
          <SectionLabel id="sd-table">Eating in</SectionLabel>
          <div class="sd-two">
            <Field
              label="Table number (optional)"
              hint="If you're already sitting down, we'll bring it over."
              inputMode="numeric"
              autocomplete="off"
              maxLength={20}
              value={table}
              onInput={(e) => setTable((e.currentTarget as HTMLInputElement).value)}
            />
          </div>
        </section>
      )}

      {/* ---- note ---- */}
      <section class="sd-sec" aria-labelledby="sd-note">
        <SectionLabel id="sd-note">Note to the barista</SectionLabel>
        <Field label="Anything we should know? (optional)" id="sd-note-in">
          <textarea
            id="sd-note-in"
            class="sh-input sd-textarea"
            maxLength={300}
            value={note}
            onInput={(e) => setNote((e.currentTarget as HTMLTextAreaElement).value)}
          />
        </Field>
      </section>

      {/* ---- payment ---- */}
      <section class="sd-sec" aria-labelledby="sd-pay">
        <SectionLabel id="sd-pay">Payment</SectionLabel>
        {payOptions.length === 0 ? (
          <Notice tone="error">We can't take payment for online orders right now. Please order at the counter.</Notice>
        ) : (
          <div class="sd-choices" role="radiogroup" aria-label="How to pay">
            {payOptions.includes('counter') && (
              <label class={['sd-choice', pay === 'counter' && 'is-on'].filter(Boolean).join(' ')}>
                <input type="radio" name="pay" checked={pay === 'counter'} onChange={() => setPay('counter')} />
                <span class="sd-choice__text">
                  <span class="sd-choice__title">Pay at the counter</span>
                  <span class="sd-choice__sub">Card or cash when you collect.</span>
                </span>
              </label>
            )}
            {payOptions.includes('online') && (
              <label class={['sd-choice', pay === 'online' && 'is-on'].filter(Boolean).join(' ')}>
                <input type="radio" name="pay" checked={pay === 'online'} onChange={() => setPay('online')} />
                <span class="sd-choice__text">
                  <span class="sd-choice__title">Pay now by card</span>
                  <span class="sd-choice__sub">You'll be taken to a secure payment page, then back here.</span>
                </span>
              </label>
            )}
          </div>
        )}
      </section>

      {/* ---- the order ---- */}
      <section class="sd-sec" aria-labelledby="sd-order">
        <SectionLabel id="sd-order">Your order · {dining === 'eat_in' ? 'eat in' : 'takeaway'}</SectionLabel>
        {quoteErr && <Notice tone="error">{quoteErr}</Notice>}
        {quote && quote.problems.length > 0 && (
          <Notice tone="warn">
            {quote.problems.join(' ')}{' '}
            <a class="sd-link" href={paths.basket()}>
              Change the order
            </a>
          </Notice>
        )}
        {quote && quoteLines ? (
          <Statement
            lines={quoteLines}
            subtotal={quote.subtotal_pence}
            discount={quote.discount_pence}
            total={quote.total_pence}
            discountText={quote.reward.applied ? quote.reward.text ?? 'Free drink' : null}
            gbp={gbp}
          />
        ) : (
          <div aria-busy="true">
            <p class="sr-only" role="status">
              Pricing your order…
            </p>
            <div class="sd-stmt" aria-hidden="true">
              <ul class="sd-stmt__lines">
                {lines.map((l, i) => (
                  <li key={i}>
                    <span class="sd-stmt__qty num">{l.qty}×</span>
                    <span class="sd-stmt__name">
                      <span class="sh-skel" style="width:60%;height:1em" />
                    </span>
                    <span class="sh-skel" style="width:3.5em;height:1em" />
                  </li>
                ))}
              </ul>
              <dl class="sd-stmt__sums">
                <dt class="sd-stmt__total">Total</dt>
                <dd class="sd-stmt__total">
                  <span class="sh-skel" style="width:4.5em;height:1.2em" />
                </dd>
              </dl>
            </div>
          </div>
        )}
        <p class="sd-muted" style="margin-top:14px">
          By placing an order you agree to our{' '}
          <a class="sd-link" href={cfg.terms_url}>
            terms
          </a>
          . Prices are those at the counter today.
        </p>
      </section>

      {err && (
        <Notice tone="error" onDismiss={() => setErr(null)}>
          {err}
        </Notice>
      )}

      <BottomBar label="Place your order">
        <div class="sd-bar">
          <div class="sd-bar__sum">
            <small>{!online.value ? 'Offline' : priceChanged ? 'New total' : quoting ? 'Updating…' : 'Total'}</small>
            <span class="sd-bar__total num">{gbp(total)}</span>
          </div>
          <Button tone="caramel" busy={busy} disabled={!!blocked} onClick={() => void place()}>
            {priceChanged ? 'Confirm and place order' : 'Place order'}
          </Button>
        </div>
      </BottomBar>

      <Sheet
        open={!ack}
        onClose={() => {
          if (!readAck()) go(paths.basket());
        }}
        title="Any allergies or intolerances?"
        wide
        actions={
          <Button tone="caramel" block onClick={cont}>
            Continue
          </Button>
        }
      >
        <p style="white-space:pre-line">{cfg.allergen_notice}</p>
      </Sheet>
    </div>
  );
}

/** "Today", "Tomorrow", else "Wed 30 Sep" for a YYYY-MM-DD in the café's own calendar. */
function dayLabel(ymd: string): string {
  const [y, m, d] = ymd.split('-').map(Number);
  const target = new Date(y, m - 1, d);
  const today = new Date();
  today.setHours(0, 0, 0, 0);
  const diff = Math.round((target.getTime() - today.getTime()) / 86_400_000);
  if (diff === 0) return 'Today';
  if (diff === 1) return 'Tomorrow';
  return new Intl.DateTimeFormat('en-GB', { weekday: 'short', day: 'numeric', month: 'short' }).format(target);
}

export default Checkout;
