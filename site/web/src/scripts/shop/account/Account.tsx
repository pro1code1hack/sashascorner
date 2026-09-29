// /order/account: the customer's account IS the Rewards card (CONTRACT §0, §4). Two
// tabs like the reference -- Sign in (email or mobile, then a one-time code) and Join
// Rewards (the loyalty join form) -- and, once a card is on this device, the member's
// stamps and recent orders. No password anywhere. The card id + token land in the
// same localStorage slot the rewards web card uses (scripts/rewards/api.ts), so a
// member who joined on /rewards is signed in here and vice-versa.
import { useEffect, useState } from 'preact/hooks';
import { api, humanError } from '../../rewards/api';
import type { JoinResult } from '../../rewards/card';
import { session, shopApi, shopError } from '../api';
import { gbp } from '../format';
import { go, paths, route } from '../router';
import { basket, catalogue, config, listNames, reorder } from '../store';
import { showToast } from '../toast';
import type { Me, OrderStatus, RecentOrder } from '../types';
import { Button, Field, Notice } from '../ui';
import { EMAIL_RE, EmptyState, PageHead, SectionLabel, displayCode, loadOrderToken, localTime, looksLikePhone, useShopData } from '../checkout/common';

const MONTHS = ['January', 'February', 'March', 'April', 'May', 'June', 'July', 'August', 'September', 'October', 'November', 'December'];
type Tab = 'signin' | 'join';

export function Account() {
  useShopData();
  const cfg = config.value;
  const q = route.value.query;
  const next = q.get('next') === 'checkout' ? 'checkout' : null;
  const [tab, setTab] = useState<Tab>(q.get('tab') === 'join' || location.hash === '#join' ? 'join' : 'signin');
  const [token, setToken] = useState<string | null>(session.token());
  const [me, setMe] = useState<Me | null>(null);
  const [meErr, setMeErr] = useState<string | null>(null);
  const [joined, setJoined] = useState<JoinResult | null>(null);
  const [prefill, setPrefill] = useState<{ contact: string; note: string } | null>(null);

  useEffect(() => {
    if (!token) {
      setMe(null);
      return;
    }
    setMeErr(null);
    void shopApi.me().then((r) => {
      if (r.ok && r.data) setMe(r.data);
      else if (r.status === 401) {
        session.clear();
        setToken(null);
      } else setMeErr(shopError(r));
    });
  }, [token]);

  const signedIn = (cardId: string, tok: string) => {
    session.set(cardId, tok);
    setToken(tok);
    if (next === 'checkout') go(paths.checkout());
  };
  const signOut = () => {
    session.clear();
    setToken(null);
    setMe(null);
    setJoined(null);
  };

  const program = cfg?.loyalty;

  return (
    <div class="sd-page">
      <PageHead back={next ? paths.checkout() : paths.overview()} backLabel={next ? 'Checkout' : 'Order online'} title={token ? 'Your account' : 'Account'} />

      <div class="sd-panel on-dark">
        <p class="sd-panel__kicker">{program?.program_name ?? "Sasha's Corner Rewards"}</p>
        <h2 class="sd-panel__title">
          {token ? (
            <>
              Your card, <span class="sd-soft">your stamps.</span>
            </>
          ) : (
            <>
              Join Rewards <span class="sd-soft">and order online.</span>
            </>
          )}
        </h2>
        <p class="sd-panel__text">
          {program
            ? `${program.stamps_required} stamps and your next drink is free: ${program.reward_text.replace(/\.$/, '').toLowerCase()}. Stamps count online too, once an order is collected.`
            : 'Collect stamps at the till and online. No password: your email or mobile number gets your card back.'}
        </p>
      </div>

      {token ? (
        <SignedIn me={me} err={meErr} joined={joined} onSignOut={signOut} next={next} />
      ) : (
        <>
          <div class="sd-acctabs" role="tablist" aria-label="Sign in or join">
            <button type="button" role="tab" id="sd-tab-signin" aria-selected={tab === 'signin'} aria-controls="sd-pane-signin" class="sd-acctab" onClick={() => setTab('signin')}>
              Sign in
            </button>
            <button type="button" role="tab" id="sd-tab-join" aria-selected={tab === 'join'} aria-controls="sd-pane-join" class="sd-acctab" onClick={() => setTab('join')}>
              Join Rewards
            </button>
          </div>
          {tab === 'signin' ? (
            <div id="sd-pane-signin" role="tabpanel" aria-labelledby="sd-tab-signin">
              <SignIn prefill={prefill} onDone={(r) => signedIn(r.card_id, r.token)} onJoinInstead={() => setTab('join')} />
            </div>
          ) : (
            <div id="sd-pane-join" role="tabpanel" aria-labelledby="sd-tab-join">
              <Join
                onDone={(r) => {
                  setJoined(r);
                  signedIn(r.card_id, r.token);
                }}
                onExists={(contact, note) => {
                  setPrefill({ contact, note });
                  setTab('signin');
                }}
                onSignInInstead={() => setTab('signin')}
              />
            </div>
          )}
        </>
      )}
    </div>
  );
}

