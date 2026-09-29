// The customer's account (owner decision 2026-09-29): the account is the thing, and the
// Rewards card is held inside it. Mounted site-wide at /account (pages/account.astro,
// scripts/shop/AccountApp.tsx); /order/account redirects here. Signed out: two tabs --
// Sign in (email or mobile, then a one-time code) and Create account (the loyalty join
// form). Signed in: the profile (Agent K) -- the real Rewards card as the till sees it,
// the free drink when one is ready, the order history with Order again, the member's
// details, and sign out. No password anywhere. The card id + token land in the same
// localStorage slot the rewards web card uses (scripts/rewards/api.ts), so a member
// who joined on /rewards is signed in here and vice-versa.
import { useEffect, useRef, useState } from 'preact/hooks';
import { api, humanError, saveToken } from '../../rewards/api';
import { cardHtml, type CardState, type JoinResult, type PublicProgram } from '../../rewards/card';
import { walletHtml, wireWalletTracking } from '../../rewards/wallet';
import '../../rewards/card.css';
import { session, shopApi, shopError } from '../api';
import { gbp } from '../format';
import { go, paths, route } from '../router';
import { catalogue, config, listNames, productById, reorder, sizeOf, stashNotice } from '../store';
import { me as meSignal, setMember, signIn, signOut, signedIn } from '../member';
import { showToast, stashToast } from '../toast';
import type { MeK, MeMember, MePatch, MyOrder, OrderStatus, RecentOrder } from '../types';
import { Button, Field, Notice } from '../ui';
import { useTitle } from '../views/useTitle';
import { EMAIL_RE, EmptyState, PageHead, SectionLabel, displayCode, loadOrderToken, localTime, looksLikePhone, useShopData } from '../checkout/common';

const MONTHS = ['January', 'February', 'March', 'April', 'May', 'June', 'July', 'August', 'September', 'October', 'November', 'December'];
type Tab = 'signin' | 'join';
export const ACCOUNT_LEAD =
  'Your account is your Rewards card: stamps on every drink, your details remembered, a free drink when you’ve earned it. No password — we send a code to your email or mobile.';

