"""Control-transfer model: who is in control of the live session, and how it changes.

    RUNNING ──stuck──▶ STUCK_EVALUATING ──request written──▶ AWAITING_HUMAN ──claim──▶ HUMAN_IN_CONTROL
       ▲                                                          │ deadline              │ resume
       │                                                          ▼                       ▼
       └────────────── verification passes ◀──────────── HANDBACK_VERIFYING ◀─────────────┘
                                                                  │ fails (≤ max_handoffs) → STUCK_EVALUATING
    any state ──abort/decline/finish──▶ FINISHED

The ``controller`` (automation | human | none) is derived from the state. ``ControlToken`` is
what the surface demands before acting: ``token.require_automation()`` raises
``ControlViolation`` unless the state machine says automation holds control. This is enforced
in code (``Surface.act``), not by convention, and a unit test proves the driver refuses to act
while a human is in control.

This module must not import Playwright (a guard test asserts it).
"""

from __future__ import annotations

import datetime as dt
import json
import secrets
from collections.abc import Callable
from enum import StrEnum
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class Controller(StrEnum):
    AUTOMATION = "automation"
    HUMAN = "human"
    NONE = "none"


class RunState(StrEnum):
    RUNNING = "RUNNING"
    STUCK_EVALUATING = "STUCK_EVALUATING"
    AWAITING_HUMAN = "AWAITING_HUMAN"
    HUMAN_IN_CONTROL = "HUMAN_IN_CONTROL"
    HANDBACK_VERIFYING = "HANDBACK_VERIFYING"
    FINISHED = "FINISHED"


CONTROLLER_FOR: dict[RunState, Controller] = {
    RunState.RUNNING: Controller.AUTOMATION,
    RunState.STUCK_EVALUATING: Controller.AUTOMATION,
    RunState.AWAITING_HUMAN: Controller.NONE,
    RunState.HUMAN_IN_CONTROL: Controller.HUMAN,
    RunState.HANDBACK_VERIFYING: Controller.AUTOMATION,
    RunState.FINISHED: Controller.NONE,
}

TRANSITIONS: dict[RunState, set[RunState]] = {
    RunState.RUNNING: {RunState.STUCK_EVALUATING, RunState.FINISHED},
    RunState.STUCK_EVALUATING: {RunState.AWAITING_HUMAN, RunState.RUNNING, RunState.FINISHED},
    RunState.AWAITING_HUMAN: {RunState.HUMAN_IN_CONTROL, RunState.FINISHED},
    RunState.HUMAN_IN_CONTROL: {RunState.HANDBACK_VERIFYING, RunState.FINISHED},
    RunState.HANDBACK_VERIFYING: {RunState.RUNNING, RunState.STUCK_EVALUATING, RunState.FINISHED},
    RunState.FINISHED: set(),
}


class ControlViolation(RuntimeError):
    """Raised when automation tries to act while it does not hold control."""


class IllegalTransition(RuntimeError):
    pass


def _now() -> str:
    return dt.datetime.now(dt.UTC).replace(microsecond=0).isoformat()


class InterventionRequest(BaseModel):
    """Everything a human needs to act on an escalation. Written to runs/<id>/intervention.json."""

    model_config = ConfigDict(extra="forbid")

    intervention_id: str
    run_id: str
    mode: str
    capability_id: str | None = None
    goal: str | None = None
    step_id: str | None = None
    intent: str | None = None
    trigger: str
    reason: str
    url: str | None = None
    screenshot: str | None = None
    last_events: list[dict[str, Any]] = Field(default_factory=list)
    allowed_resolutions: list[str] = Field(
        default_factory=lambda: ["retry_step", "skip_step", "complete", "abort"]
    )
    created_at: str = Field(default_factory=_now)
    deadline: str | None = None
    session_endpoint: str | None = None
    # filled in as the handoff progresses
    claimed_by: str | None = None
    claimed_at: str | None = None
    released_at: str | None = None
    resolution: str | None = None
    note: str | None = None

    @staticmethod
    def new_id() -> str:
        return "int_" + secrets.token_hex(4)


class Transition(BaseModel):
    model_config = ConfigDict(extra="forbid")

    ts: str
    from_state: RunState
    to_state: RunState
    controller: Controller
    actor: str
    reason: str = ""