// ---- signed in ------------------------------------------------------------------
const STATUS_WORDS: Record<OrderStatus, string> = {
  PENDING_PAYMENT: 'Waiting for payment',
  NEW: 'Received',
  ACCEPTED: 'Accepted',
  PREPARING: 'Being made',
  READY: 'Ready to collect',
  COLLECTED: 'Collected',
  CANCELLED: 'Cancelled',
  REJECTED: 'Not accepted',
};

function SignedIn({ me, err, joined, onSignOut, next }: { me: Me | null; err: string | null; joined: JoinResult | null; onSignOut: () => void; next: 'checkout' | null }) {
  const cardHref = me ? `/c/${encodeURIComponent(me.card_id)}` : joined?.web_card_url ?? null;
  const cat = catalogue.value;
  const [reorderNote, setReorderNote] = useState<string | null>(null);
  // "Order again": the order's lines back through the basket against today's menu.
  const again = (o: RecentOrder) => {
    if (!cat || !o.lines?.length) return;
    const r = reorder(cat, o.lines);
    // The server's own verdict counts too: a line it could not map is one we never saw.
    const unknown = Math.max(r.unknown, o.reorder_complete === false && r.leftOut.length === 0 && r.unknown === 0 ? 1 : 0);
    const bits: string[] = [];
    if (r.leftOut.length) bits.push(`${listNames(r.leftOut)} ${r.leftOut.length === 1 ? 'is' : 'are'} not available today and ${r.leftOut.length === 1 ? 'was' : 'were'} left out.`);
    if (unknown) bits.push(`${unknown === 1 ? 'One item' : `${unknown} items`} from that order ${unknown === 1 ? 'is' : 'are'} no longer on the menu.`);
    if (r.added === 0) {
      setReorderNote(`Nothing from ${displayCode(o.code)} can be ordered today. ${bits.join(' ')}`);
      return;
    }
    if (r.changed.length) bits.push(`${listNames(r.changed)}: an option has changed, so please check it.`);
    if (bits.length) basket.notice.value = bits.join(' ');
    else showToast(`${displayCode(o.code)} added to your order.`);
    go(paths.basket());
  };
  return (
    <div class="sd-me" style="margin-top:24px">
      {joined && (
        <Notice tone="info">
          Your card is ready{me?.first_name ? `, ${me.first_name}` : ''}. Save it somewhere you'll find it at the till: the first stamp comes with your next drink.
        </Notice>
      )}
      {joined && (joined.apple_pass_url || joined.google_save_url) && (
        <div class="sd-wallet">
          {joined.apple_pass_url && (
            <Button href={joined.apple_pass_url} data-native="" size="sm">
              Add to Apple Wallet
            </Button>
          )}
          {joined.google_save_url && (
            <Button href={joined.google_save_url} data-native="" size="sm">
              Save to Google Wallet
            </Button>
          )}
        </div>
      )}
      {err && <Notice tone="error">{err}</Notice>}
      {me ? (
        <>
          <p class="sd-me__hello">Hello, {me.first_name}.</p>
          <div class="sd-rw">
            <div class="sd-rw__head">
              <span class="sd-rw__name">{me.reward ? 'Free drink ready' : 'Your stamps'}</span>
              <span class="sd-rw__count">
                <span class="num">{me.stamps_current}</span> of <span class="num">{me.stamps_required}</span>
              </span>
            </div>
            <ul class="sd-stamps" aria-label={`${me.stamps_current} of ${me.stamps_required} stamps`}>
              {Array.from({ length: me.stamps_required }, (_, i) => (
                <li key={i} class={i < Math.min(me.stamps_current, me.stamps_required) ? 'is-on' : undefined} />
              ))}
            </ul>
            {me.reward ? <p>{me.reward.text}. Use it at checkout, or show your card at the till.</p> : <p class="sd-muted">Every drink you collect adds a stamp.</p>}
          </div>
          <div class="sd-actions">
            <Button tone="caramel" href={next === 'checkout' ? paths.checkout() : paths.overview()}>
              {next === 'checkout' ? 'Back to checkout' : 'Start an order'}
            </Button>
            {cardHref && (
              <a class="sd-link" href={cardHref} data-native="">
                Open my card
              </a>
            )}
          </div>
          <section aria-labelledby="sd-recent">
            <SectionLabel id="sd-recent">Recent orders</SectionLabel>
            {reorderNote && (
              <Notice tone="warn" onDismiss={() => setReorderNote(null)}>
                {reorderNote}
              </Notice>
            )}
            {me.recent_orders.length === 0 ? (
              <p class="sd-muted">No online orders yet.</p>
            ) : (
              <ul class="sd-orders">
                {me.recent_orders.map((o) => {
                  const t = loadOrderToken(o.code);
                  const canReorder = !!cat && !!o.lines?.length;
                  return (
                    <li key={o.code}>
                      <span class="num">{t ? <a href={`${paths.status(o.code)}`}>{displayCode(o.code)}</a> : displayCode(o.code)}</span>
                      <span class="sd-orders__meta">
                        {localTime(o.placed_at, true)} · {STATUS_WORDS[String(o.status).toUpperCase() as OrderStatus] ?? o.status}
                        {o.lines?.length ? ` · ${o.lines.reduce((n, l) => n + l.qty, 0)} ${o.lines.reduce((n, l) => n + l.qty, 0) === 1 ? 'item' : 'items'}` : ''}
                      </span>
                      <span class="num">{gbp(o.total_pence)}</span>
                      {canReorder && (
                        <span class="sd-orders__again">
                          <Button tone="ghost" size="sm" onClick={() => again(o)} aria-label={`Order ${displayCode(o.code)} again`}>
                            Order again
                          </Button>
                        </span>
                      )}
                    </li>
                  );
                })}
              </ul>
            )}
          </section>
        </>
      ) : !err ? (
        <p class="sd-muted" role="status">
          Loading your card…
        </p>
      ) : null}
      <div class="sd-actions">
        <button type="button" class="sd-linkbtn" onClick={onSignOut}>
          Sign out on this device
        </button>
      </div>
    </div>
  );
}

