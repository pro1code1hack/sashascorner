"""The only way a tool runs, and the only thing that writes `agent_action_log`.

Spec 9: *"Every action logged to `agent_action_log` with inputs, output and tool."*
That is only true if the logging is done by the machinery rather than by whoever
remembered. So `AgentRun.call()` is the single dispatch point, and it writes a row on
**every** path -- success, refusal, policy block, handler crash, awaiting-human. No
handler calls the log repository; none of them can, because `ToolContext` does not
carry it.

The log gets its own session on the audit-only engine (`policies.audit_only_engine`)
and commits immediately. Deliberate: a refusal that vanished because the surrounding
transaction rolled back is the one failure mode this table exists to prevent, and a
refusal is exactly the moment something is going wrong.

### Narration, and how "never computes" is enforced

Spec 9 job 2 is narration that "reads computed numbers, never computes them". Two
structural guarantees rather than a prompt instruction:

1. **There is no path from narration text into a numeric column.** The model's prose
   goes to `agent_action_log.output` and to a human. Nothing parses it into a figure
   and nothing stores it as one. The arithmetic is in `domain/` and the repositories,
   ran before the model was called.
2. **Every figure the model prints is checked against the figures the tools
   computed.** `verify_figures` extracts numbers from the narration and compares them
   to `ToolResult.figures`. An invented total is caught and the narration is marked
   `outcome = FAILED` with the unsupported figures named, rather than being believed.

Without an `ANTHROPIC_API_KEY` the run uses `deterministic_narration`, which builds
the same report from the same facts with a template. That is a supported state, not a
degraded one: the numbers are identical either way, because the model was never the
thing computing them.
"""

from __future__ import annotations

import re
import uuid
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any, cast

from sqlalchemy.orm import sessionmaker

from cafeops.agent import tools as _tools  # noqa: F401 -- importing registers the allowlist
from cafeops.agent.policies import (
    AgentPolicyError,
    ToolContext,
    ToolKind,
    ToolRefused,
    ToolResult,
    ToolSpec,
    WriteAttemptBlocked,
    allowed_names,
    anthropic_tool_definitions,
    audit_only_engine,
    guard_output,
    read_only_engine,
    read_only_scope,
    spec_for,
)
from cafeops.config import settings
from cafeops.db.repositories.agent_log import SqlAgentLogRepository
from cafeops.domain.types import AgentProposal, AgentToolOutcome
from cafeops.services.agent_proposals import agent_for, insert_proposal

#: The narration prompt. It explains the boundary because a model that understands
#: why it must not compute produces better prose -- but nothing here is what ENFORCES
#: the boundary. The enforcement is the allowlist, the read-only connection and
#: `verify_figures`.
NARRATION_SYSTEM = """\
You explain numbers that have already been calculated for a small independent cafe \
in Dundee. The owner reads your sentences on a phone between customers.

Rules, in order of importance:

1. NEVER calculate anything. Do not add, subtract, multiply, average, project or \
convert. Every figure you write must appear verbatim in a tool result. If you want a \
number that no tool gave you, say what is missing instead of deriving it. A checker \
compares every figure in your answer against the tool output and flags any that is \
not there.
2. Say which of two opposite fixes applies. Expiry write-offs mean "you are ordering \
too much"; a measurement gap means "the recipe or the waste factor is wrong". \
Getting this backwards sends somebody to fix the wrong thing.
3. Lead with the thing worth acting on, name the money, and say what to do. Two to \
five sentences. No headings, no bullet lists unless you are listing more than three \
items, no preamble.
4. A figure reported as unknown stays unknown. Never substitute zero for it.

Call the tools you need, then answer in plain English."""

#: A number as it might appear in prose: money, a percentage, a quantity, a rank.
_FIGURE = re.compile(
    r"(?:GBP\s?|£)\s?-?\d[\d,]*(?:\.\d+)?|"  # money
    r"-?\d[\d,]*(?:\.\d+)?\s?%|"  # percentage
    r"#\d+|"  # a rank
    r"-?\d[\d,]*\.\d+|"  # a decimal quantity
    r"\b\d{1,3}(?:,\d{3})+\b|"  # a grouped integer
    r"\b\d+\b"  # a bare integer
)

#: Small integers are counts, dates and ordinals in ordinary prose ("two of three
#: days", "2026"). Checking them produces noise, not findings.
_TRIVIAL_MAX = 200


