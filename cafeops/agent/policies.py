"""The boundary the agent cannot cross, built so crossing it is unreachable.

Spec 9 states the rules; this module makes them structural. The brief is explicit
that a prompt is not an implementation: *"Do not rely on a prompt for this. Build it
so a write is unreachable."* Four devices, each independent of the others, so no
single mistake removes the boundary:

1. **The allowlist is the only route to a handler.** `dispatch` resolves a name in
   `_REGISTRY` or raises `ToolRefused`. There is no fallback, no `getattr`, no
   dynamic import. A model asking for `write_stock_movement` gets a refusal because
   the name is absent, not because a prompt asked it not to.

2. **A read tool is handed no object it could write through.** `ToolContext` carries
   a `ReadOnlyRepositories` bundle and nothing else -- no `Session`, no writable
   repository, no engine. The write capability is not merely forbidden, it is not in
   scope. This is the same move as
   `SqlParLevelRepository.set_auto_order(..., True)` raising (ARCHITECTURE.md 8A.2)
   and the purchase-order `CHECK` constraint (ARCHITECTURE.md 5): with no test
   suite, an invariant survives by being unrepresentable.

3. **The connection itself refuses to write.** `read_only_engine` installs a
   `before_cursor_execute` hook that raises `WriteAttemptBlocked` on anything that
   is not a read. Raw SQL is blocked too, which is the point -- device 2 protects
   against a handler misusing an object it was given, and device 3 protects against
   a handler acquiring one some other way.

4. **The audit writer can write to exactly one table.** The log needs a real write,
   so it gets its own engine whose hook rejects any statement touching a table other
   than `agent_action_log`. The one permitted write cannot become a second route in.

`stock_movement`, `purchase_order` and the composition tables are named explicitly
in `FORBIDDEN_TABLES` as well -- redundant given device 3, and kept because a named
list is what a reviewer reads.
"""

from __future__ import annotations

import enum
import re
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from typing import Any

from sqlalchemy import Engine, event
from sqlalchemy.orm import Session, sessionmaker

from cafeops.config import settings
from cafeops.db.base import create_db_engine
from cafeops.domain.types import AgentProposal

# ==========================================================================
# The refusals
# ==========================================================================


class AgentPolicyError(Exception):
    """Base for every boundary this module enforces."""


class ToolRefused(AgentPolicyError):
    """The requested tool is not on the allowlist, or is not allowed to be called.

    Logged with `outcome = REFUSED`. These are the interesting rows in
    `agent_action_log`: they are the evidence the allowlist did its job.
    """

    def __init__(self, tool_name: str, reason: str) -> None:
        super().__init__(f"{tool_name}: {reason}")
        self.tool_name = tool_name
        self.reason = reason


class WriteAttemptBlocked(AgentPolicyError):
    """A statement that is not a read reached a read-only agent connection.

    Raised by the engine hook, so it fires for ORM writes and raw SQL alike.
    """


class ProposalRequired(AgentPolicyError):
    """A PROPOSE tool returned something other than an `AgentProposal`.

    Spec 9: a tool that needs to change something emits a proposal a human
    confirms. Returning anything else would mean the change had already happened.
    """


class HumanApprovalRequired(AgentPolicyError):
    """The action would spend money. Spec 9: it stops here, at a person."""


# ==========================================================================
# What the agent may never touch
# ==========================================================================

#: Named for the reviewer. Device 3 blocks writes to every table regardless.
FORBIDDEN_TABLES: frozenset[str] = frozenset(
    {
        # stock (invariant 12: the ledger is append-only, and not by the agent)
        "stock_movement",
        "stock_batch",
        "stock_count",
        "drift_observation",
        "par_level",
        # orders (invariant 1: nothing is ordered without human confirmation)
        "purchase_order",
        "po_line",
        "tesco_routing",
        # composition (invariant 3: recipe edits are effective-dated, by a human)
        "drink_template",
        "template_component",
        "size_profile",
        "variant_axis",
        "variant_option",
        "modifier",
        "menu_item",
        "manual_recipe_line",
        "menu_item_cost",
        "legacy_staged_recipe",
        # money and prices
        "ingredient",
        "ingredient_price",
        "supplier",
        "supplier_product",
        "sale",
        "channel_metric",
        "channel_item_metric",
    }
)

#: The only table the audit writer may touch freely.
AUDIT_TABLE = "agent_action_log"

#: The one other table the audit writer may write -- and only INSERT into. A proposal
#: is an audit artefact (what the agent suggested), not stock, orders or composition,
#: so it rides on the audit engine; but status changes are a human's, made through
#: `services/agent_proposals.py` on the normal engine, so UPDATE and DELETE are refused
#: here. DECISIONS.md 7, shell-agents spec 6.1.
PROPOSAL_TABLE = "agent_proposal"

#: Statement keywords that make something not a read, wherever they appear.
_WRITE_KEYWORDS = re.compile(
    r"\b(insert|update|delete|replace|drop|alter|create|truncate|attach|vacuum|reindex)\b",
    re.IGNORECASE,
)
#: The first keyword a read may start with.
_READ_STARTS = ("select", "with", "pragma", "explain", "values")
_COMMENTS = re.compile(r"(--[^\n]*)|(/\*.*?\*/)", re.DOTALL)


