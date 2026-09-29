"""Who is making a change. Every back-office write carries a person's name."""

from __future__ import annotations

__all__ = ["require_actor"]

#: `*_by` columns are String(120); a longer name is a paste error, not a person.
_MAX_ACTOR_LEN = 120


def require_actor(actor: str | None, *, error: type[Exception] = ValueError) -> str:
    """The trimmed operator name, refusing a blank one: a write nobody signed is not a
    record of a decision, it is an accident (invariant 1's spirit, applied to edits).

    `error` lets a service raise its own refusal type (the API maps those to a status);
    the sentence is the same whichever type carries it. This is THE actor rule -- a
    service that trims or truncates a name on its own is a copy waiting to drift.
    """
    name = (actor or "").strip()
    if not name:
        raise error("say who is making this change (the operator name)")
    return name[:_MAX_ACTOR_LEN]