def _normalise_figure(raw: str) -> str:
    text = raw.strip().replace(",", "").replace("£", "GBP ").replace("GBP", "GBP ")
    return " ".join(text.split()).upper()


def verify_figures(narration: str, allowed: Sequence[str]) -> tuple[str, ...]:
    """Figures in the narration that no tool computed. Empty tuple is the good case.

    This is the check that turns "reads computed numbers, never computes them" from a
    prompt instruction into something the system knows the answer to. A model that
    writes "about GBP 210 of waste" against a computed GBP 190.27 is caught here.
    """
    permitted = {_normalise_figure(f) for f in allowed}
    # A permitted "GBP 190.27" also permits the bare "190.27" that prose may use.
    for figure in list(permitted):
        permitted.add(figure.removeprefix("GBP ").strip())
        permitted.add(figure.rstrip("%").strip())
        permitted.add(figure.lstrip("+").strip())
        permitted.add(figure.lstrip("#").strip())
    unsupported: list[str] = []
    for match in _FIGURE.finditer(narration):
        raw = match.group(0)
        normalised = _normalise_figure(raw)
        candidates = {
            normalised,
            normalised.removeprefix("GBP ").strip(),
            normalised.rstrip("%").strip(),
            normalised.lstrip("#").strip(),
        }
        if candidates & permitted:
            continue
        # The small-integer bypass applies ONLY to a bare number. A percentage, a
        # money amount or a rank is always a claim about the business, never an
        # incidental count -- found by running it: "roughly 18% of purchases" slipped
        # through when '%' was stripped before this check.
        sigilless = not any(c in normalised for c in ("%", "#")) and "GBP" not in normalised
        if sigilless and "." not in normalised:
            try:
                if abs(int(normalised)) <= _TRIVIAL_MAX:
                    continue
            except ValueError:
                pass
        unsupported.append(raw.strip())
    return tuple(dict.fromkeys(unsupported))


@dataclass(slots=True)
class CallRecord:
    """One dispatch, as it was logged."""

    tool_name: str
    outcome: AgentToolOutcome
    inputs: dict[str, Any]
    output: str | None
    refusal_reason: str | None
    proposal: AgentProposal | None
    log_id: int

    @property
    def refused(self) -> bool:
        return self.outcome is AgentToolOutcome.REFUSED


@dataclass(slots=True)
class NarrationResult:
    run_id: str
    model: str
    text: str
    unsupported_figures: tuple[str, ...] = ()
    calls: list[CallRecord] = field(default_factory=list)
    used_api: bool = False
    note: str | None = None

    @property
    def trustworthy(self) -> bool:
        return not self.unsupported_figures


