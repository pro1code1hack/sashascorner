"""One shared password. Spec 1: "no user management beyond a single shared password".

Two people use this app on a laptop a few times a week, behind Caddy on one box
(spec 3). Sessions, roles, refresh tokens and a user table would all be machinery
guarding a door that two people share the key to, and every one of them is a thing
that can break or leak.

The password arrives as `CAFEOPS_API_PASSWORD`, in either header form:

    X-API-Key: <password>
    Authorization: Bearer <password>

Both are accepted because the two clients differ: `curl` and the fixture dumper reach
for the header, and a browser `fetch` reaches for `Authorization`. They carry the same
secret and neither is more privileged.

**It fails closed.** With no password configured every endpoint but `/api/health`
answers 503 and says what to set. The alternative -- serving the whole business open
because a variable was missed -- is worse than not serving at all, and a warning in a
log is not a mitigation.

Comparison is `secrets.compare_digest`, so a wrong password takes the same time to
reject however much of it is right.

The password is declared once, on the shared `config.Settings`, and a fresh
`Settings()` is constructed per call so a changed `.env` is noticed without a restart.
Two declarations of one secret is how a password set in `.env` ends up honoured by half
the process and ignored by the other half.
"""

from __future__ import annotations

import secrets
from typing import Annotated

from fastapi import Depends, Header, HTTPException, status

__all__ = [
    "ApiAuth",
    "OptionalApiAuth",
    "api_password",
    "presented_password_is_valid",
    "require_password",
]

_UNSET_MESSAGE = (
    "CAFEOPS_API_PASSWORD is not set, so this API is refusing to serve rather than "
    "serving the whole business unauthenticated. Set it in .env or the environment and "
    "restart. Spec 1: a single shared password, no user management."
)


def api_password() -> str | None:
    """Read fresh each call so a restart is not needed to notice a changed `.env`.

    The cost is a file stat per request, which against 40 transactions a day is
    nothing, and the benefit is that "it is not picking up my password" is never the
    answer to why a screen is blank.
    """
    from cafeops.config import Settings

    return Settings().api_password or None


def require_password(
    x_api_key: Annotated[str | None, Header(alias="X-API-Key")] = None,
    authorization: Annotated[str | None, Header()] = None,
) -> None:
    """Reject anything that does not present the shared password."""
    configured = api_password()
    if configured is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=_UNSET_MESSAGE,
        )

    presented: list[str] = []
    if x_api_key:
        presented.append(x_api_key)
    if authorization and authorization.lower().startswith("bearer "):
        presented.append(authorization[7:].strip())

    if not presented:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=(
                "no credential presented. Send the shared password as either "
                "'X-API-Key: <password>' or 'Authorization: Bearer <password>'."
            ),
            headers={"WWW-Authenticate": "Bearer"},
        )
    # compare_digest against every presented form, and only then decide. Returning on
    # the first match would leak which header was the right one through timing.
    if not any(secrets.compare_digest(candidate, configured) for candidate in presented):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="wrong password.",
            headers={"WWW-Authenticate": "Bearer"},
        )


#: Attach to a router to protect every route on it.
ApiAuth = Depends(require_password)


def presented_password_is_valid(
    x_api_key: Annotated[str | None, Header(alias="X-API-Key")] = None,
    authorization: Annotated[str | None, Header()] = None,
) -> bool:
    """Did this caller present the right password? Never rejects.

    For the handful of endpoints that must answer WITHOUT a key but should say less
    to a stranger -- `/api/health` is the case: it has to work before anyone has the
    password, or "is it up?" and "is my password right?" collapse into one question,
    but a liveness probe does not need the size of the inventory.

    Same constant-time comparison as `require_password`, and the same "compare every
    presented form, then decide" shape, so which header was right is not leaked
    through timing.
    """
    configured = api_password()
    if configured is None:
        return False
    presented: list[str] = []
    if x_api_key:
        presented.append(x_api_key)
    if authorization and authorization.lower().startswith("bearer "):
        presented.append(authorization[7:].strip())
    if not presented:
        return False
    return any(secrets.compare_digest(candidate, configured) for candidate in presented)


#: For an open route that discloses more to a caller who belongs here.
OptionalApiAuth = Depends(presented_password_is_valid)
