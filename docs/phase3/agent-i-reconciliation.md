# Agent I — Phase 2 reconciliation: structured meaning, and the gaps agents left

**Owns:** `cafeops/domain/` (types.py and protocols.py by exception — see below),
`cafeops/services/`, `cafeops/bot/views.py` and `cafeops/bot/formatters.py`,
`cafeops/jobs/`

You are closing the failure this codebase has hit **four separate times**: meaning that
exists only as an English sentence, which every other surface then has to guess at.

## Read first

`CLAUDE.md` §13 (the twelve invariants), `ARCHITECTURE.md` §8G.1 (two agents inventing
the same side channel), §8A.3 (a queued gap that is now reachable), §8F.2.

## YOU MAY EDIT `domain/types.py` AND `protocols.py`

That exception exists only for this task, and only for the changes below. Everything
else in those files stays as it is. Say in your summary exactly what you changed there.

## 1. Pair every explanatory prose field with a stable code

`DeliveryReceipt.warnings`, `OrderSuggestion.notes`, `ForecastResult.confidence_reasons`
and `GateDecision.reason` are free English sentences. The Russian bot has to
substring-match them to render anything, so an unclassified warning is reported only as
a count ("ещё N замечаний записано в журнал" — "N more notes were written to the log").
A reword upstream silently degrades the owner's message.

Give each a stable code alongside the text — `tuple[tuple[str, str], ...]` of
`(code, text)`, or a small enum per field, whichever reads better. `CapKind` and
`LowConfidenceKind` (already in `db/models/enums.py`, re-exported from `domain/types.py`)
are the pattern to follow: **derive the code from the same condition that writes the
sentence, never by parsing the sentence back.**

Then delete the regex classifiers in `cafeops/bot/views.py` (`_classify_cap`,
`_confidence_by_name` and friends) and have the formatters branch on the codes.

## 2. `GateDecision` needs a `revoke_cause`

`ARCHITECTURE.md` §8A.3 recorded this as queued and the bot has now made it reachable.
Two problems:

- A 10–15% revoke carries `alert=False`, so a **revocation is silent**. Losing
  auto-ordering is a material change in behaviour — orders that were being drafted stop
  being drafted — and the owner should hear about it even at a lower severity than the
  >15% "your figures are untrustworthy" alarm.
- `GateDecision.reason` is prose, so the bot cannot say **why** it was revoked: drift
  above tolerance, or the ingredient dropping out of tier A.

Add a `revoke_cause` enum and make the revoke visible. Do not weaken the gate itself:
the rule that an ingredient needs two consecutive counts under 10% is load-bearing, and
`ARCHITECTURE.md` §8A.1 explains why a 10–15% count revokes an existing grant.

## 3. Surface the retail emergency in the digest

`build_split` computes emergency lines and the job report counts them, but the morning
digest never shows them. The retail path now fires on the seeded data
(`cafeops simulate --commit` writes `tesco_routing` rows), so there is real data. Spec
§4.4: the accumulated premium is the argument for fixing the ordering cadence — it
should be impossible to miss, in Russian, with the cumulative cost.

## 4. Decide what a tier C `LOW` answer does

The digest lists tier C items the staff marked `LOW`, but **nothing adds them to an
order**. Spec §4.7 says tier C is "never calculated — yes/no checklist", which justifies
not forecasting them; it does not obviously mean a `LOW` answer should be inert.

A checklist nobody acts on trains people to stop filling it in. Propose and implement
the smallest honest thing: most likely a `LOW` item appears on the relevant supplier's
draft order at a human-chosen quantity, clearly marked as coming from the checklist
rather than a forecast — never silently sized by a number the system does not have.
Say what you chose and why.

## Rules

- **Write NO TESTS.** Verify by running and showing real output, including the Russian.
- **Use an ISOLATED database:**
  `export CAFEOPS_DATABASE_URL="sqlite+pysqlite:///$PWD/agenti.db"`, then
  `rm -f agenti.db && uv run alembic upgrade head && uv run cafeops seed --demo && uv run cafeops drift --backfill`.
  Never rebuild the shared `cafeops.db`. Delete yours when done.
- `uv run ruff check . && uv run ruff format --check .` and
  `uv run mypy cafeops/domain/ cafeops/services/` must pass with **zero new
  `type: ignore`**.
- `domain/` stays pure: no SQLAlchemy, no I/O, no `config` import.
- Every schema change gets an Alembic migration. `cafeops/bot/*` has RUF001/2/3 disabled
  because the Cyrillic is the text — do not transliterate anything to please a linter.
- Do not touch `web/` or `cafeops/api/`; other agents own those.