class AgentRun:
    """One bounded agent run. Open it, call tools through it, close it.

    Nothing outside this class may dispatch a tool, and nothing outside it writes the
    log. Both engines are created here so their guards (`read_only_engine`,
    `audit_only_engine`) are always in force -- there is no unguarded path because
    there is no unguarded engine.
    """

    def __init__(
        self,
        *,
        purpose: str,
        model: str | None = None,
        run_id: str | None = None,
        agent: str | None = None,
    ) -> None:
        self.run_id = run_id or uuid.uuid4().hex[:16]
        self.purpose = purpose
        #: Which agent this is (drift_explainer | import_assistant | channel_reporter |
        #: basket_stager), stamped on every log row and proposal. Derived from the
        #: purpose when not given, so existing callers keep working.
        self.agent = agent or agent_for(purpose)
        self.model = model or settings.agent_model
        self.started_at = datetime.now(UTC)

        self._read_engine = read_only_engine()
        self._read_session, self._reads = read_only_scope(self._read_engine)

        self._audit_engine = audit_only_engine()
        self._audit_session = sessionmaker(
            bind=self._audit_engine, expire_on_commit=False, future=True
        )()
        self.log = SqlAgentLogRepository(self._audit_session)

        self.calls: list[CallRecord] = []
        self.figures: list[str] = []
        self.proposals: list[AgentProposal] = []

    # -- lifecycle ---------------------------------------------------------

    def close(self) -> None:
        self._read_session.close()
        self._audit_session.close()
        self._read_engine.dispose()
        self._audit_engine.dispose()

    def __enter__(self) -> AgentRun:
        return self

    def __exit__(self, *_exc: object) -> None:
        self.close()

    # -- the single dispatch point ----------------------------------------

    def call(self, tool_name: str, args: dict[str, Any] | None = None) -> CallRecord:
        """Run one tool, or refuse, and log either way.

        The order matters: resolve the name first, so a tool that is not on the
        allowlist is refused before any handler exists to run.
        """
        args = dict(args or {})
        try:
            spec = spec_for(tool_name)
        except ToolRefused as exc:
            return self._record(
                tool_name,
                args,
                AgentToolOutcome.REFUSED,
                output=None,
                refusal_reason=exc.reason,
            )

        if len(self.calls) >= settings.agent_max_tool_calls:
            return self._record(
                tool_name,
                args,
                AgentToolOutcome.REFUSED,
                output=None,
                refusal_reason=(
                    f"run {self.run_id} has already made {len(self.calls)} tool calls, which is "
                    f"the ceiling (CAFEOPS_AGENT_MAX_TOOL_CALLS). A looping agent is an agent "
                    "spending money."
                ),
            )

        ctx = ToolContext(run_id=self.run_id, reads=self._reads)
        try:
            result = guard_output(spec, spec.handler(ctx, args))
        except WriteAttemptBlocked as exc:
            # The connection refused a write. This is the boundary firing, so it is a
            # REFUSED row rather than a FAILED one: nothing went wrong, something was
            # stopped.
            return self._record(
                tool_name,
                args,
                AgentToolOutcome.REFUSED,
                output=None,
                refusal_reason=f"write blocked at the connection: {exc}",
            )
        except AgentPolicyError as exc:
            return self._record(
                tool_name,
                args,
                AgentToolOutcome.REFUSED,
                output=None,
                refusal_reason=f"{type(exc).__name__}: {exc}",
            )
        except Exception as exc:  # a handler bug is FAILED, and still logged
            return self._record(
                tool_name,
                args,
                AgentToolOutcome.FAILED,
                output=f"{type(exc).__name__}: {exc}",
                refusal_reason=None,
            )

        self.figures.extend(result.figures)
        if result.proposal is not None:
            self.proposals.append(result.proposal)

        outcome = (
            AgentToolOutcome.AWAITING_HUMAN
            if (spec.stops_at_human or result.proposal is not None)
            else AgentToolOutcome.OK
        )
        text = result.text
        if result.awaiting_human:
            text = f"{text}\n\nSTOPS HERE: {result.awaiting_human}"
        return self._record(
            tool_name,
            args,
            outcome,
            output=text,
            refusal_reason=None,
            proposal=result.proposal,
        )

    def _record(
        self,
        tool_name: str,
        inputs: dict[str, Any],
        outcome: AgentToolOutcome,
        *,
        output: str | None,
        refusal_reason: str | None,
        proposal: AgentProposal | None = None,
    ) -> CallRecord:
        proposal_ref = None if proposal is None else f"{proposal.kind}:{proposal.subject_ref}"
        log_id = self.log.log(
            run_id=self.run_id,
            tool_name=tool_name,
            inputs=inputs,
            outcome=outcome,
            output=output,
            purpose=self.purpose,
            refusal_reason=refusal_reason,
            proposal_ref=proposal_ref,
            model=self.model,
            agent=self.agent,
        )
        if proposal is not None:
            # The agent's only write besides its log: a WAITING proposal, INSERT only
            # (the audit engine refuses anything else on that table). Same commit as
            # the log row, so a proposal never exists without the call that made it.
            insert_proposal(
                self._audit_session,
                run_id=self.run_id,
                log_id=log_id,
                agent=self.agent,
                proposal=proposal,
                figures=list(dict.fromkeys(self.figures)),
            )
        # Commit each row on its own. A refusal that disappeared with a rolled-back
        # transaction would defeat the point of having the table.
        self._audit_session.commit()
        record = CallRecord(
            tool_name=tool_name,
            outcome=outcome,
            inputs=inputs,
            output=output,
            refusal_reason=refusal_reason,
            proposal=proposal,
            log_id=log_id,
        )
        self.calls.append(record)
        return record

    # -- the facts pack ----------------------------------------------------

    def gather(self, calls: Sequence[tuple[str, dict[str, Any]]]) -> str:
        """Run READ tools and return their output as one block of text.

        Deterministic. This is where every number in a narration comes from, and it
        happens before the model is involved at all.
        """
        parts: list[str] = []
        for name, args in calls:
            record = self.call(name, args)
            # No square brackets: this text is printed through rich, which would read
            # `[read_drift_report]` as a style tag and swallow the label whole. Found by
            # running it and seeing an anonymous " (OK)" heading.
            label = f"--- {name} ({record.outcome.value})"
            parts.append(f"{label}\n{record.output or record.refusal_reason or '(no output)'}")
        return "\n\n".join(parts)

    # -- narration ---------------------------------------------------------

    def narrate(
        self,
        question: str,
        *,
        facts: str,
        tool_names: Sequence[str] | None = None,
    ) -> NarrationResult:
        """Turn computed numbers into a sentence somebody acts on.

        Falls back to `deterministic_narration` with no API key. Either way the
        figures are checked against what the tools computed.
        """
        if not settings.anthropic_api_key:
            text = deterministic_narration(question=question, facts=facts)
            result = NarrationResult(
                run_id=self.run_id,
                model="deterministic-template",
                text=text,
                unsupported_figures=(),
                calls=list(self.calls),
                used_api=False,
                note=(
                    "No ANTHROPIC_API_KEY is set, so this report was assembled from the "
                    "same computed facts by a template rather than by the model. The "
                    "numbers are identical: the model was never what computed them."
                ),
            )
            self._log_narration(result, facts=facts)
            return result

        return self._narrate_via_api(question, facts=facts, tool_names=tool_names)

    def _narrate_via_api(
        self,
        question: str,
        *,
        facts: str,
        tool_names: Sequence[str] | None,
    ) -> NarrationResult:
        import anthropic
        from anthropic.types import MessageParam, TextBlock, ToolParam, ToolUseBlock

        client = anthropic.Anthropic(
            api_key=settings.anthropic_api_key,
            timeout=NARRATION_CALL_TIMEOUT_SECONDS,
            max_retries=NARRATION_MAX_RETRIES,
        )
        # Read tools only. A narration has no business staging a basket or emitting a
        # proposal, so the surface offered is narrowed to the READ kind -- the
        # allowlist filtered down, not widened.
        offered = (
            list(tool_names)
            if tool_names is not None
            else [name for name in allowed_names() if spec_for(name).kind is ToolKind.READ]
        )
        definitions = cast("list[ToolParam]", anthropic_tool_definitions(offered))

        messages: list[MessageParam] = [
            {
                "role": "user",
                "content": (
                    f"{question}\n\nThese figures are already computed. Quote them; do "
                    f"not derive anything new.\n\n{facts}"
                ),
            }
        ]
        text_parts: list[str] = []
        note: str | None = None

        for _ in range(settings.agent_max_tool_calls):
            response = client.messages.create(
                model=self.model,
                max_tokens=settings.agent_max_tokens,
                system=NARRATION_SYSTEM,
                thinking={"type": "adaptive"},
                tools=definitions,
                messages=messages,
            )
            if response.stop_reason == "refusal":
                note = "the model declined to answer"
                break
            # The full content list goes back, thinking blocks included: the API needs
            # them echoed unchanged to continue on the same model.
            messages.append({"role": "assistant", "content": response.content})
            tool_uses = [b for b in response.content if isinstance(b, ToolUseBlock)]
            text_parts += [b.text for b in response.content if isinstance(b, TextBlock)]
            if not tool_uses:
                break
            results: list[Any] = []
            for block in tool_uses:
                raw_input = block.input if isinstance(block.input, dict) else {}
                record = self.call(block.name, dict(raw_input))
                results.append(
                    {
                        "type": "tool_result",
                        "tool_use_id": block.id,
                        "content": record.output or record.refusal_reason or "(no output)",
                        "is_error": record.outcome
                        in (AgentToolOutcome.REFUSED, AgentToolOutcome.FAILED),
                    }
                )
            messages.append({"role": "user", "content": results})
        else:
            note = (
                f"stopped after {settings.agent_max_tool_calls} model turns without a final answer"
            )

        text = "\n\n".join(p.strip() for p in text_parts if p.strip())
        result = NarrationResult(
            run_id=self.run_id,
            model=self.model,
            text=text,
            unsupported_figures=verify_figures(text, self.figures),
            calls=list(self.calls),
            used_api=True,
            note=note,
        )
        self._log_narration(result, facts=facts)
        return result

    def _log_narration(self, result: NarrationResult, *, facts: str) -> None:
        """The narration itself is an action, so it is a row like any other.

        `FAILED` when a figure could not be traced back to a tool result. The prose is
        kept -- a reader needs to see what was said -- but the row says not to trust it.
        """
        outcome = AgentToolOutcome.OK if result.trustworthy else AgentToolOutcome.FAILED
        self.log.log(
            run_id=self.run_id,
            tool_name="narrate",
            inputs={
                "facts_chars": len(facts),
                "figures_available": len(set(self.figures)),
                "used_api": result.used_api,
            },
            outcome=outcome,
            output=result.text,
            purpose=self.purpose,
            refusal_reason=(
                None
                if result.trustworthy
                else (
                    "narration contained figure(s) no tool computed: "
                    + ", ".join(result.unsupported_figures)
                    + ". Reads computed numbers, never computes them (spec 9)."
                )
            ),
            model=result.model,
            agent=self.agent,
        )
        self._audit_session.commit()


