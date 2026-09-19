# Agent F — Channels and the bounded agent (v2)

**Owns:** `cafeops/integrations/channels/`, `cafeops/agent/`,
`db/repositories/channel.py`, `db/repositories/agent_log.py`, `jobs/channel_sync.py`

Two independent halves. Do the channels half first — it is smaller and the agent half
depends on having a real job to do.

## Part 1 — `integrations/channels/`

`ChannelSource` protocol with **two** implementations, per spec §4.6:

1. **`CsvChannelSource`** — build this first and make it good. The owner has portal
   logins and exports manually, so this is the path that works *today*. Deliveroo and
   Just Eat export different column sets; write a small column-mapping layer rather than
   one brittle parser, and reject a file you cannot map instead of guessing.
2. **`BrowserAgentChannelSource`** — pulls the same reports by driving a browser. Spec
   §4.6 says assume partner API access never arrives, and §9 says this is the one place
   a browser agent is clearly the right tool. **It will break**; falling back to CSV
   must be a config change, not a rewrite.

Models exist: `channel_metric` (unique on channel+date), `channel_item_metric` (unique
on channel+date+item). Both carry `source` so a figure typed from a PDF and one scraped
by an agent are never silently mixed.

Derived numbers worth computing (spec §4.6): ROAS, contribution after commission **and**
ad spend, and **items that rank well but convert badly** — that last one is a photo or
description problem, not a product problem, and it is the most actionable thing here.

`ChannelMetric.net_pence` deliberately returns `None` when commission or spend is
unknown rather than subtracting what it has. Same rule as invariant 8.

## Part 2 — `cafeops/agent/`

**Read spec §9 in full before writing a line.** The shape is the point:

> Deterministic stays deterministic. An LLM in the stock-maths path makes an
> unauditable system that orders differently on Tuesday than it did on Monday.

Three jobs, each behind a whitelisted tool:

1. Browser automation where no API exists (channel reports, supplier portal baskets).
   Output is data or a **staged** basket — never a submitted payment.
2. Narration and anomaly explanation. Reads computed numbers, never computes them.
   Turning a drift report into *"oat milk is 22% off and expiry write-offs account for
   most of it — you are ordering too much, not mis-measuring"* is the whole value.
3. Import assistance: proposing template groupings and `waste_factor` adjustments,
   always as an `AgentProposal` a human confirms.

### The hard rules, and how to make them structural

The agent must never write to `stock_movement`, `purchase_order` or any composition
table. **Do not rely on a prompt for this.** Build it so a write is unreachable:

- Tools are an explicit allowlist. Anything not on it is refused, and the refusal is
  logged with `outcome = REFUSED` — those are the interesting rows.
- Read tools get read-only repository access. A tool that needs to change something
  emits an `AgentProposal` (`domain/types.py`) instead.
- **Every** action goes to `agent_action_log` with run_id, tool, inputs, output,
  outcome, model. `AgentLogRepository` is in the protocols.
- Anything that would spend money stops at a human.

`integrations/suppliers/` already has an `OrderChannelAdapter` whose `BROWSER_AGENT`
implementation stops at a filled basket with `requires_human_completion=True`. Reuse it;
do not build a second path to a checkout.

### Verify by demonstrating the refusals

No tests. Show real output where the agent **attempts** a write-path tool and is
refused, with the `agent_action_log` rows to prove it — plus one good narration of the
seeded drift data, which contains a genuine over-ordering case (£190.27 of expiry
write-offs) for it to find.

Use the Claude API via the Anthropic SDK; load the `claude-api` skill for current model
ids and tool-use patterns rather than guessing.
