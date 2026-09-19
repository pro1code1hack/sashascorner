"""The bounded agent. Spec 9.

Three jobs, each behind a whitelisted tool: browser automation where no API exists,
narration of numbers computed elsewhere, and proposals a human confirms.

`policies.py` is the boundary and is worth reading first -- it is built so a write is
unreachable rather than forbidden. `tools.py` is the whole allowlist. `runner.py` is
the single dispatch point and the only writer of `agent_action_log`.
"""

from __future__ import annotations
