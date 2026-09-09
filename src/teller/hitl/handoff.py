"""HandoffController — pause automation, cede the live session to a human, take it back.

Used by both replay and discovery through one callable: ``controller(runner, request) -> mode``.

    AWAITING_HUMAN     the run's intervention.json is on disk; the operator console (web) and the
                       CLI twin (``teller intervene``) both append to commands.jsonl
    claim              -> HUMAN_IN_CONTROL: recorder.js is injected into every frame so the
                       human's clicks/typing/navigation are captured (redacted) to
                       human_actions.jsonl; the automation thread keeps polling *inside*
                       page.wait_for_timeout so Playwright events keep flowing
    resume/confirm/    -> HANDBACK_VERIFYING: recorder detached, before/after screenshots kept,
    decline/abort         the Handoff record appended; the caller re-observes and verifies

While waiting, the automation never acts: ``Surface.act`` checks the ControlToken on every call
and the state machine says controller != automation.
"""

from __future__ import annotations

import json
import logging
import time
from pathlib import Path
from typing import Any

from teller.hitl.commands import read_commands
from teller.hitl.operator_server import OperatorServer
from teller.hitl.state import InterventionRequest
from teller.replay.result import Handoff

log = logging.getLogger("teller.hitl")


class HandoffController:
    def __init__(self, *, mode: str = "auto", headed: bool = False, operator_port: int = 8787, default_timeout_s: int = 900):
        self.mode = ("web" if headed else "cli") if mode == "auto" else mode
        self.headed = headed
        self.operator_port = operator_port
        self.default_timeout_s = default_timeout_s
        self._server: OperatorServer | None = None

    # ---- factories ------------------------------------------------------------------------

    @classmethod
    def for_replay(cls, *, mode: str = "auto", headed: bool = False) -> HandoffController:
        return cls(mode=mode, headed=headed)

    @classmethod
    def for_discovery(cls, runner: Any, req: InterventionRequest) -> str | None:
        return cls(mode="web", headed=True)(runner, req)

    # ---- the handoff ----------------------------------------------------------------------

    def __call__(self, runner: Any, req: InterventionRequest) -> str | None:
        run_dir: Path = runner.run_dir
        state = runner.state
        surface = runner.surface
        elog = runner.log
        page = getattr(surface, "page", None)
        timeout_s = self.default_timeout_s
        cap = getattr(runner, "cap", None)
        if cap is not None:
            timeout_s = cap.escalation_policy.handoff_timeout_s
        deadline = time.monotonic() + timeout_s
        req.deadline = time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime(time.time() + timeout_s))
        state.pending = req
        state._persist()

        console_url = None
        if self.mode == "web":
            self._server = OperatorServer(run_dir, port=self.operator_port)
            console_url = self._server.start()
        elog.emit("handoff.requested", {
            "intervention_id": req.intervention_id, "trigger": req.trigger, "reason": req.reason,
            "step_id": req.step_id, "console": console_url, "session_endpoint": req.session_endpoint,
            "cli": f"teller intervene claim {run_dir.name} --operator <you>; then: teller intervene resume {run_dir.name} --mode retry_step",
            "deadline_s": timeout_s,
        }, step_id=req.step_id)
        print(f"\n=== HUMAN INTERVENTION REQUESTED ({req.trigger}) ===\n"
              f"reason:  {req.reason}\nrun:     {run_dir}\n"
              f"console: {console_url or '(cli mode)'}\n"
              f"cli:     teller intervene claim {run_dir.name} --operator <you>\n"
              f"         teller intervene resume {run_dir.name} --mode retry_step|skip_step|complete\n", flush=True)

        actions_path = run_dir / "human_actions.jsonl"
        action_count = 0
        handoff = Handoff(intervention_id=req.intervention_id, trigger=req.trigger, reason=req.reason, step_id=req.step_id)

        def sink(payload: dict[str, Any]) -> None:
            nonlocal action_count
            action_count += 1
            with actions_path.open("a", encoding="utf-8") as fh:
                fh.write(json.dumps(payload) + "\n")
            elog.emit("human_action", payload, step_id=req.step_id, actor=f"operator:{handoff.claimed_by or 'unknown'}")

        offset = 0
        last_shot = 0.0
        result_mode: str | None = None
        try:
            while True:
                self._pump(page, 250)
                now = time.monotonic()
                if now - last_shot > 2.0:
                    try:
                        surface.screenshot(str(run_dir / "screenshots" / "handoff_live.jpg"))
                    except Exception:  # noqa: BLE001
                        pass
                    last_shot = now
                cmds, offset = read_commands(run_dir, offset)
                for cmd in cmds:
                    if cmd.get("intervention_id") != req.intervention_id:
                        elog.emit("warning", {"code": "STALE_COMMAND", "command": cmd})
                        continue
                    c = cmd.get("command")
                    if c == "claim" and handoff.claimed_by is None:
                        state.claim(req.intervention_id, cmd.get("operator", "operator"))
                        handoff.claimed_by = cmd.get("operator", "operator")
                        handoff.claimed_at = cmd.get("ts")
                        try:
                            surface.screenshot(str(run_dir / "screenshots" / "handoff_before.jpg"))
                            surface.inject_recorder(sink)
                        except Exception as e:  # noqa: BLE001
                            elog.emit("warning", {"code": "RECORDER_FAILED", "error": str(e)})
                        elog.emit("handoff.claimed", {"operator": handoff.claimed_by, "controller": state.controller.value}, actor=f"operator:{handoff.claimed_by}")
                    elif c in ("dialog_accept", "dialog_dismiss") and handoff.claimed_by:
                        info = surface.handle_dialog(c == "dialog_accept")
                        elog.emit("human_action", {"kind": "dialog", "accept": c == "dialog_accept", "message": info.message if info else None}, actor=f"operator:{handoff.claimed_by}")
                    elif c in ("resume", "confirm", "decline", "abort"):
                        if handoff.claimed_by is None and c != "abort":
                            elog.emit("warning", {"code": "RESUME_WITHOUT_CLAIM", "command": c})
                            continue
                        if handoff.claimed_by is None:  # abort without claim
                            state.claim(req.intervention_id, cmd.get("operator", "operator"))
                            handoff.claimed_by = cmd.get("operator", "operator")
                        mode = cmd.get("mode") if c == "resume" else c
                        state.release(req.intervention_id, mode or c, cmd.get("note"))
                        handoff.released_at = cmd.get("ts")
                        handoff.resume_mode = mode
                        handoff.note = cmd.get("note")
                        result_mode = mode
                        break
                if result_mode is not None:
                    break
                if time.monotonic() > deadline:
                    elog.emit("escalation", {"trigger": "ESCALATION_TIMEOUT", "waited_s": timeout_s})
                    break
        finally:
            try:
                surface.detach_recorder()
                surface.screenshot(str(run_dir / "screenshots" / "handoff_after.jpg"))
            except Exception:  # noqa: BLE001
                pass
            if self._server is not None:
                self._server.stop()
                self._server = None

        handoff.action_count = action_count
        handoff.human_actions_path = str(actions_path.relative_to(run_dir)) if actions_path.exists() else None
        req.claimed_by = handoff.claimed_by
        req.claimed_at = handoff.claimed_at
        req.released_at = handoff.released_at
        req.resolution = result_mode
        req.note = handoff.note
        (run_dir / "intervention.json").write_text(req.model_dump_json(indent=2, exclude_none=True), encoding="utf-8")
        if hasattr(runner, "handoffs"):
            runner.handoffs.append(handoff)
        elog.emit("handoff.released", {"resolution": result_mode, "human_actions": action_count, "note": handoff.note}, actor=f"operator:{handoff.claimed_by or 'none'}")

        if result_mode is None:
            state.finish(reason="ESCALATION_TIMEOUT")
            return None
        if result_mode in ("retry_step", "skip_step", "complete", "confirm"):
            # HANDBACK_VERIFYING -> RUNNING: the caller re-observes and verifies the postcondition
            state.handback_ok(reason=result_mode)
        return result_mode

    @staticmethod
    def _pump(page: Any, ms: int) -> None:
        """Wait while keeping Playwright's event loop alive (binding callbacks, dialogs, navigations)."""
        if page is not None:
            try:
                page.wait_for_timeout(ms)
                return
            except Exception:  # noqa: BLE001 - page gone; fall back to sleeping
                pass
        time.sleep(ms / 1000)
