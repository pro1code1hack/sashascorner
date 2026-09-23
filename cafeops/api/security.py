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

Read through a private `BaseSettings` rather than `config.settings` on purpose: this
is the only new setting the API needs, `config.py` is shared with five other agents
mid-run, and `os.environ` alone would miss a password written in `.env`. See the
integrator note in the final summary -- this belongs in `config.Settings` as
`api_password` once the merge is done.
"""

from __future__ import annotations

import secrets
from typing import Annotated

from fastapi import Depends, Header, HTTPException, status
from pydantic_settings import BaseSettings, SettingsConfigDict

__all__ = ["ApiAuth", "api_password", "require_password"]

_UNSET_MESSAGE = (
    "CAFEOPS_API_PASSWORD is not set, so this API is refusing to serve rather than "
    "serving the whole business unauthenticated. Set it in .env or the environment and "
    "restart. Spec 1: a single shared password, no user management."
)


class _ApiSettings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env", env_file_encoding="utf-8", extra="ignore", env_prefix="CAFEOPS_"
    )

    api_password: str | None = None


def api_password() -> str | None:
    """Read fresh each call so a restart is not needed to notice a changed `.env`.

    The cost is a file stat per request, which against 40 transactions a day is
    nothing, and the benefit is that "it is not picking up my password" is never the
    answer to why a screen is blank.
    """
    return _ApiSettings().api_password or None


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