// ---- sign in: contact -> code -----------------------------------------------------
function SignIn({ prefill, onDone, onJoinInstead }: { prefill: { contact: string; note: string } | null; onDone: (r: JoinResult) => void; onJoinInstead: () => void }) {
  const [step, setStep] = useState<'contact' | 'code' | 'staff'>('contact');
  const [contact, setContact] = useState(prefill?.contact ?? '');
  const [code, setCode] = useState('');
  const [sent, setSent] = useState('');
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const [ferr, setFerr] = useState<string | null>(null);

  const send = async (e: Event) => {
    e.preventDefault();
    setErr(null);
    const c = contact.trim();
    if (!EMAIL_RE.test(c) && !looksLikePhone(c)) {
      setFerr('Enter the email address or mobile number you joined with.');
      return;
    }
    setFerr(null);
    setBusy(true);
    try {
      const r = await api<{ delivery: 'email' | 'sms' | 'ask_staff' }>('POST', '/api/loyalty/recover', { body: { contact: c, website: '' } });
      if (!r.ok || !r.data) {
        setErr(humanError(r));
        return;
      }
      if (r.data.delivery === 'ask_staff') {
        setStep('staff');
        return;
      }
      setSent(
        r.data.delivery === 'sms'
          ? 'If that number has a card, we have just texted it a code. It lasts 10 minutes.'
          : 'If that email address has a card, we have just emailed it a code. It lasts 10 minutes; check your junk folder too.',
      );
      setCode('');
      setStep('code');
    } finally {
      setBusy(false);
    }
  };

  const verify = async (e: Event) => {
    e.preventDefault();
    setErr(null);
    const c = code.replace(/\s/g, '');
    if (!/^\d{4,8}$/.test(c)) {
      setFerr('Enter the code from the message: numbers only.');
      return;
    }
    setFerr(null);
    setBusy(true);
    try {
      const r = await api<JoinResult>('POST', '/api/loyalty/recover/verify', { body: { contact: contact.trim(), code: c } });
      if (r.ok && r.data) {
        onDone(r.data);
        return;
      }
      if (r.status === 400 && r.error?.error === 'bad_code') {
        setFerr("That code didn't work. It may have expired: codes last 10 minutes.");
        return;
      }
      setErr(humanError(r));
    } finally {
      setBusy(false);
    }
  };

  if (step === 'staff') {
    return (
      <EmptyState title="Ask us at the till" action={<button type="button" class="sd-linkbtn" onClick={() => setStep('contact')}>Try a different email or number</button>}>
        <p>We can't message that contact from here. Show this screen at the counter and we'll send your card to your phone; your stamps come with it.</p>
      </EmptyState>
    );
  }
  if (step === 'code') {
    return (
      <form class="sd-form" onSubmit={(e) => void verify(e)} noValidate>
        <p>{sent}</p>
        <Field
          label="Code"
          class="sd-code-field"
          inputMode="numeric"
          autocomplete="one-time-code"
          pattern="[0-9]*"
          maxLength={8}
          value={code}
          error={ferr}
          onInput={(e) => setCode((e.currentTarget as HTMLInputElement).value)}
        />
        {err && <p class="sd-err" role="alert">{err}</p>}
        <div class="sd-actions">
          <Button tone="caramel" type="submit" busy={busy}>
            Sign in
          </Button>
          <button type="button" class="sd-linkbtn" onClick={() => setStep('contact')}>
            Use a different email or number
          </button>
        </div>
      </form>
    );
  }
  return (
    <form class="sd-form" onSubmit={(e) => void send(e)} noValidate>
      {prefill?.note && <Notice tone="info">{prefill.note}</Notice>}
      <p class="sd-muted">No password. We send a one-time code to the email address or mobile number on your card.</p>
      <Field
        label="Email or mobile number"
        autocomplete="email"
        autocapitalize="off"
        spellcheck={false}
        maxLength={254}
        value={contact}
        error={ferr}
        onInput={(e) => setContact((e.currentTarget as HTMLInputElement).value)}
      />
      {err && <p class="sd-err" role="alert">{err}</p>}
      <div class="sd-actions">
        <Button tone="caramel" type="submit" busy={busy}>
          Send me a code
        </Button>
        <button type="button" class="sd-linkbtn" onClick={onJoinInstead}>
          I'm new: join instead
        </button>
      </div>
    </form>
  );
}

