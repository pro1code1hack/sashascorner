"""One logging configuration for every long-running entry point.

The API (uvicorn), the bot, the scheduler and the browser worker are separate processes
(CLAUDE.md §3). Each used to call `logging.basicConfig` with its own format, so the same
event looked different in each journal. One function, one format, one place to change
the level -- `CAFEOPS_LOG_LEVEL` -- and libraries that chatter at INFO are turned down
here rather than at each call site.
"""

from __future__ import annotations

import logging
import os

__all__ = ["configure_logging"]

_FORMAT = "%(asctime)s %(levelname)-5s %(name)s: %(message)s"

#: Third-party loggers that log every request at INFO. WARNING is where they are useful.
_QUIET = ("httpx", "httpcore", "apscheduler.executors.default", "aiogram.event")


def configure_logging(level: int | str | None = None) -> None:
    """Idempotent: safe to call from every entry point, including twice in one process."""
    chosen = level if level is not None else os.environ.get("CAFEOPS_LOG_LEVEL", "INFO")
    logging.basicConfig(level=chosen, format=_FORMAT, force=True)
    for name in _QUIET:
        logging.getLogger(name).setLevel(logging.WARNING)
