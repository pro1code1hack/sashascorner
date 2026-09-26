# Frontend kit: primitives, shell, routes and rules for screen builders

Built from [`design-system.md`](design-system.md) and [`shell-agents.md`](shell-agents.md);
[`DECISIONS.md`](DECISIONS.md) overrides both. Tokens live in `web/src/styles.css`
(`@theme`). Primitives live in `web/src/components/ui/`, and you import them from the barrel:

```ts
import { Button, Field, Input, Table, Th, Td, Tr, PageHeader, PageBody } from '../../components/ui'
```

---

## 1. Rules for screen builders

1. **Tokens only. Never write a hex, rgb or arbitrary colour in a screen.** Use `bg-canvas`,
   `text-ink-2`, `border-line`, `bg-alert-wash` and the rest from `styles.css`. If a colour
   you need is missing, raise it with the integrator. Do not add it inline.
2. **Red means a threshold was crossed.** `text-alert` / `bg-alert-wash` / `Pill tone="bad"`
   go only on a value past its rule (margin < 60%, |drift| > 15%, runs out ≤ 3 days, and so on).
   Green appears only as `ok-wash`+`ok-ink` inside a labelled pill or tile, never as text on white.
3. **Contrast.** `ink-3` is for ≥ 14px meta that carries no information. Anything a
   person needs to read uses `ink-2`. Red text at 13px or below uses `bad-ink`, not `alert`.
4. **Estimates are italic, counts are upright.** Use `est` on `Td`, `Input`, `Tile` and
   `KeyValueGrid` rows, or the `.est` / `italic` class. Put `<EstLegend/>` on screens that show
   estimates. Theoretical stock is always italic (invariant 6).
5. **Money is integer pence, formatted at the edge** with `lib/format`: use `gbp(pence)` for
   design-style `£12.00` / `−£4.20`, and `money()` / `poundsOnly()` / `pence()` where they fit.
   Never divide pence by 100 in a screen. A missing cost is `—` or words ("no price"), **never 0**
   (invariant 8).
6. **Quantities are strings.** Keep them as the API's strings, and parse or add them with
   `lib/dec` (`parseDec`, `add`, `toFixed`, `trimQty`). Never call `Number()` or `parseFloat` on a
   qty or on money. Inputs hold strings; parse only at submit time
   (`components/confirm/numbers.ts` has `poundsToPence`, `parseDays`).
7. **Low-confidence forecasts replace the number** (invariant 9): show the reason where the
   figure would go, not beside it.
8. **Layout contract.** A screen returns `<PageHeader/>`, then an optional `<Toolbar/>` /
   `<MetaStrip/>`, then **one** `<PageBody/>` that owns the scroll. A two-pane or drawer screen
   uses `<div className="flex min-h-0 flex-1">` with its panes (each `min-h-0 overflow-y-auto`)
   and `<Drawer/>` as the last child. The page never scrolls sideways: wide tables go in
   `Table minWidth={…}`, which scrolls inside itself. Check at 375, 1180 and 1440.
9. **Breakpoints.** Use `compact:` (≥ 900px, where the sidebar docks and drawers sit inline),
   `wide:` (≥ 1280px) and `max-compact:` (< 900px), plus the Tailwind defaults `sm:` (640px) etc.
   Use `useIsDocked()` / `useMediaQuery()` from `lib/media` only when *behaviour* differs.
