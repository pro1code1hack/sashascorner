"""Errors the API raises itself, as opposed to ones it translates.

There is one, and it exists because of a real silent failure this API found in its own
fixture dump.

`CompositionRepository.live_component` returns a component row **open or closed**, on
purpose: the editor works on a component by id and `close_and_open_component` is what
refuses to touch a superseded row. That is right for the write path. It is not enough for
the *preview* path, because a preview of a closed component computes honestly and answers
"0 items affected, no warnings" -- the recipe it is being compared against no longer
contains that component id, so nothing changes. A reviewer reads that as "this edit is
harmless" and approves it.

So the API checks liveness before previewing, and says so. A superseded id means the
editor is looking at a page built before somebody else's edit landed, and the answer is
to reload -- not to show a diff of nothing.
"""

from __future__ import annotations

__all__ = ["ComponentSupersededError"]


class ComponentSupersededError(ValueError):
    """The component id is real but no longer the live row for its slot.

    409 rather than 404: the row exists and its history is intact. What is stale is the
    client's idea of which row is current.
    """
