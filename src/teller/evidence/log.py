"""Structured JSONL event log — the source of truth for a run.

Every event carries the run id, mode, the current *controller* (who is allowed to act), the
step id if any, a type from a closed vocabulary and a payload. All payloads pass through the
``Redactor`` before they are written: the log is the only path to disk for run telemetry, so
there is no way to leak a value that the redactor did not see.

Console output mirrors the log at INFO so a demo reads live.
"""

from __future__ import annotations

import datetime as dt
import json
import logging
import threading
from collections.abc import Callable
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

EventType = Literal[
    "run.start",
    "run.end",
    "login",
    "observe",
    "decide",
    "policy_check",
    "act",
    "wait",
    "condition_detected",
    "recovery",
    "checkpoint",
    "extract",
    "escalation",
    "handoff.requested",
    "handoff.claimed",
    "handoff.command",
    "handoff.released",
    "handoff.verified",
    "human_action",
    "warning",
    "result",
    "error",
]

Controller = Literal["automation", "human", "none"]


class Event(BaseModel):
    model_config = ConfigDict(extra="forbid")

    ts: str
    run_id: str
    mode: Literal["replay", "discovery"]
    controller: Controller
    type: EventType
    step_id: str | None = None
    actor: str = "automation"
    payload: dict[str, Any] = Field(default_factory=dict)


def now_iso() -> str:
    return dt.datetime.now(dt.UTC).replace(microsecond=0).isoformat()


def new_run_id(prefix: str = "run") -> str:
    stamp = dt.datetime.now(dt.UTC).strftime("%Y-%m-%dT%H-%M-%S")
    import secrets

    return f"{prefix}_{stamp}_{secrets.token_hex(2)}"


class EventLog:
    """Append-only JSONL writer with an injectable redaction hook."""

    def __init__(
        self,
        run_dir: Path,
        run_id: str,
        mode: Literal["replay", "discovery"],
        redact: Callable[[Any], Any] | None = None,
        echo: bool = True,
    ):
        self.run_dir = Path(run_dir)
        self.run_dir.mkdir(parents=True, exist_ok=True)
        self.path = self.run_dir / "events.jsonl"
        self.run_id = run_id
        self.mode = mode
        self.controller: Controller = "automation"
        self._redact = redact or (lambda x: x)
        self._lock = threading.Lock()
        self._log = logging.getLogger("teller.run")
        self._echo = echo
        self.events: list[Event] = []

    def set_controller(self, controller: Controller) -> None:
        self.controller = controller

    def emit(
        self,
        type_: EventType,
        payload: dict[str, Any] | None = None,
        *,
        step_id: str | None = None,
        actor: str = "automation",
    ) -> Event:
        ev = Event(
            ts=now_iso(),
            run_id=self.run_id,
            mode=self.mode,
            controller=self.controller,
            type=type_,
            step_id=step_id,
            actor=actor,
            payload=self._redact(payload or {}),
        )
        line = ev.model_dump_json(exclude_none=True)
        with self._lock:
            with self.path.open("a", encoding="utf-8") as fh:
                fh.write(line + "\n")
            self.events.append(ev)
        if self._echo:
            short = json.dumps(ev.payload, default=str)
            if len(short) > 300:
                short = short[:297] + "..."
            self._log.info("%s %s %s %s", ev.type, f"[{ev.controller}]", ev.step_id or "-", short)
        return ev

    def tail(self, n: int = 5) -> list[dict[str, Any]]:
        return [e.model_dump(exclude_none=True) for e in self.events[-n:]]


def configure_console_logging(level: int = logging.INFO) -> None:
    root = logging.getLogger("teller")
    if root.handlers:
        return
    handler = logging.StreamHandler()
    handler.setFormatter(logging.Formatter("%(asctime)s %(message)s", datefmt="%H:%M:%S"))
    root.addHandler(handler)
    root.setLevel(level)