10. **Writes.** Use `apiWrite<T>(path, body, method?)` from `lib/api`. It returns a `WriteResult`
    (`ok` / `refused` with the server's sentence / `offline` in fixture mode / `failed`). Show a
    refusal verbatim. While a write is in flight, use `Button pending pendingLabel="Applying…"`.
    Nothing is optimistic. Invalidate `['shell']` (`SHELL_QUERY_KEY`) after anything that
    changes a badge (orders waiting, proposals waiting).
11. **Reads.** Use `request<T>(path)` from `lib/api` inside `useQuery`. Auth headers are attached
    for you, and in fixture mode `request` answers GETs from `web/fixtures/` itself (`lib/fixtures`):
    no `LIVE` branch in readers. A new endpoint needs an `Example` in `cafeops/api/examples.py`
    and a `cafeops api-fixtures` run, or fixture mode shows "Not in the recorded fixtures".
12. **Operator name.** Any write that needs a human name (`counted_by`, `received_by`,
    `responded_by`, `requested_by`, `decided_by`, `actor`) reads it with `useOperator()` from
    `lib/operator`, stays **disabled while it is null**, and renders
    `<OperatorNeeded what="record a count"/>` (from `components/shell/Operator`) next to the
    action.
13. **Web never creates or confirms a purchase order** (DECISIONS §1). Orders are read-only,
    with "Waiting in Telegram" for pending ones.
14. **Uppercase:** only sidebar group heads, table headers (the `Table` default) and
    `Field upper` labels. Anything else is sentence case.
15. **Buttons are `<button>`, links are `<a href="#/…">`.** Never put `onClick` on a div.
    Every icon-only control has a `label`. Glyphs (★ × ✓ − +) are `aria-hidden`.
16. **No modals.** Drawers replace them. No stat cards, sparklines, delta pills or KPI grids.
17. **No tests** (ARCHITECTURE §1). Verify with `npx tsc --noEmit && npx vite build`, then look at
    the screen in a browser at 375 / 1180 / 1440.

---

## 2. File layout

```
web/src/
  App.tsx                 auth gate + QueryClient + router outlet
  routes.tsx              ROUTES (id, path, label, Screen, badge), NAV_GROUPS, resolveRoute
  styles.css              @theme tokens, base layer (.fig .est .scroll-x focus, reduced motion)
  components/
    ui/                   v2 primitives (this document). Barrel: ui/index.ts
    shell/                Shell.tsx (frame), Login.tsx, Operator.tsx, ScreenFallback.tsx (route loading)
    confirm/numbers.ts    poundsToPence, penceToPounds, parseDays, parseCutoff
  screens/<area>/         one folder per area; <Name>Screen.tsx is the route entry, lazy-loaded
                          (routes.tsx) so each screen is its own chunk
  lib/
    api.ts                request, apiWrite, auth (authHeaders, signIn, signOut)
    fixtures.ts           fixture mode: answers GETs from web/fixtures/ via fixtures/index.json
    shell-api.ts          GET /api/shell types + useShell(), startSync(), banner dismissal
    router.ts             useLocation(), navigate(), href()
    operator.ts           useOperator(), getOperator(), setOperator()
    media.ts              useMediaQuery(), useIsDocked()
    format.ts dec.ts types.ts
```

Area screens own everything under `screens/<area>/`: put sub-components, data hooks and
view-model code beside the screen file. Shared primitives belong in `components/ui/`; ask
before you add one. (The pre-v2 `legacy/` screens and their kit were deleted once nothing
routed to or imported them; git history has them.)

---

## 3. Shell and routes

Hash routing (`#/stock`), so a reload keeps the page. A route matches its own path and any
path below it, so a screen may use sub-paths such as `#/recipes/proposal/abc` and read them
with `useLocation().segments` / `.query`. An unknown hash redirects to `#/stock`.

| Nav group | Label | Hash | Screen file | Badge |
|---|---|---|---|---|
| Every day | Stock | `#/stock` | `screens/stock/StockScreen.tsx` | none |
| | Orders | `#/orders` | `screens/orders/OrdersScreen.tsx` | `badges.orders_waiting` |
| | Agents | `#/agents` | `screens/agents/AgentsScreen.tsx` | `badges.proposals_waiting` |
| Menu | Recipes | `#/recipes` | `screens/recipes/RecipesScreen.tsx` | none |
| | Menu items | `#/menu` | `screens/menu/MenuItemsScreen.tsx` | none |
| | Ingredients | `#/ingredients` | `screens/ingredients/IngredientsScreen.tsx` | none |
| | Suppliers | `#/suppliers` | `screens/suppliers/SuppliersScreen.tsx` | none |
| Money | Overview | `#/money/overview` (also `#/money`) | `screens/money/OverviewScreen.tsx` | none |
| | Sales | `#/money/sales` | `screens/money/SalesScreen.tsx` | none |
| | Expenses | `#/money/expenses` | `screens/money/ExpensesScreen.tsx` | none |
| | Reconcile | `#/money/reconcile` | `screens/money/ReconcileScreen.tsx` | none |
| | Profit & loss | `#/money/pnl` | `screens/money/ProfitLossScreen.tsx` | none |
| | Director's account | `#/money/director` | `screens/money/DirectorsAccountScreen.tsx` | none |
| footer | Settings | `#/settings` | `screens/settings/SettingsScreen.tsx` | none |
| (not in nav) | Setup checklist | `#/setup` | `screens/setup/SetupScreen.tsx` | none |

In code, navigate with `navigate('/money/reconcile')`, or link with `<a href={href('/stock', {filter: 'shelf'})}>`.

**Frame** (`components/shell/Shell.tsx`):
- **≥ 1280px:** 232px docked sidebar.
- **900–1279px:** 200px docked sidebar.
- **< 900px:** 56px top bar (hamburger with the summed badge, "S" tile, page title) and a
  280px off-canvas sidebar over `bg-scrim`. It traps focus, closes on Esc, on a scrim tap or
  on navigation, and returns focus to the hamburger.

The sidebar shows the brand, the sync line ("sales synced 3 hours ago" /
"Lightspeed not connected"), the nav groups, the operator line, Settings, and Sign out
(live only).

**Banners** come from `GET /api/shell → banners[]` (MISSING on the backend; until it exists the
shell shows no banners, badges or sync line: unknown renders as nothing, never as 0).
Dismissal is per session, per `instance_key`. Fixture mode shows a permanent "Fixture data"
strip.

**Auth** (`lib/api.ts`): `authHeaders()` is the single place auth is attached. `signIn(pw)`
tries `POST /api/auth/session` (a session token, sent as `Authorization: Bearer`). On
404/405 it falls back to verifying the raw password against the authenticated
`GET /api/suppliers` and requires a JSON 200, so an HTML fallback page cannot "unlock". It
then stores the credential in sessionStorage with its kind. A live 401 clears the credential
and fires `SIGNED_OUT_EVENT`, and the app returns to Login with an explanation. `signOut()`
calls `DELETE /api/auth/session` when holding a session. Login messages: 401 "That's not
it…", 503 "no password set", 429 "Too many tries".

---

## 4. Primitives

Every primitive has visible `:focus-visible` focus (2px brand outline) and uses only
`transition-colors`-class transitions, which the global reduced-motion rule neutralises.

### 4.1 Buttons (`ui/button.tsx`)

**`Button`**
- Props: `variant` (`primary | secondary | outline | danger | danger-soft | on-wash | ghost | link | add`,
  default `secondary`), `size` (`sm | md | lg`; each variant has the design's default),
  `block`, `pending`, `pendingLabel`, and every `<button>` attribute. `type` defaults to `button`.
- Behaviour: pending disables the button and swaps the label without changing its width.
- Heights: sm 32 (primary 36), md 40, lg 48.

```tsx
<Button variant="primary" pending={m.isPending} pendingLabel="Applying…" onClick={apply}>Apply from today</Button>
```

**`IconButton`**
- Props: `label` (required, the accessible name), `tone` (`plain | wash`), `size` (`36 | 40`),
  and `children` (defaults to ×).

```tsx
<IconButton label="Close" tone="wash" onClick={onClose} />
```

**`ConfirmTwiceButton`**
- Props: `armedLabel`, `onConfirm`, `variant` (default `danger`), `size`, `pending`, `pendingLabel`.
- Behaviour: the first press arms it for 4s; the second press fires.

```tsx
<ConfirmTwiceButton armedLabel="Tap again to archive" onConfirm={archive}>Archive supplier</ConfirmTwiceButton>
```

### 4.2 Fields (`ui/field.tsx`)

**`Field`**
- Props: `label`, `hint`, `error`, `upper` (the 11px uppercase label), `className`.
- Behaviour: it gives the child control its id, `aria-describedby` and `aria-invalid`.
- The label is 12px bold `ink-2`, in sentence case.

**`Input`**
- Props: `size` (`md` 40px | `sm` 38px | `xs` 28px, the in-table size), `numeric` (right-aligned,
  tabular), `changed` (alert border on alert wash), `missing` (alert border), `est` (italic),
  plus every `<input>` attribute.

**`MoneyInput`**
- Props: the same as `Input`, plus `big` (a borderless figure for a `Tile`).
- Renders a `£` prefix. The value is a pounds **string**; convert it with `poundsToPence`.

**`SearchInput`**
- Props: `label` (default "Search") and `<input>` attributes.

**`TitleInput`**
- Props: `variant` (`page` 26px with an underline | `drawer` 22px).

**`Select`**
- Props: `size`, `variant` (`box` | `role`, the recipe role picker).

**`Textarea`** and **`PasswordInput`**
- `PasswordInput` is the 48px login box.

```tsx
<Field label="Pack size" hint="litres per pack" error={err}>
  <Input numeric value={packSize} onChange={(e) => setPackSize(e.target.value)} />
</Field>
```

### 4.3 Toggles (`ui/toggle.tsx`)

**`Toggle`**
- Props: `checked`, `onChange(next)`, `label`, `disabled`.
- Renders `role="switch"`; the track is `ok` when on.

**`Checkbox`**
- Props: `checked`, `onChange`, `label`, `alert` (red while unchecked: terms confirmed).

**`Stepper`**
- Props: `value`, `onDecrement`, `onIncrement`, `label` (the noun: "packs"), `size` (`sm` 22px | `lg` 50px),
  `canDecrement`, `canIncrement`, `children` (replaces the value with an input).
- The arithmetic stays in the caller, with `lib/dec`.

**`SizeTile`**
- Props: `label`, `sub`, `active`, `onClick`, `add`.

```tsx
<Toggle checked={active} onChange={setActive} label="On the menu" />
<Stepper value={packs} label="packs" onDecrement={dec} onIncrement={inc} />
```

### 4.4 Chips (`ui/chips.tsx`)

**`FilterChip`**
- Props: `active`, `onClick`, `count`, `tone` (`default | alert`; `alert` is "Needs a look"),
  `size` (`34 | 36`).
- Renders `aria-pressed`. It replaces every legacy solid pill.

**`FilterChipRow`**
- Props: `label` (group name), `scroll` (one line that scrolls inside itself).

**`Segmented<T>`**
- Props: `options` (`{value, label, ariaLabel?}[]`), `value`, `onChange`, `label`, `showLabel`.
- It is a radiogroup; the arrow keys move the selection.

**`LinkChip`**
- Props: `href` or `onClick`, `quiet` (the "Up next" style).

```tsx
<Segmented label="Tier" showLabel value={tier} onChange={setTier}
  options={[{value:'all',label:'All'},{value:'A',label:'A'},{value:'B',label:'B'},{value:'C',label:'C'}]} />
```

### 4.5 Badges (`ui/badges.tsx`)

**`CountBadge`**
- Props: `count`, `label`.
- Renders nothing when the count is ≤ 0 or unknown.

**`Pill`**
- Props: `tone` (`ok | warn | bad | neutral | muted | brand`), `children`.

**`TrustPill`**
- Props: `trust` (`trusted | drifting | excluded | never_counted | checklist | low`).
- Uses the design's words and tones.

**`MarginChip`**
- Props: `marginPct` (`number | null`).
- Below 60% it is bad; 60% and above is ok; `null` shows "no price".

**`StatusTag`**
- Props: `tone` (`ink | ink-3 | alert`).
- Order statuses: Waiting is `alert`; Draft, Received and Cancelled are `ink-3`; Confirmed and Sent are `ink`.

**`TierBadge`**
- Props: `tier`.

**`Dot`**
- Props: `tone` (`muted | alert | brand`).

```tsx
<TrustPill trust={row.status === 'trusted' ? 'trusted' : 'drifting'} />
```

### 4.6 Table (`ui/table.tsx`)

**`Table`**
- Props: `density` (`dense` ≈ 32px rows | `comfortable` 52px rows), `header` (`upper`, the default
  per DECISIONS §2 | `sentence`, the 13px ink-2 header), `stickyHeader`, `minWidth` (scrolls
  inside itself below it), `label` (a screen-reader caption).

**`THead` / `TBody`**
- No props.

**`Th`**
- Props: `numeric`, `width`.

**`Tr`**
- Props: `selected` (brand wash), `flagged` (alert wash), `onClick` (makes the row focusable and
  opens it with Enter or Space), `label`.

**`Td`**
- Props: `numeric` (right-aligned, tabular), `est` (italic), `alert` (red figure), `strong`,
  `secondary` (13px ink-2, truncated), `remove` (the 20px × column).

**`Cell`**
- Props: `top`, `sub`, `lead` (for example a `TierBadge`).

**`TotalRow`**
- Props: `rule` (`line | heavy`; heavy is the 2px ink rule for P&L).

**`KeyValueGrid`**
- Props: `rows` (`{key, label, value, est?, alert?}[]`).

**`ScrollX`** and **`EstLegend`**
- `ScrollX` scrolls its children sideways inside itself.
- `EstLegend` renders the "Estimates in italic, counts upright" note.

```tsx
<Table density="comfortable" minWidth={640} label="Stock">
  <THead><tr><Th>Ingredient</Th><Th numeric>Left (est.)</Th><Th numeric>Drift</Th></tr></THead>
  <TBody>
    <Tr onClick={() => open(r.id)} selected={sel === r.id} label={`Open ${r.name}`}>
      <Td><Cell lead={<TierBadge tier={r.tier} />} top={r.name} sub={r.unit} /></Td>
      <Td numeric est><Qty text={r.on_hand.qty} unit={r.unit} /></Td>
      <Td numeric alert={Math.abs(r.drift) > 15} strong={Math.abs(r.drift) >= 10}>{pct(r.drift, {sign:true})}</Td>
    </Tr>
  </TBody>
</Table>
```

### 4.7 Cards (`ui/card.tsx`)

**`Card`**
- Props: `soft`, `as`, `className`.

**`GridCard`**
- Props: `selected` (brand ring), `inactive` (faded), `onClick`, `label`.
- Renders a `<button>`.

**`Tile`**
- Props: `label`, `children` (the figure), `tone` (`plain | ok | bad`), `est`.

**`InfoPanel`**
- Props: `action` (an on-wash button).

**`EstNote`**, **`WarnBox`** (dashed alert) and **`DashedPanel`** (dashed neutral)
- No behaviour of their own; they are boxes around their children.

**`Empty`**
- Props: `action`, `roomy`.

**`Loading`**
- Props: `what`.

**`ErrorBox`**
- Props: `error`, `what`.
- Shows the server's `detail`, and says "not on the server yet" for a 404.

```tsx
<Tile label="Margin" tone={m === null ? 'plain' : m < 60 ? 'bad' : 'ok'}>{m === null ? '—' : `${m}%`}</Tile>
```

### 4.8 Drawer (`ui/drawer.tsx`)

**`Drawer`**
- Props: `open`, `onClose`, `title`, `context`, `width` (≥ 1280px, default 420), `compactWidth`
  (900–1279px, default 380), `tone` (`surface | canvas`), `titleSize` (`md | lg`), `footer`.
- **≥ 900px:** an inline panel in the layout, not an overlay. Esc closes it.
- **< 900px:** a full-screen sheet with a sticky header. It traps focus, closes on Esc, and
  returns focus to the opener.
- Placement: make it the last child of a `flex min-h-0 flex-1` row.

```tsx
<div className="flex min-h-0 flex-1">
  <PageBody>{list}</PageBody>
  <Drawer open={id !== null} onClose={() => setId(null)} title={item.name} context="Tier A · Chilled"
    width={400} tone="canvas" footer={<><Button className="flex-1">Write off</Button><Button variant="primary" className="flex-1">Save</Button></>}>
    …sections…
  </Drawer>
</div>
```

### 4.9 Banner (`ui/banner.tsx`)

**`Banner`**
- Props: `tone` (`stale | alert`), `children` (the text),
  `action` (`{label, onClick, pending?, pendingLabel?}`), `onDismiss` (omit it for a permanent strip).
- Roles: `alert` for the alert tone, `status` for stale.
- Below 640px the action and × wrap under the text.

**`BannerStack`**
- A wrapper that stacks banners in the order given. The shell renders the global stack, so
  screens rarely need one.

### 4.10 Charts (`ui/charts.tsx`)

The number props are used for geometry only; every visible label is a pre-formatted string.

**`Meter`**
- Props: `segments` (`{key, value, tone?: brand|muted|alert, label?}[]`), `label`.
- Used for the drift attribution split and "where the money went".

**`Bars`**
- Props: `bars` (`{key, label, value|null, valueLabel?, alert?}[]`), `height`, `label`.
- A `null` value is drawn as a dashed outline, never as a zero bar.

**`DivergingBars`**
- Props: the same as `Bars`, drawn around a zero line (cash over/under).

```tsx
<Bars label="Takings by day" bars={days.map(d => ({ key: d.date, label: dayShort(d.date), value: d.gross_pence, valueLabel: d.gross_pence === null ? undefined : gbp(d.gross_pence) }))} />
```

### 4.11 Page (`ui/page.tsx`)

**`PageHeader`**
- Props: `title`, `subtitle` (a sentence about what the page is), `saved`, `actions`.

**`SectionHead`**
- Props: `size` (`section` 16px | `panel` 18px), `right`, `as`.

**`Toolbar`**, **`MetaStrip`** and **`PageBody`**
- `Toolbar` is the filter row and `MetaStrip` the "as of…" strip.
- `PageBody` props: `flush`. It owns the page's scroll.

### 4.12 Hooks and helpers

- `useOperator()` returns `[name | null, setName]`. The name is stored in localStorage on this
  device and synced across components and tabs.
- `useLocation()`, `navigate(path, {replace?, query?})` and `href(path, query?)` handle routing.
- `useShell()` returns the `ShellResponse` or `undefined`. `SHELL_QUERY_KEY` is its query key.
- `useIsDocked()` / `useMediaQuery(q)` report breakpoints for behaviour, not style.
- `useFocusTrap(ref, active, onEscape, {trap})` is for any custom overlay-like surface.
- `cx(...)` joins class names.
- `lib/format` adds `gbp(pence)` and `ago(iso)` ("3 hours ago") to the existing helpers.
