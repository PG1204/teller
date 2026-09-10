"""Escalator factories: how a stuck run waits for a human.

A headless discovery run has no operator, so it ends as ``needs_human`` with a complete
``InterventionRequest`` on disk. Otherwise the ``HandoffController`` pauses on the same live session
and hands control back (see ``hitl/handoff.py``).
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from teller.hitl.state import InterventionRequest


def discovery_escalator(*, headed: bool) -> Callable[[Any, InterventionRequest], str | None] | None:
    if not headed:
        return None
    from teller.hitl.handoff import HandoffController

    return HandoffController.for_discovery


def replay_escalator(*, mode: str = "auto", headed: bool = False) -> Callable[[Any, InterventionRequest], str | None] | None:
    """How a paused replay waits for an operator. ``none`` -> never wait (failures are terminal)."""
    if mode == "none":
        return None
    from teller.hitl.handoff import HandoffController

    return HandoffController.for_replay(mode=mode, headed=headed)
