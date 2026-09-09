"""Unit tests for the handoff state machine (``teller.hitl.state``)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from teller.hitl.state import (
    Controller,
    ControlState,
    ControlToken,
    ControlViolation,
    IllegalTransition,
    InterventionRequest,
    RunState,
    Transition,
)


def _request(intervention_id: str = "int_0001") -> InterventionRequest:
    return InterventionRequest(
        intervention_id=intervention_id,
        run_id="run_test",
        mode="replay",
        trigger="resolution_failed",
        reason="could not resolve the Go button",
    )


def _cycle(cs: ControlState, intervention_id: str, operator: str = "alex") -> None:
    """One full handoff: raise -> claim -> release -> handback_ok."""
    cs.raise_intervention(_request(intervention_id))
    cs.claim(intervention_id, operator)
    cs.release(intervention_id, "retry_step")
    cs.handback_ok()


# ---- legal path ---------------------------------------------------------------------------


def test_initial_state_is_running_with_automation_in_control() -> None:
    cs = ControlState()
    assert cs.state is RunState.RUNNING
    assert cs.controller is Controller.AUTOMATION
    assert cs.history == []
    assert cs.handoffs == 0


def test_legal_path_walks_every_state_with_expected_controller() -> None:
    cs = ControlState()
    path = [
        (RunState.STUCK_EVALUATING, Controller.AUTOMATION),
        (RunState.AWAITING_HUMAN, Controller.NONE),
        (RunState.HUMAN_IN_CONTROL, Controller.HUMAN),
        (RunState.HANDBACK_VERIFYING, Controller.AUTOMATION),
        (RunState.RUNNING, Controller.AUTOMATION),
    ]
    assert cs.controller is Controller.AUTOMATION
    for to, controller in path:
        t = cs.transition(to, actor="test")
        assert cs.state is to
        assert cs.controller is controller
        assert t.controller is controller
    assert [t.to_state for t in cs.history] == [to for to, _ in path]


def test_high_level_api_drives_the_same_path() -> None:
    cs = ControlState()
    req = _request()
    cs.raise_intervention(req)
    assert cs.state is RunState.AWAITING_HUMAN
    assert cs.pending is req
    claimed = cs.claim("int_0001", "alex")
    assert cs.state is RunState.HUMAN_IN_CONTROL
    assert claimed.claimed_by == "alex"
    assert claimed.claimed_at is not None
    cs.release("int_0001", "retry_step", note="fixed the search box")
    assert cs.state is RunState.HANDBACK_VERIFYING
    assert req.resolution == "retry_step"
    assert req.note == "fixed the search box"
    assert req.released_at is not None
    cs.handback_ok()
    assert cs.state is RunState.RUNNING
    assert cs.pending is None
    assert cs.handoffs == 1


def test_handback_failed_returns_to_stuck_evaluating() -> None:
    cs = ControlState()
    cs.raise_intervention(_request())
    cs.claim("int_0001", "alex")
    cs.release("int_0001", "skip_step")
    cs.handback_failed("checkpoint still failing")
    assert cs.state is RunState.STUCK_EVALUATING
    assert cs.history[-1].reason == "checkpoint still failing"
    assert cs.history[-1].actor == "automation"


# ---- control token ------------------------------------------------------------------------


def test_token_requires_automation_along_the_handoff() -> None:
    cs = ControlState()
    token = cs.token()
    assert isinstance(token, ControlToken)
    token.require_automation()  # RUNNING
    cs.raise_intervention(_request())
    assert token.controller is Controller.NONE
    with pytest.raises(ControlViolation, match="AWAITING_HUMAN"):
        token.require_automation()
    cs.claim("int_0001", "alex")
    assert token.controller is Controller.HUMAN
    with pytest.raises(ControlViolation, match="controller=human"):
        token.require_automation()
    cs.release("int_0001", "complete")
    token.require_automation()  # HANDBACK_VERIFYING is automation
    cs.handback_ok()
    token.require_automation()  # RUNNING again


def test_token_reads_live_state_not_a_snapshot() -> None:
    cs = ControlState()
    token = cs.token()
    cs.transition(RunState.STUCK_EVALUATING, actor="automation")
    cs.transition(RunState.AWAITING_HUMAN, actor="automation")
    with pytest.raises(ControlViolation):
        token.require_automation()
    cs.finish()
    with pytest.raises(ControlViolation, match="FINISHED"):
        token.require_automation()


# ---- illegal transitions ------------------------------------------------------------------


@pytest.mark.parametrize(
    ("start", "to"),
    [
        (RunState.RUNNING, RunState.HUMAN_IN_CONTROL),
        (RunState.RUNNING, RunState.AWAITING_HUMAN),
        (RunState.RUNNING, RunState.HANDBACK_VERIFYING),
        (RunState.RUNNING, RunState.RUNNING),
        (RunState.STUCK_EVALUATING, RunState.HUMAN_IN_CONTROL),
        (RunState.AWAITING_HUMAN, RunState.RUNNING),
        (RunState.HUMAN_IN_CONTROL, RunState.RUNNING),
    ],
)
def test_illegal_transitions_raise(start: RunState, to: RunState) -> None:
    cs = ControlState()
    cs.state = start
    with pytest.raises(IllegalTransition, match="not allowed"):
        cs.transition(to, actor="test")
    assert cs.state is start
    assert cs.history == []


@pytest.mark.parametrize("to", list(RunState))
def test_finished_is_terminal(to: RunState) -> None:
    cs = ControlState()
    cs.finish(reason="done")
    with pytest.raises(IllegalTransition):
        cs.transition(to, actor="test")
    assert cs.state is RunState.FINISHED


def test_claim_with_unknown_or_stale_id_raises() -> None:
    cs = ControlState()
    with pytest.raises(IllegalTransition, match="no pending intervention"):
        cs.claim("int_nope", "alex")
    cs.raise_intervention(_request("int_0001"))
    with pytest.raises(IllegalTransition, match="stale or already resolved"):
        cs.claim("int_0002", "alex")
    assert cs.state is RunState.AWAITING_HUMAN
    assert cs.handoffs == 0


def test_release_without_claim_raises() -> None:
    cs = ControlState()
    with pytest.raises(IllegalTransition, match="no pending intervention"):
        cs.release("int_0001", "retry_step")
    cs.raise_intervention(_request("int_0001"))
    # pending id matches but nobody claimed: AWAITING_HUMAN -> HANDBACK_VERIFYING is illegal
    with pytest.raises(IllegalTransition, match="not allowed"):
        cs.release("int_0001", "retry_step")
    assert cs.state is RunState.AWAITING_HUMAN


def test_stale_id_after_resolution_is_rejected() -> None:
    cs = ControlState()
    _cycle(cs, "int_0001")
    with pytest.raises(IllegalTransition):
        cs.claim("int_0001", "alex")


def test_max_handoffs_enforced_on_third_claim() -> None:
    cs = ControlState(max_handoffs=2)
    _cycle(cs, "int_0001")
    _cycle(cs, "int_0002")
    assert cs.handoffs == 2
    cs.raise_intervention(_request("int_0003"))
    with pytest.raises(IllegalTransition, match=r"max_handoffs \(2\) exceeded"):
        cs.claim("int_0003", "alex")
    assert cs.state is RunState.AWAITING_HUMAN


def test_max_handoffs_zero_refuses_any_claim() -> None:
    cs = ControlState(max_handoffs=0)
    cs.raise_intervention(_request())
    with pytest.raises(IllegalTransition, match="max_handoffs"):
        cs.claim("int_0001", "alex")


# ---- persistence --------------------------------------------------------------------------


def test_state_json_written_on_init_and_every_transition(tmp_path: Path) -> None:
    run_dir = tmp_path / "run"
    cs = ControlState(run_dir=run_dir)
    state_file = run_dir / "state.json"
    assert state_file.exists()
    assert json.loads(state_file.read_text())["state"] == "RUNNING"

    seen: list[str] = []
    cs.raise_intervention(_request())
    seen.append(json.loads(state_file.read_text())["state"])
    cs.claim("int_0001", "alex")
    seen.append(json.loads(state_file.read_text())["state"])
    cs.release("int_0001", "retry_step")
    seen.append(json.loads(state_file.read_text())["state"])
    cs.handback_ok()
    seen.append(json.loads(state_file.read_text())["state"])
    assert seen == ["AWAITING_HUMAN", "HUMAN_IN_CONTROL", "HANDBACK_VERIFYING", "RUNNING"]

    snap = json.loads(state_file.read_text())
    assert snap["controller"] == "automation"
    assert snap["handoffs"] == 1
    assert snap["max_handoffs"] == 2
    assert snap["pending"] is None


def test_intervention_json_persisted_and_updated(tmp_path: Path) -> None:
    cs = ControlState(run_dir=tmp_path)
    intervention_file = tmp_path / "intervention.json"
    assert not intervention_file.exists()
    cs.raise_intervention(_request("int_abcd"))
    data = json.loads(intervention_file.read_text())
    assert data["intervention_id"] == "int_abcd"
    assert "claimed_by" not in data  # exclude_none
    cs.claim("int_abcd", "alex")
    data = json.loads(intervention_file.read_text())
    assert data["claimed_by"] == "alex"
    cs.release("int_abcd", "complete", note="done by hand")
    data = json.loads(intervention_file.read_text())
    assert data["resolution"] == "complete"
    assert data["note"] == "done by hand"
    assert data["released_at"]


def test_history_entries_carry_actor_and_reason(tmp_path: Path) -> None:
    cs = ControlState(run_dir=tmp_path)
    cs.raise_intervention(_request())
    cs.claim("int_0001", "alex")
    cs.release("int_0001", "skip_step")
    cs.handback_ok("verification passed")
    history = json.loads((tmp_path / "state.json").read_text())["history"]
    assert [(h["actor"], h["reason"]) for h in history] == [
        ("automation", "resolution_failed"),
        ("automation", "could not resolve the Go button"),
        ("operator:alex", "claim"),
        ("operator:alex", "skip_step"),
        ("automation", "verification passed"),
    ]
    assert all(h["ts"] for h in history)
    assert history[2]["from_state"] == "AWAITING_HUMAN"
    assert history[2]["to_state"] == "HUMAN_IN_CONTROL"


def test_no_run_dir_means_no_files(tmp_path: Path) -> None:
    cs = ControlState()
    cs.raise_intervention(_request())
    assert cs.run_dir is None
    assert list(tmp_path.iterdir()) == []


# ---- listener and finish ------------------------------------------------------------------


def test_on_transition_listener_receives_transition_objects() -> None:
    seen: list[Transition] = []
    cs = ControlState(on_transition=seen.append)
    cs.raise_intervention(_request())
    cs.claim("int_0001", "alex")
    assert len(seen) == 3
    assert all(isinstance(t, Transition) for t in seen)
    assert [(t.from_state, t.to_state) for t in seen] == [
        (RunState.RUNNING, RunState.STUCK_EVALUATING),
        (RunState.STUCK_EVALUATING, RunState.AWAITING_HUMAN),
        (RunState.AWAITING_HUMAN, RunState.HUMAN_IN_CONTROL),
    ]
    assert seen[-1].actor == "operator:alex"
    assert seen == cs.history


def test_listener_not_called_on_illegal_transition() -> None:
    seen: list[Transition] = []
    cs = ControlState(on_transition=seen.append)
    with pytest.raises(IllegalTransition):
        cs.transition(RunState.HUMAN_IN_CONTROL, actor="test")
    assert seen == []


def test_finish_is_idempotent() -> None:
    seen: list[Transition] = []
    cs = ControlState(on_transition=seen.append)
    cs.finish(actor="operator:alex", reason="abort")
    cs.finish()
    cs.finish(reason="again")
    assert cs.state is RunState.FINISHED
    assert cs.controller is Controller.NONE
    assert len(cs.history) == 1
    assert len(seen) == 1
    assert cs.history[0].actor == "operator:alex"
    assert cs.history[0].reason == "abort"


@pytest.mark.parametrize(
    "state",
    [s for s in RunState if s is not RunState.FINISHED],
)
def test_finish_is_reachable_from_every_live_state(state: RunState) -> None:
    cs = ControlState()
    cs.state = state
    cs.finish()
    assert cs.state is RunState.FINISHED


def test_intervention_request_ids_are_unique_and_prefixed() -> None:
    ids = {InterventionRequest.new_id() for _ in range(20)}
    assert len(ids) == 20
    assert all(i.startswith("int_") and len(i) == 12 for i in ids)