# ==========================================================================
# The no-API-key path
# ==========================================================================


#: One narration call's ceiling. `settings.agent_max_tokens` (1200 by default) is a
#: short answer; at any sane output rate it is well inside a minute, so a call past
#: 60 s is a hung connection. `narrate()` makes up to `settings.agent_max_tool_calls`
#: calls, so the worst case is bounded at timeout x turns x (1 + retries).
NARRATION_CALL_TIMEOUT_SECONDS = 60.0
NARRATION_MAX_RETRIES = 2


def deterministic_narration(*, question: str, facts: str) -> str:
    """The same report, assembled by a template.

    Not a placeholder. With no key this is what the owner reads, and it contains
    every number the model would have had, because the numbers were computed before
    either path ran. What is lost is the prose, not the information.
    """
    header = (
        f"{question}\n\n"
        "Assembled without the model (no ANTHROPIC_API_KEY). Every figure below was "
        "computed by the deterministic code that the narration would have read:"
    )
    body = facts.strip() or "(no facts were gathered)"
    tail = (
        "Read it the way the tool output states it: where expiry write-offs explain most "
        "of a drift gap the fix is to order less, and where they do not the fix is the "
        "recipe or the waste factor. The two are opposite."
    )
    return f"{header}\n\n{body}\n\n{tail}"