class ControlState:
    """The state machine. Persists every transition to ``<run_dir>/state.json`` and notifies
    an optional listener (the event log)."""

    def __init__(
        self,
        run_dir: Path | None = None,
        on_transition: Callable[[Transition], None] | None = None,
        max_handoffs: int = 2,
    ):
        self.state: RunState = RunState.RUNNING
        self.history: list[Transition] = []
        self.run_dir = Path(run_dir) if run_dir else None
        self._listener = on_transition
        self.max_handoffs = max_handoffs
        self.handoffs = 0
        self.pending: InterventionRequest | None = None
        self._persist()

    # ---- queries -------------------------------------------------------------------------

    @property
    def controller(self) -> Controller:
        return CONTROLLER_FOR[self.state]

    def token(self) -> ControlToken:
        return ControlToken(self)

    # ---- transitions ----------------------------------------------------------------------

    def transition(self, to: RunState, *, actor: str, reason: str = "") -> Transition:
        if to not in TRANSITIONS[self.state]:
            raise IllegalTransition(f"{self.state} -> {to} is not allowed")
        if to is RunState.HUMAN_IN_CONTROL:
            if self.handoffs + 1 > self.max_handoffs:
                raise IllegalTransition(f"max_handoffs ({self.max_handoffs}) exceeded")
            self.handoffs += 1
        t = Transition(
            ts=_now(),
            from_state=self.state,
            to_state=to,
            controller=CONTROLLER_FOR[to],
            actor=actor,
            reason=reason,
        )
        self.state = to
        self.history.append(t)
        self._persist()
        if self._listener:
            self._listener(t)
        return t

    def raise_intervention(self, req: InterventionRequest, *, actor: str = "automation") -> None:
        if self.state is RunState.RUNNING:
            self.transition(RunState.STUCK_EVALUATING, actor=actor, reason=req.trigger)
        self.pending = req
        self.transition(RunState.AWAITING_HUMAN, actor=actor, reason=req.reason)
        self._persist()

    def claim(self, intervention_id: str, operator: str) -> InterventionRequest:
        self._check_pending(intervention_id)
        assert self.pending is not None
        self.pending.claimed_by = operator
        self.pending.claimed_at = _now()
        self.transition(RunState.HUMAN_IN_CONTROL, actor=f"operator:{operator}", reason="claim")
        return self.pending

    def release(self, intervention_id: str, resolution: str, note: str | None = None) -> None:
        self._check_pending(intervention_id)
        assert self.pending is not None
        self.pending.released_at = _now()
        self.pending.resolution = resolution
        self.pending.note = note
        actor = f"operator:{self.pending.claimed_by or 'unknown'}"
        self.transition(RunState.HANDBACK_VERIFYING, actor=actor, reason=resolution)

    def handback_ok(self, reason: str = "verification passed") -> None:
        self.pending = None
        self.transition(RunState.RUNNING, actor="automation", reason=reason)

    def handback_failed(self, reason: str) -> None:
        self.transition(RunState.STUCK_EVALUATING, actor="automation", reason=reason)

    def finish(self, actor: str = "automation", reason: str = "") -> None:
        if self.state is not RunState.FINISHED:
            self.transition(RunState.FINISHED, actor=actor, reason=reason)

    def _check_pending(self, intervention_id: str) -> None:
        if self.pending is None or self.pending.intervention_id != intervention_id:
            raise IllegalTransition(
                f"no pending intervention {intervention_id!r} (stale or already resolved)"
            )

    # ---- persistence ----------------------------------------------------------------------

    def snapshot(self) -> dict[str, Any]:
        return {
            "state": self.state.value,
            "controller": self.controller.value,
            "handoffs": self.handoffs,
            "max_handoffs": self.max_handoffs,
            "pending": self.pending.model_dump(exclude_none=True) if self.pending else None,
            "history": [t.model_dump() for t in self.history],
        }

    def _persist(self) -> None:
        if not self.run_dir:
            return
        self.run_dir.mkdir(parents=True, exist_ok=True)
        (self.run_dir / "state.json").write_text(
            json.dumps(self.snapshot(), indent=2), encoding="utf-8"
        )
        if self.pending:
            (self.run_dir / "intervention.json").write_text(
                self.pending.model_dump_json(indent=2, exclude_none=True), encoding="utf-8"
            )


class ControlToken:
    """Handed to the surface. Checks the *live* state on every call — it cannot be cached."""

    def __init__(self, state: ControlState):
        self._state = state

    @property
    def controller(self) -> Controller:
        return self._state.controller

    def require_automation(self) -> None:
        if self._state.controller is not Controller.AUTOMATION:
            raise ControlViolation(
                f"automation attempted to act while controller={self._state.controller} "
                f"(state={self._state.state})"
            )


__all__ = [
    "Controller",
    "RunState",
    "CONTROLLER_FOR",
    "TRANSITIONS",
    "ControlViolation",
    "IllegalTransition",
    "InterventionRequest",
    "Transition",
    "ControlState",
    "ControlToken",
]