def _first_keyword(sql: str) -> str:
    stripped = _COMMENTS.sub(" ", sql).strip().lstrip("(").strip()
    return stripped.split(None, 1)[0].lower() if stripped else ""


def classify_statement(sql: str) -> str:
    """`"read"` or the write keyword that disqualified it. Pure; used by both hooks."""
    body = _COMMENTS.sub(" ", sql)
    match = _WRITE_KEYWORDS.search(body)
    if match is not None:
        return match.group(1).lower()
    if _first_keyword(sql) not in _READ_STARTS:
        return _first_keyword(sql) or "empty"
    return "read"


def read_only_engine(url: str | None = None) -> Engine:
    """An engine that raises on anything that is not a read.

    The hook is on `before_cursor_execute`, which every statement passes through --
    ORM flush, Core insert, `session.execute(text("DELETE ..."))`, all of it. It is
    dialect-agnostic on purpose: a SQLite `?mode=ro` URI would do the same job today
    and stop doing it the day this moves to Postgres.
    """
    engine = create_db_engine(url or settings.database_url)

    @event.listens_for(engine, "before_cursor_execute")
    def _refuse_writes(
        _conn: Any,
        _cursor: Any,
        statement: str,
        _parameters: Any,
        _context: Any,
        _executemany: bool,
    ) -> None:
        verdict = classify_statement(statement)
        if verdict != "read":
            raise WriteAttemptBlocked(
                f"the agent's connection is read-only and this statement is a "
                f"{verdict.upper()}: {statement.strip()[:200]}"
            )

    return engine


def audit_only_engine(url: str | None = None) -> Engine:
    """An engine whose only permitted write is to `agent_action_log`.

    The log has to be written for spec 9's "every action logged" to mean anything,
    and that write must not become a second way in. A write naming any other table
    is refused here, so the audit trail cannot be used as a side channel.
    """
    engine = create_db_engine(url or settings.database_url)

    @event.listens_for(engine, "before_cursor_execute")
    def _audit_table_only(
        _conn: Any,
        _cursor: Any,
        statement: str,
        _parameters: Any,
        _context: Any,
        _executemany: bool,
    ) -> None:
        verdict = classify_statement(statement)
        if verdict == "read":
            return
        lowered = statement.lower()
        if re.search(rf"\b{PROPOSAL_TABLE}\b", lowered):
            if verdict != "insert" or not re.match(
                rf"\s*insert\s+into\s+\"?{PROPOSAL_TABLE}\"?[\s(]", lowered
            ):
                raise WriteAttemptBlocked(
                    f"the agent's audit connection may only INSERT into {PROPOSAL_TABLE}; "
                    f"refused a {verdict.upper()}: {statement.strip()[:200]}. Deciding a "
                    "proposal is a person's job (DECISIONS.md 7)."
                )
        elif AUDIT_TABLE not in lowered:
            raise WriteAttemptBlocked(
                f"the agent's audit connection may only write {AUDIT_TABLE}; refused a "
                f"{verdict.upper()}: {statement.strip()[:200]}"
            )
        for table in FORBIDDEN_TABLES:
            if re.search(rf"\b{re.escape(table)}\b", lowered):
                raise WriteAttemptBlocked(
                    f"the agent's audit connection refused a {verdict.upper()} naming "
                    f"{table!r}, which is on FORBIDDEN_TABLES"
                )

    return engine


# ==========================================================================
# Read-only repository access
# ==========================================================================


@dataclass(frozen=True, slots=True)
class ReadOnlyRepositories:
    """Everything a read tool is given, and nothing more.

    Deliberately not a `Session`. A handler holding this cannot construct a write
    even by accident, because there is no object here with an `add` or a `commit`.
    The repositories it does hold are themselves built on the read-only engine, so
    calling a write method on one raises from the connection rather than succeeding.
    """

    channels: Any
    drift: Any
    ingredients: Any
    stock: Any
    par: Any
    #: Kept so a handler can run a read-only `select`. Writing through it raises.
    query: Callable[..., Any]


def read_only_scope(engine: Engine) -> tuple[Session, ReadOnlyRepositories]:
    """Open a read-only session and the repository bundle over it.

    `autoflush=False` because an autoflush on a read-only connection would raise
    from inside an innocent-looking `select`, which is a confusing way to be right.
    """
    from cafeops.db.repositories.channel import SqlChannelRepository
    from cafeops.db.repositories.drift import SqlDriftRepository
    from cafeops.db.repositories.ingredient import SqlIngredientRepository
    from cafeops.db.repositories.par import SqlParLevelRepository
    from cafeops.db.repositories.stock import SqlStockRepository

    factory = sessionmaker(bind=engine, expire_on_commit=False, autoflush=False, future=True)
    session = factory()
    reads = ReadOnlyRepositories(
        channels=SqlChannelRepository(session),
        drift=SqlDriftRepository(session),
        ingredients=SqlIngredientRepository(session),
        stock=SqlStockRepository(session),
        par=SqlParLevelRepository(session),
        query=session.execute,
    )
    return session, reads