// ---- join: the loyalty join form ------------------------------------------------------
function Join({ onDone, onExists, onSignInInstead }: { onDone: (r: JoinResult) => void; onExists: (contact: string, note: string) => void; onSignInInstead: () => void }) {
  const [first, setFirst] = useState('');
  const [via, setVia] = useState<'email' | 'phone'>('email');
  const [contact, setContact] = useState('');
  const [day, setDay] = useState('');
  const [month, setMonth] = useState('');
  const [terms, setTerms] = useState(false);
  const [marketing, setMarketing] = useState(false);
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const [fe, setFe] = useState<{ first?: string; contact?: string; birthday?: string; terms?: string }>({});

  const submit = async (e: Event) => {
    e.preventDefault();
    setErr(null);
    const errs: typeof fe = {};
    const f = first.trim();
    const c = contact.trim();
    if (!f) errs.first = 'Please add your first name.';
    if (!c) errs.contact = via === 'email' ? 'Please add your email address.' : 'Please add your mobile number.';
    else if (via === 'email' && !EMAIL_RE.test(c)) errs.contact = "That email address doesn't look right.";
    else if (via === 'phone' && !looksLikePhone(c)) errs.contact = "That number doesn't look right. A UK mobile starts 07.";
    const d = Number(day || 0);
    const m = Number(month || 0);
    if ((d && !m) || (!d && m)) errs.birthday = 'Please pick both the day and the month, or leave both empty.';
    else if (d && m && d > new Date(Date.UTC(2024, m, 0)).getUTCDate()) errs.birthday = "That month doesn't have that many days.";
    if (!terms) errs.terms = 'Please agree to the card rules to get a card.';
    setFe(errs);
    if (Object.keys(errs).length) return;
    setBusy(true);
    try {
      const r = await api<JoinResult>('POST', '/api/loyalty/join', {
        body: {
          first_name: f,
          ...(via === 'email' ? { email: c } : { phone: c }),
          ...(d && m ? { birthday_day: d, birthday_month: m } : {}),
          terms: true,
          marketing_opt_in: marketing,
          src: 'order',
          website: '',
        },
      });
      if (r.ok && r.data) {
        onDone(r.data);
        return;
      }
      if (r.status === 409 && r.error?.error === 'already_member') {
        onExists(c, `You already have a card with that ${via === 'email' ? 'email address' : 'number'}. We can send you a code to sign in.`);
        return;
      }
      const onField: Record<string, keyof typeof fe> = {
        first_name_required: 'first',
        first_name_too_long: 'first',
        bad_email: 'contact',
        bad_phone: 'contact',
        contact_required: 'contact',
        terms_required: 'terms',
        bad_birthday: 'birthday',
      };
      const code = r.error?.error ?? '';
      if (r.status === 422 && onField[code]) {
        setFe({ [onField[code]]: humanError(r) });
        return;
      }
      setErr(humanError(r));
    } finally {
      setBusy(false);
    }
  };

  return (
    <form class="sd-form" onSubmit={(e) => void submit(e)} noValidate>
      <Field
        label="First name"
        autocomplete="given-name"
        autocapitalize="words"
        maxLength={40}
        value={first}
        error={fe.first}
        onInput={(e) => setFirst((e.currentTarget as HTMLInputElement).value)}
      />
      <fieldset>
        <legend>How can we reach you?</legend>
        <div class="sd-seg" role="radiogroup" aria-label="Contact by">
          <label>
            <input type="radio" name="via" value="email" checked={via === 'email'} onChange={() => { setVia('email'); setContact(''); }} />
            <span>Email</span>
          </label>
          <label>
            <input type="radio" name="via" value="phone" checked={via === 'phone'} onChange={() => { setVia('phone'); setContact(''); }} />
            <span>Mobile</span>
          </label>
        </div>
        <Field
          label={via === 'email' ? 'Email address' : 'Mobile number'}
          hint="Only used for your card and your orders, unless you tick the news box below."
          type={via === 'email' ? 'email' : 'tel'}
          inputMode={via === 'email' ? 'email' : 'tel'}
          autocomplete={via === 'email' ? 'email' : 'tel'}
          autocapitalize="off"
          spellcheck={false}
          maxLength={via === 'email' ? 254 : 20}
          value={contact}
          error={fe.contact}
          onInput={(e) => setContact((e.currentTarget as HTMLInputElement).value)}
        />
      </fieldset>
      <fieldset>
        <legend>
          Birthday <em>(optional)</em>
        </legend>
        <p class="sd-muted">For a free drink around your birthday. Day and month only, no year.</p>
        <div class="sd-bday">
          <Field label="Day" id="sd-bday-d">
            <select id="sd-bday-d" class="sh-input" value={day} onChange={(e) => setDay((e.currentTarget as HTMLSelectElement).value)}>
              <option value="">Day</option>
              {Array.from({ length: 31 }, (_, i) => (
                <option key={i} value={String(i + 1)}>
                  {i + 1}
                </option>
              ))}
            </select>
          </Field>
          <Field label="Month" id="sd-bday-m">
            <select id="sd-bday-m" class="sh-input" value={month} onChange={(e) => setMonth((e.currentTarget as HTMLSelectElement).value)}>
              <option value="">Month</option>
              {MONTHS.map((mo, i) => (
                <option key={mo} value={String(i + 1)}>
                  {mo}
                </option>
              ))}
            </select>
          </Field>
        </div>
        {fe.birthday && (
          <p class="sd-ferr" role="alert">
            {fe.birthday}
          </p>
        )}
      </fieldset>
      <div class="sd-form" style="gap:12px">
        <label class="sd-check">
          <input type="checkbox" checked={terms} aria-invalid={fe.terms ? 'true' : undefined} onChange={(e) => setTerms((e.currentTarget as HTMLInputElement).checked)} />
          <span>
            I agree to the{' '}
            <a href="/rewards#rules" data-native="">
              card rules
            </a>{' '}
            and have read the{' '}
            <a href="/privacy" data-native="">
              privacy notice
            </a>
            .
          </span>
        </label>
        {fe.terms && (
          <p class="sd-ferr" role="alert">
            {fe.terms}
          </p>
        )}
        <label class="sd-check">
          <input type="checkbox" checked={marketing} onChange={(e) => setMarketing((e.currentTarget as HTMLInputElement).checked)} />
          <span>Send me news and offers, like a new seasonal menu. Optional: no more than two a month, and you can stop them at any time.</span>
        </label>
      </div>
      {err && <p class="sd-err" role="alert">{err}</p>}
      <div class="sd-actions">
        <Button tone="caramel" type="submit" busy={busy}>
          Get my card
        </Button>
        <button type="button" class="sd-linkbtn" onClick={onSignInInstead}>
          Already have a card?
        </button>
      </div>
    </form>
  );
}

export default Account;