# ==========================================================================
# Convenience entry points
# ==========================================================================

#: The READ calls a drift narration needs. Named so the CLI, the bot and a job all
#: gather the same facts rather than each choosing a subset.
DRIFT_FACT_CALLS: tuple[tuple[str, dict[str, Any]], ...] = (
    ("read_drift_report", {"limit": 6}),
    ("read_expiry_writeoffs", {}),
)

CHANNEL_FACT_CALLS: tuple[tuple[str, dict[str, Any]], ...] = (("read_channel_performance", {}),)


def narrate_drift(*, model: str | None = None) -> NarrationResult:
    """Explain the drift report: which ingredient, how much, and which fix."""
    with AgentRun(
        purpose="narrate the drift and waste report", model=model, agent="drift_explainer"
    ) as run:
        facts = run.gather(list(DRIFT_FACT_CALLS))
        return run.narrate(
            "What is the most important thing in this week's stock report, and what "
            "should the owner do about it?",
            facts=facts,
        )


def narrate_channels(*, model: str | None = None) -> NarrationResult:
    """Explain Deliveroo / Just Eat: contribution, ROAS, and the conversion outliers."""
    with AgentRun(
        purpose="narrate channel performance", model=model, agent="channel_reporter"
    ) as run:
        facts = run.gather(list(CHANNEL_FACT_CALLS))
        return run.narrate(
            "What should the owner change about Deliveroo and Just Eat this week?",
            facts=facts,
        )


def registered_tools() -> list[ToolSpec]:
    """The allowlist, for the CLI. Importing this module is what registers it."""
    return [spec_for(name) for name in allowed_names()]


__all__ = [
    "DRIFT_FACT_CALLS",
    "AgentRun",
    "CallRecord",
    "NarrationResult",
    "ToolResult",
    "deterministic_narration",
    "narrate_channels",
    "narrate_drift",
    "registered_tools",
    "verify_figures",
]