export function Account() {
  useTitle('My account', true);
  useShopData();
  const cfg = config.value;
  const q = route.value.query;
  const next = q.get('next') === 'checkout' ? 'checkout' : null;
  const [tab, setTab] = useState<Tab>(q.get('tab') === 'join' || location.hash === '#join' ? 'join' : 'signin');
  const on = signedIn.value;
  const [meErr, setMeErr] = useState<string | null>(null);
  const [joined, setJoined] = useState<JoinResult | null>(null);
  const [prefill, setPrefill] = useState<{ contact: string; note: string } | null>(null);

  useEffect(() => {
    if (!on) return;
    setMeErr(null);
    void shopApi.me().then((r) => {
      if (r.ok && r.data) setMember(r.data);
      else if (r.status === 401) signOut();
      else setMeErr(shopError(r));
    });
  }, [on]);

  const signedInNow = (cardId: string, tok: string) => {
    signIn(cardId, tok);
    if (next === 'checkout') go(paths.checkout());
  };
  const out = () => {
    signOut();
    setJoined(null);
  };

  const program = cfg?.loyalty;

  return (
    <div class={['sd-page', on && 'sd-page--wide'].filter(Boolean).join(' ')}>
      <PageHead back={next ? paths.checkout() : undefined} backLabel="Checkout" title={on ? 'My account' : tab === 'join' ? 'Create account' : 'Sign in'} />

      {!on && (
        <div class="sd-panel on-dark">
          <p class="sd-panel__kicker">{program?.program_name ?? "Sasha's Corner Rewards"}</p>
          <h2 class="sd-panel__title">
            {tab === 'join' ? (
              <>
                Create your account <span class="sd-soft">and order online.</span>
              </>
            ) : (
              <>
                Sign in <span class="sd-soft">to your account.</span>
              </>
            )}
          </h2>
          <p class="sd-panel__text">
            {program
              ? `${program.stamps_required} stamps and your next drink is free: ${program.reward_text.replace(/\.$/, '').toLowerCase()}. Stamps count online too, once an order is collected.`
              : 'Collect stamps at the till and online. No password: your email or mobile number gets you back in.'}
          </p>
        </div>
      )}

      {on ? (
        <Profile me={meSignal.value} err={meErr} joined={joined} onSignOut={out} next={next} />
      ) : (
        <>
          <div class="sd-acctabs" role="tablist" aria-label="Sign in or create an account">
            <button type="button" role="tab" id="sd-tab-signin" aria-selected={tab === 'signin'} aria-controls="sd-pane-signin" class="sd-acctab" onClick={() => setTab('signin')}>
              Sign in
            </button>
            <button type="button" role="tab" id="sd-tab-join" aria-selected={tab === 'join'} aria-controls="sd-pane-join" class="sd-acctab" onClick={() => setTab('join')}>
              Create account
            </button>
          </div>
          {tab === 'signin' ? (
            <div id="sd-pane-signin" role="tabpanel" aria-labelledby="sd-tab-signin">
              <SignIn prefill={prefill} onDone={(r) => signedInNow(r.card_id, r.token)} onJoinInstead={() => setTab('join')} />
            </div>
          ) : (
            <div id="sd-pane-join" role="tabpanel" aria-labelledby="sd-tab-join">
              <Join
                onDone={(r) => {
                  setJoined(r);
                  signedInNow(r.card_id, r.token);
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

// ---- the profile ------------------------------------------------------------------
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
const OFF = new Set<string>(['CANCELLED', 'REJECTED']);

function Profile({ me, err, joined, onSignOut, next }: { me: MeK | null; err: string | null; joined: JoinResult | null; onSignOut: () => void; next: 'checkout' | null }) {
  const cardId = session.cardId() ?? me?.card_id ?? null;
  const [card, setCard] = useState<CardState | null>(null);
  const [cardGone, setCardGone] = useState(false);
  const [cardErr, setCardErr] = useState<string | null>(null);
  const loadCard = async () => {
    const token = session.token();
    if (!cardId || !token) return;
    const r = await api<CardState>('GET', `/api/loyalty/card/${encodeURIComponent(cardId)}`, { token });
    if (r.ok && r.data) {
      setCard(r.data);
      setCardGone(false);
      setCardErr(null);
    } else if (r.status === 404 || r.status === 410) {
      // Signed in, but the card this token names is gone: offer the default programme.
      setCard(null);
      setCardGone(true);
      setCardErr(null);
    } else if (r.status === 401 || r.status === 403) {
      signOut();
    } else setCardErr(humanError(r));
  };
  useEffect(() => {
    void loadCard();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [cardId]);

  const first = me?.member?.first_name ?? me?.first_name ?? card?.first_name ?? '';
  const rewardReady = !!me?.reward || !!card?.rewards.some((r) => r.kind === 'STAMP_CARD');
  const rewardText = me?.reward?.text ?? card?.reward_text ?? config.value?.loyalty.reward_text ?? 'A free drink';

  return (
    <div class="sh-prof">
      {joined && (
        <Notice tone="info">
          Your account is ready{first ? `, ${first}` : ''}. Your Rewards card is below: save it somewhere you'll find it at the till, as the first stamp comes with your next drink.
        </Notice>
      )}
      {err && <Notice tone="error">{err}</Notice>}
      {first && <p class="sh-prof__hello">Hello, {first}.</p>}
      {next === 'checkout' && (
        <div class="sd-actions" style="margin-bottom:8px">
          <Button tone="caramel" href={paths.checkout()}>
            Back to checkout
          </Button>
        </div>
      )}

      <div class="sh-prof__grid">
        <div class="sh-prof__side">
          {/* ---- (a) your Rewards card ---- */}
          <section class="sd-sec sd-sec--first" aria-labelledby="sh-prof-card">
            <SectionLabel id="sh-prof-card">Your Rewards card</SectionLabel>
            <YourCard card={card} gone={cardGone} err={cardErr} cardId={cardId} onRetry={() => void loadCard()} onChanged={() => void loadCard()} />
          </section>
        </div>

        <div class="sh-prof__main">
          {/* ---- (b) free drink ---- */}
          {rewardReady && (
            <section class="sd-sec sd-sec--first" aria-labelledby="sh-prof-free">
              <SectionLabel id="sh-prof-free">Free drink</SectionLabel>
              <div class="sh-prof__free">
                <strong>Your free drink is ready.</strong>
                <p>
                  {rewardText.replace(/\.$/, '')}. Use it at checkout when you order online, or show your card at the till.
                </p>
                <div class="sd-actions">
                  <Button tone="ghost" size="sm" href={paths.overview()}>
                    Choose a drink
                  </Button>
                </div>
              </div>
            </section>
          )}

          {/* ---- (c) your orders ---- */}
          <section class={['sd-sec', !rewardReady && 'sd-sec--first'].filter(Boolean).join(' ')} aria-labelledby="sh-prof-orders">
            <SectionLabel id="sh-prof-orders">Your orders</SectionLabel>
            <Orders me={me} />
          </section>

          {/* ---- (d) your details ---- */}
          <section class="sd-sec" aria-labelledby="sh-prof-details">
            <SectionLabel id="sh-prof-details">Your details</SectionLabel>
            {me ? <Details me={me} card={card} onSaved={() => void loadCard()} /> : !err ? <p class="sd-muted" role="status">Loading your details…</p> : null}
          </section>

          {/* ---- (e) sign out ---- */}
          <section class="sd-sec sh-prof__out" aria-label="Sign out">
            <div class="sd-actions">
              <button type="button" class="sd-linkbtn" onClick={onSignOut}>
                Sign out on this device
              </button>
              <span class="sd-muted">Your account, card and stamps stay safe; sign in again with a code.</span>
            </div>
          </section>
        </div>
      </div>
    </div>
  );
}

// (a) The actual card: the same renderer as /c/<id> and the /rewards sample. Under it,
// the wallet buttons, the other programmes the member can add, and the full card page.
function YourCard({
  card,
  gone,
  err,
  cardId,
  onRetry,
  onChanged,
}: {
  card: CardState | null;
  gone: boolean;
  err: string | null;
  cardId: string | null;
  onRetry: () => void;
  onChanged: () => void;
}) {
  const walletRef = useRef<HTMLDivElement>(null);
  useEffect(() => {
    if (walletRef.current) wireWalletTracking(walletRef.current, 'account');
  }, []);
  const fullHref = cardId ? `/c/${encodeURIComponent(cardId)}` : null;
  const [adding, setAdding] = useState<string | null>(null);
  const [addErr, setAddErr] = useState<string | null>(null);

  // POST /api/loyalty/card/{id}/programs: one more card for this member (phase 3). The
  // new card's token is kept too, but the account stays on the card it signed in with.
  const add = async (slug: string, name: string) => {
    const token = session.token();
    if (!cardId || !token) return;
    setAdding(slug);
    setAddErr(null);
    const r = await api<JoinResult>('POST', `/api/loyalty/card/${encodeURIComponent(cardId)}/programs`, { token, body: { program: slug } });
    setAdding(null);
    if (r.ok && r.data) {
      saveToken(r.data.card_id, r.data.token);
      saveToken(cardId, token);
      showToast(`${name} added to your account.`);
      onChanged();
      return;
    }
    setAddErr(r.error?.detail || humanError(r));
  };

  if (err) {
    return (
      <Notice tone="error">
        {err}{' '}
        <button type="button" class="sd-linkbtn" onClick={onRetry}>
          Try again
        </button>
      </Notice>
    );
  }
  if (gone) return <AddDefaultCard busy={!!adding} err={addErr} onAdd={add} />;
  if (!card) {
    return (
      <div class="sh-prof__card" aria-busy="true">
        <p class="sr-only" role="status">
          Loading your card…
        </p>
        <span class="sh-skel" style="width:100%;max-width:400px;aspect-ratio:4/5;border-radius:18px" aria-hidden="true" />
      </div>
    );
  }
  // Signed in with a club card and no main card among the siblings: offer the default.
  const isMain = (card.program_slug ?? 'stamp') === 'stamp';
  const hasMain = isMain || (card.other_cards ?? []).some((o) => o.program_slug === 'stamp');
  const joinable = card.joinable ?? [];
  return (
    <div class="sh-prof__card">
      <div
        class="sh-prof__cardbox"
        // cardHtml() escapes every value it prints (scripts/rewards/card.ts `esc`).
        dangerouslySetInnerHTML={{ __html: cardHtml(card, { qr: card.qr_payload, qrLabel: 'Your card code, for scanning at the till' }) }}
      />
      <p class="sh-prof__till">Show the code to the barista at the till: it works on this screen, in your wallet, or on the full card page.</p>
      <div
        class="sh-prof__wallet"
        ref={walletRef}
        dangerouslySetInnerHTML={{ __html: walletHtml({ apple: card.wallets.apple_pass_url, google: card.wallets.google_save_url }) }}
      />
      {!hasMain && <AddDefaultCard busy={!!adding} err={addErr} onAdd={add} compact />}
      {(card.other_cards?.length ?? 0) > 0 && (
        <div class="sh-prof__clubs">
          <p class="sh-prof__clubs__label">Also in your account</p>
          <ul class="sh-prof__clublist">
            {card.other_cards!.map((o) => (
              <li key={o.card_id}>
                <span>
                  <strong>{o.program_name}</strong>
                  <small class="num">
                    {o.stamps_current}/{o.stamps_required}
                    {o.reward_ready ? ' · reward ready' : ''}
                  </small>
                </span>
                <a class="sd-link" href={o.web_card_url} data-native="">
                  Open
                </a>
              </li>
            ))}
          </ul>
        </div>
      )}
      {joinable.length > 0 && (
        <div class="sh-prof__clubs">
          <p class="sh-prof__clubs__label">Add to your account</p>
          <ul class="sh-prof__clublist">
            {joinable.map((p) => (
              <li key={p.slug}>
                <span>
                  <strong>{p.name}</strong>
                  <small>{p.description ?? (p.kind === 'POINTS' ? `${p.points_per_pound ?? ''} points a pound` : `${p.stamps_required} stamps, then ${p.reward_text.toLowerCase()}`)}</small>
                </span>
                <Button tone="ghost" size="sm" busy={adding === p.slug} disabled={!!adding && adding !== p.slug} onClick={() => void add(p.slug, p.name)}>
                  Add
                </Button>
              </li>
            ))}
          </ul>
          {addErr && (
            <p class="sd-ferr" role="alert">
              {addErr}
            </p>
          )}
        </div>
      )}
      {fullHref && (
        <p class="sh-prof__cardlinks">
          <a class="sd-link" href={fullHref} data-native="">
            Open the full card
          </a>
          <span class="sd-muted">Invite a friend, delete your card.</span>
        </p>
      )}
    </div>
  );
}

/** No card for the default programme: one button adds it (POST …/programs, the default slug). */
function AddDefaultCard({ busy, err, onAdd, compact }: { busy: boolean; err: string | null; onAdd: (slug: string, name: string) => void; compact?: boolean }) {
  const [def, setDef] = useState<PublicProgram | null>(null);
  useEffect(() => {
    void api<{ programs: PublicProgram[] }>('GET', '/api/loyalty/programs').then((r) => {
      const p = r.ok && r.data ? (r.data.programs.find((x) => x.is_default) ?? r.data.programs[0] ?? null) : null;
      setDef(p ?? { slug: 'stamp', name: config.value?.loyalty.program_name ?? "Sasha's Corner Rewards", kind: 'STAMPS', description: null, stamps_required: config.value?.loyalty.stamps_required ?? 8, points_per_pound: null, reward_text: config.value?.loyalty.reward_text ?? 'Any drink, on us', reward_ready_label: 'Free drink ready', is_default: true });
    });
  }, []);
  return (
    <div class={['sh-prof__free', compact && 'sh-prof__addcard'].filter(Boolean).join(' ')}>
      <strong>Add your Rewards card</strong>
      <p>
        {compact
          ? "Your account doesn't have the main stamp card yet. Add it and every drink you collect gets a stamp."
          : "We couldn't find a Rewards card on this account. Add one and every drink you collect gets a stamp; your details stay as they are."}
      </p>
      <div class="sd-actions">
        <Button tone="caramel" size="sm" busy={busy} disabled={!def} onClick={() => def && onAdd(def.slug, def.name)}>
          Add my Rewards card
        </Button>
      </div>
      {err && (
        <p class="sd-ferr" role="alert">
          {err}
        </p>
      )}
    </div>
  );
}

// (c) The order history: paged from /api/shop/me/orders; while that route is not live,
// the /me answer's recent orders (no names on the wire, so the names come from today's
// catalogue).
interface Row {
  code: string;
  status: OrderStatus;
  status_label: string;
  when: string;
  total_pence: number;
  summary: string | null;
  reorder: { lines: MyOrder['reorder']['lines']; complete: boolean } | null;
}
const PAGE = 10;

function rowFromMyOrder(o: MyOrder): Row {
  const parts = o.lines.map((l) => `${l.qty}× ${l.name}${l.size_label ? ` (${l.size_label})` : ''}${l.options.length ? ` · ${l.options.map((x) => x.name).join(', ')}` : ''}`);
  return {
    code: o.code,
    status: String(o.status).toUpperCase() as OrderStatus,
    status_label: o.status_label || STATUS_WORDS[String(o.status).toUpperCase() as OrderStatus] || String(o.status),
    when: o.placed_local,
    total_pence: o.total_pence,
    summary: parts.join(', ') || null,
    reorder: o.reorder?.lines?.length ? o.reorder : null,
  };
}
function rowFromRecent(o: RecentOrder): Row {
  const cat = catalogue.value;
  const status = String(o.status).toUpperCase() as OrderStatus;
  const parts = (o.lines ?? []).map((l) => {
    const p = cat ? productById(cat, l.product_id) : undefined;
    const size = p && sizeOf(p, l.menu_item_id);
    const name = p ? `${p.name}${size && size.code !== 'ONE' && size.label ? ` (${size.label})` : ''}` : 'an item no longer on the menu';
    return `${l.qty}× ${name}`;
  });
  return {
    code: o.code,
    status,
    status_label: STATUS_WORDS[status] ?? String(o.status),
    when: localTime(o.placed_at, true),
    total_pence: o.total_pence,
    summary: parts.join(', ') || null,
    reorder: o.lines?.length ? { lines: o.lines, complete: o.reorder_complete !== false } : null,
  };
}

function Orders({ me }: { me: MeK | null }) {
  const cat = catalogue.value;
  const [rows, setRows] = useState<Row[] | null>(null);
  const [total, setTotal] = useState(0);
  const [page, setPage] = useState(0);
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const [fallback, setFallback] = useState(false);
  const [note, setNote] = useState<string | null>(null);

  const load = async (p: number) => {
    setBusy(true);
    setErr(null);
    const r = await shopApi.myOrders(p, PAGE);
    setBusy(false);
    if (r.ok && r.data) {
      const fresh = r.data.items.map(rowFromMyOrder);
      setRows((cur) => (p === 1 || !cur ? fresh : [...cur, ...fresh]));
      setTotal(r.data.total);
      setPage(r.data.page);
      return;
    }
    if (r.status === 404 || r.status === 405) {
      // The paged route is not live yet: the /me answer carries the last few.
      setFallback(true);
      return;
    }
    if (r.status === 401) {
      signOut();
      return;
    }
    setErr(shopError(r));
  };
  useEffect(() => {
    void load(1);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);
  useEffect(() => {
    if (fallback && me) {
      const fresh = me.recent_orders.map(rowFromRecent);
      setRows(fresh);
      setTotal(fresh.length);
      setPage(1);
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [fallback, me, cat]);

  // "Order again": the order's lines back through the basket against today's menu.
  const again = (o: Row) => {
    if (!cat || !o.reorder) return;
    const r = reorder(cat, o.reorder.lines);
    // The server's own verdict counts too: a line it could not map is one we never saw.
    const unknown = Math.max(r.unknown, !o.reorder.complete && r.leftOut.length === 0 && r.unknown === 0 ? 1 : 0);
    const bits: string[] = [];
    if (r.leftOut.length) bits.push(`${listNames(r.leftOut)} ${r.leftOut.length === 1 ? 'is' : 'are'} not available today and ${r.leftOut.length === 1 ? 'was' : 'were'} left out.`);
    if (unknown) bits.push(`${unknown === 1 ? 'One item' : `${unknown} items`} from that order ${unknown === 1 ? 'is' : 'are'} no longer on the menu.`);
    if (r.added === 0) {
      setNote(`Nothing from ${displayCode(o.code)} can be ordered today. ${bits.join(' ')}`);
      return;
    }
    if (r.changed.length) bits.push(`${listNames(r.changed)}: an option has changed, so please check it.`);
    // The basket is a full page load away from /account, so the words travel in sessionStorage.
    if (bits.length) stashNotice(bits.join(' '));
    else stashToast(`${displayCode(o.code)} added to your order.`);
    go(paths.basket());
  };

  if (err && !rows) {
    return (
      <Notice tone="error">
        {err}{' '}
        <button type="button" class="sd-linkbtn" onClick={() => void load(1)}>
          Try again
        </button>
      </Notice>
    );
  }
  if (!rows) {
    return (
      <div aria-busy="true">
        <p class="sr-only" role="status">
          Loading your orders…
        </p>
        <div class="sh-skel-grid" aria-hidden="true">
          <span class="sh-skel" style="width:55%;height:1.2rem" />
          <span class="sh-skel" style="width:80%;height:1rem" />
          <span class="sh-skel" style="width:45%;height:1.2rem" />
        </div>
      </div>
    );
  }
  if (rows.length === 0) {
    return (
      <EmptyState title="No online orders yet" action={<Button tone="caramel" size="sm" href={paths.overview()}>Start an order</Button>}>
        <p>Orders you place online show up here, with a one-tap way to order the same again.</p>
      </EmptyState>
    );
  }
  return (
    <>
      {note && (
        <Notice tone="warn" onDismiss={() => setNote(null)}>
          {note}
        </Notice>
      )}
      <ul class="sh-orders">
        {rows.map((o) => {
          const t = loadOrderToken(o.code);
          const shown = displayCode(o.code);
          const canReorder = !!cat && !!o.reorder;
          return (
            <li class="sh-order" key={o.code}>
              <div class="sh-order__head">
                <span class="sh-order__code num">{t ? <a href={paths.status(o.code)}>{shown}</a> : shown}</span>
                <span class="sh-order__when">{o.when}</span>
                <span class={['sh-order__status', OFF.has(o.status) && 'is-off'].filter(Boolean).join(' ')}>{o.status_label}</span>
              </div>
              <span class="sh-order__total num">{gbp(o.total_pence)}</span>
              {o.summary && <p class="sh-order__lines">{o.summary}</p>}
              {(canReorder || t) && (
                <div class="sh-order__tools">
                  {canReorder && (
                    <Button tone="ghost" size="sm" onClick={() => again(o)} aria-label={`Order ${shown} again`}>
                      Order again
                    </Button>
                  )}
                  {t && (
                    <a class="sd-link" href={paths.status(o.code)} aria-label={`View order ${shown}`}>
                      View
                    </a>
                  )}
                </div>
              )}
            </li>
          );
        })}
      </ul>
      {err && (
        <Notice tone="error" onDismiss={() => setErr(null)}>
          {err}
        </Notice>
      )}
      <div class="sh-orders__more">
        <span class="sd-muted num">
          {rows.length} of {total}
        </span>
        {rows.length < total && !fallback && (
          <Button tone="ghost" size="sm" busy={busy} onClick={() => void load(page + 1)}>
            Show more
          </Button>
        )}
      </div>
    </>
  );
}

// (d) Name, contact, birthday and the news switch: PATCH /api/shop/me with only what
// changed, and the server's refusal shown as it came.
type Form = { first: string; email: string; phone: string; day: string; month: string; marketing: boolean };
const FIELD_OF: Record<string, keyof Omit<Form, 'marketing'>> = {
  first_name_required: 'first',
  first_name_too_long: 'first',
  bad_email: 'email',
  email_taken: 'email',
  bad_phone: 'phone',
  phone_taken: 'phone',
  contact_required: 'email',
  bad_birthday: 'day',
};

function Details({ me, card, onSaved }: { me: MeK; card: CardState | null; onSaved: () => void }) {
  const base = (): Form => ({
    first: me.member?.first_name ?? me.first_name ?? '',
    email: me.member?.email ?? me.email ?? '',
    phone: me.member?.phone ?? me.phone ?? '',
    day: String(me.member?.birthday_day ?? card?.birthday_day ?? ''),
    month: String(me.member?.birthday_month ?? card?.birthday_month ?? ''),
    marketing: me.member?.marketing_opt_in ?? card?.marketing_opt_in ?? false,
  });
  const [form, setForm] = useState<Form>(base);
  const [dirty, setDirty] = useState(false);
  // A fresh /me or card answer fills the form unless the member is mid-edit.
  const key = JSON.stringify([me.member, me.first_name, me.email, me.phone, card?.birthday_day, card?.birthday_month, card?.marketing_opt_in]);
  useEffect(() => {
    if (!dirty) setForm(base());
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [key]);
  const [fe, setFe] = useState<Partial<Record<keyof Form, string>>>({});
  const [msg, setMsg] = useState<string | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const set = <K extends keyof Form>(k: K, v: Form[K]) => {
    setDirty(true);
    setMsg(null);
    setForm((f) => ({ ...f, [k]: v }));
  };

  const save = async (e: Event) => {
    e.preventDefault();
    setErr(null);
    setMsg(null);
    const errs: typeof fe = {};
    const first = form.first.trim();
    const email = form.email.trim();
    const phone = form.phone.trim();
    if (!first) errs.first = 'Please add your first name.';
    if (email && !EMAIL_RE.test(email)) errs.email = "That email address doesn't look right.";
    if (phone && !looksLikePhone(phone)) errs.phone = "That number doesn't look right. A UK mobile starts 07.";
    if (!email && !phone) errs.email = 'Keep an email address or a mobile number: it is how we send you a code to sign in.';
    const d = Number(form.day || 0);
    const m = Number(form.month || 0);
    if ((d && !m) || (!d && m)) errs.day = 'Please pick both the day and the month, or leave both empty.';
    else if (d && m && d > new Date(Date.UTC(2024, m, 0)).getUTCDate()) errs.day = "That month doesn't have that many days.";
    setFe(errs);
    if (Object.keys(errs).length) return;

    const was = base();
    const body: MePatch = {};
    if (first !== was.first) body.first_name = first;
    if (email !== (was.email ?? '')) body.email = email || null;
    if (phone !== (was.phone ?? '')) body.phone = phone || null;
    if (String(d || '') !== was.day || String(m || '') !== was.month) {
      body.birthday_day = d || null;
      body.birthday_month = m || null;
    }
    if (form.marketing !== was.marketing) body.marketing_opt_in = form.marketing;
    if (Object.keys(body).length === 0) {
      setDirty(false);
      setMsg('Nothing to change.');
      return;
    }
    setBusy(true);
    try {
      const r = await shopApi.updateMe(body);
      if (r.ok && r.data) {
        setDirty(false);
        const d = r.data;
        const mem = 'member' in d && d.member ? d.member : 'card_id' in d ? null : (d as MeMember);
        setMember('card_id' in d ? d : { ...me, first_name: mem!.first_name, email: mem!.email, phone: mem!.phone, member: mem! });
        setMsg('Saved.');
        onSaved();
        return;
      }
      if (r.status === 401) {
        signOut();
        return;
      }
      // The refusal, as the server put it.
      const code = r.error?.error ?? '';
      const verbatim = r.error?.detail || null;
      if ((r.status === 422 || r.status === 409) && (FIELD_OF[code] || code === 'contact_taken')) {
        // 409 contact_taken: J's one uniqueness rule; it lands on whichever contact changed.
        const field = FIELD_OF[code] ?? (body.phone !== undefined && body.email === undefined ? 'phone' : 'email');
        setFe({ [field]: verbatim ?? humanError(r) });
        return;
      }
      if (r.status === 404 || r.status === 405) {
        setErr("Changing your details here isn't switched on yet. Ask us at the till and we'll update them.");
        return;
      }
      setErr(verbatim ?? shopError(r));
    } finally {
      setBusy(false);
    }
  };

  return (
    <form class="sh-prof__details" onSubmit={(e) => void save(e)} noValidate>
      <div class="sh-prof__row">
        <Field label="First name" autocomplete="given-name" autocapitalize="words" maxLength={40} value={form.first} error={fe.first} onInput={(e) => set('first', (e.currentTarget as HTMLInputElement).value)} />
      </div>
      <div class="sh-prof__row">
        <Field
          label="Email address"
          type="email"
          inputMode="email"
          autocomplete="email"
          autocapitalize="off"
          spellcheck={false}
          maxLength={254}
          value={form.email}
          error={fe.email}
          onInput={(e) => set('email', (e.currentTarget as HTMLInputElement).value)}
        />
        <Field
          label="Mobile number"
          type="tel"
          inputMode="tel"
          autocomplete="tel"
          maxLength={20}
          value={form.phone}
          error={fe.phone}
          onInput={(e) => set('phone', (e.currentTarget as HTMLInputElement).value)}
        />
      </div>
      <p class="sd-muted">We send your sign-in code to one of these, so keep at least one.</p>
      <fieldset>
        <legend>
          Birthday <em>(optional)</em>
        </legend>
        <p class="sd-muted">For a free drink around your birthday. Day and month only, no year.</p>
        <div class="sh-prof__bday">
          <Field label="Day" id="sh-prof-bday-d">
            <select id="sh-prof-bday-d" class="sh-input" value={form.day} aria-invalid={fe.day ? 'true' : undefined} onChange={(e) => set('day', (e.currentTarget as HTMLSelectElement).value)}>
              <option value="">Day</option>
              {Array.from({ length: 31 }, (_, i) => (
                <option key={i} value={String(i + 1)}>
                  {i + 1}
                </option>
              ))}
            </select>
          </Field>
          <Field label="Month" id="sh-prof-bday-m">
            <select id="sh-prof-bday-m" class="sh-input" value={form.month} onChange={(e) => set('month', (e.currentTarget as HTMLSelectElement).value)}>
              <option value="">Month</option>
              {MONTHS.map((mo, i) => (
                <option key={mo} value={String(i + 1)}>
                  {mo}
                </option>
              ))}
            </select>
          </Field>
        </div>
        {fe.day && (
          <p class="sd-ferr" role="alert">
            {fe.day}
          </p>
        )}
      </fieldset>
      <label class="sd-toggle">
        <span class="sd-toggle__text">
          <strong>Send me news and offers</strong>
          <small>No more than two a month, like a new seasonal menu. Card updates always come through.</small>
        </span>
        <input type="checkbox" role="switch" checked={form.marketing} aria-checked={form.marketing} onChange={(e) => set('marketing', (e.currentTarget as HTMLInputElement).checked)} />
      </label>
      {err && (
        <p class="sd-err" role="alert">
          {err}
        </p>
      )}
      <div class="sd-actions">
        <Button tone="caramel" type="submit" busy={busy}>
          Save
        </Button>
        {msg && (
          <span class="sh-prof__saved" role="status">
            {msg}
          </span>
        )}
      </div>
    </form>
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
          ? 'If that number has an account, we have just texted it a code. It lasts 10 minutes.'
          : 'If that email address has an account, we have just emailed it a code. It lasts 10 minutes; check your junk folder too.',
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
      <p class="sd-muted">No password. We send a one-time code to the email address or mobile number on your account.</p>
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
          I'm new: create an account
        </button>
      </div>
    </form>
  );
}

// ---- create account: the loyalty join form --------------------------------------------
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
    if (!terms) errs.terms = 'Please agree to the card rules to create your account.';
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
        onExists(c, `You already have an account with that ${via === 'email' ? 'email address' : 'number'}. We can send you a code to sign in.`);
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
      <p class="sd-muted">{ACCOUNT_LEAD}</p>
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
          hint="Only used for your account and your orders, unless you tick the news box below."
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
          Create my account
        </Button>
        <button type="button" class="sd-linkbtn" onClick={onSignInInstead}>
          Already have an account? Sign in
        </button>
      </div>
    </form>
  );
}

export default Account;