# ==========================================================================
# The allowlist
# ==========================================================================


class ToolKind(enum.Enum):
    """What a tool is permitted to be, which decides what it is given."""

    #: Reads already-computed numbers. Gets `ReadOnlyRepositories`. Computes nothing
    #: an LLM could influence -- the arithmetic is deterministic Python.
    READ = "READ"
    #: Wants to change something, so it returns an `AgentProposal` a human confirms.
    PROPOSE = "PROPOSE"
    #: Navigates or downloads. Output is data or a staged basket, never a submission.
    BROWSER = "BROWSER"


@dataclass(frozen=True, slots=True)
class ToolResult:
    """What a handler returns: text for the model, and the audit fields."""

    #: What the model sees. Always a string: a tool returning an object would let a
    #: number reach the model unlabelled.
    text: str
    #: Every figure in `text` that a narration is allowed to quote. The runner checks
    #: the model's prose against this, so an invented number is caught rather than
    #: trusted. This is how "reads computed numbers, never computes them" is checked
    #: rather than merely asked for.
    figures: tuple[str, ...] = ()
    proposal: AgentProposal | None = None
    #: Set when the result is a staged thing awaiting a person.
    awaiting_human: str | None = None
    extra: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class ToolContext:
    """Everything a handler receives. Note what is absent: any way to write."""

    run_id: str
    reads: ReadOnlyRepositories


Handler = Callable[[ToolContext, dict[str, Any]], ToolResult]


@dataclass(frozen=True, slots=True)
class ToolSpec:
    name: str
    kind: ToolKind
    description: str
    input_schema: dict[str, Any]
    handler: Handler
    #: True when the action would, or could, spend money. Spec 9's last hard rule.
    #: Such a tool may prepare and describe; the outcome is AWAITING_HUMAN and the
    #: runner never treats it as done.
    stops_at_human: bool = False


_REGISTRY: dict[str, ToolSpec] = {}


def register(spec: ToolSpec) -> ToolSpec:
    """Add a tool to the allowlist. The only way a handler becomes reachable."""
    if spec.name in _REGISTRY:
        raise ValueError(f"tool {spec.name!r} is already registered")
    for table in FORBIDDEN_TABLES:
        if spec.name == table or spec.name.endswith(f"_{table}"):
            raise ValueError(
                f"tool {spec.name!r} is named after {table!r}, which is on "
                "FORBIDDEN_TABLES. Nothing may be registered that claims to write it."
            )
    _REGISTRY[spec.name] = spec
    return spec


def allowed_names() -> list[str]:
    return sorted(_REGISTRY)


def specs() -> list[ToolSpec]:
    return [_REGISTRY[name] for name in allowed_names()]


def spec_for(name: str) -> ToolSpec:
    """Resolve a name, or refuse. There is no fallback path."""
    try:
        return _REGISTRY[name]
    except KeyError:
        raise ToolRefused(
            name,
            "not on the allowlist. The agent's tools are an explicit whitelist "
            f"({', '.join(allowed_names()) or 'none registered'}); anything else is "
            "refused rather than attempted.",
        ) from None


def guard_output(spec: ToolSpec, result: ToolResult) -> ToolResult:
    """Check a handler honoured its own kind. Cheap, and catches a whole class of bug."""
    if spec.kind is ToolKind.PROPOSE and result.proposal is None:
        raise ProposalRequired(
            f"{spec.name} is a PROPOSE tool and must return an AgentProposal a human "
            "confirms. It returned none, which would mean the change had already "
            "happened."
        )
    if spec.kind is not ToolKind.PROPOSE and result.proposal is not None:
        raise ProposalRequired(
            f"{spec.name} is a {spec.kind.value} tool and returned an AgentProposal. "
            "Proposals come from PROPOSE tools so the review path is one path."
        )
    if spec.stops_at_human and result.awaiting_human is None:
        raise HumanApprovalRequired(
            f"{spec.name} is declared as spending money and must say what a person "
            "has to do. It said nothing, so the action is refused."
        )
    if not spec.stops_at_human and result.awaiting_human is not None:
        raise HumanApprovalRequired(
            f"{spec.name} produced something awaiting a human but is not declared "
            "`stops_at_human`. Declare it, so the outcome is logged as AWAITING_HUMAN."
        )
    return result


def anthropic_tool_definitions(names: Sequence[str] | None = None) -> list[dict[str, Any]]:
    """The allowlist, rendered for the Messages API `tools` parameter.

    Built FROM the registry rather than written alongside it, so the list the model
    is shown and the list `dispatch` will honour cannot drift apart.
    """
    chosen = list(names) if names is not None else allowed_names()
    return [
        {
            "name": spec.name,
            "description": spec.description,
            "input_schema": spec.input_schema,
        }
        for spec in (spec_for(name) for name in chosen)
    ]
