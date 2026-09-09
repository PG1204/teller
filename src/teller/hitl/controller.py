"""Handoff controller — waits for a human on the same live session and hands control back.

P2 ships the seam: ``discovery_escalator`` returns ``None`` (no operator available), so a stuck
discovery run ends as ``needs_human`` with a complete ``InterventionRequest`` on disk. P4 fills
in the wait loop over ``commands.jsonl`` and the operator server; the interface does not change.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from teller.hitl.state import InterventionRequest


def discovery_escalator(*, headed: bool) -> Callable[[Any, InterventionRequest], str | None] | None:
    if not headed:
        return None
    from teller.hitl.handoff import HandoffController  # P4

    return HandoffController.for_discovery


def replay_escalator(*, mode: str = "auto", headed: bool = False) -> Callable[[Any, InterventionRequest], str | None] | None:
    """How a paused replay waits for an operator. ``none`` -> never wait (failures are terminal)."""
    if mode == "none":
        return None
    from teller.hitl.handoff import HandoffController  # P4

    return HandoffController.for_replay(mode=mode, headed=headed)
