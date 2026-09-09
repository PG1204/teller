"""The hero handoff: replay hits an unknown interstitial, pauses on the same live browser, a
"human" (a second CDP client, standing in for the operator's hands) clicks through it, control
is handed back, and the run completes with the handoff and the human's actions recorded."""

from __future__ import annotations

import json
import socket
import threading
import time
from pathlib import Path

import pytest

from teller.hitl.commands import append_command, read_intervention
from teller.hitl.handoff import HandoffController
from teller.replay.executor import ReplayConfig, ReplayRunner
from tests.integration.conftest import chaos, chaos_reset

pytestmark = pytest.mark.integration

ARTIFACT = Path("tests/fixtures/sample_capability.yaml")


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _wait_for_intervention(run_dir: Path, timeout: float = 30) -> dict:
    deadline = time.time() + timeout
    while time.time() < deadline:
        req = read_intervention(run_dir)
        if req and not req.get("released_at"):
            return req
        time.sleep(0.2)
    raise AssertionError("no intervention was raised")


def _human_clicks_i_attest(cdp_port: int) -> None:
    """The operator's hands: a separate CDP client driving the SAME browser session."""
    from playwright.sync_api import sync_playwright

    with sync_playwright() as pw:
        browser = pw.chromium.connect_over_cdp(f"http://127.0.0.1:{cdp_port}")
        page = next(p for ctx in browser.contexts for p in ctx.pages if "/console" in p.url)
        main = next(f for f in page.frames if f.name == "main")
        main.get_by_role("button", name="I attest").click()
        main.wait_for_url("**/console/members/search**", timeout=5000)
        browser.close()  # closes the CDP connection, not the browser


def test_unknown_interstitial_handoff_on_the_same_session(workdir: Path, mock_url: str) -> None:
    chaos_reset(mock_url)
    chaos(mock_url, "interstitial_unknown")
    cdp_port = _free_port()
    cfg = ReplayConfig(artifact_path=ARTIFACT, params={"member_id": "10001"}, root=workdir, runs_dir=workdir / "runs", cdp_port=cdp_port)
    runner = ReplayRunner(cfg, escalator=HandoffController(mode="cli", headed=False))

    def operator() -> None:
        req = _wait_for_intervention(runner.run_dir)
        assert req["trigger"] == "CHECKPOINT_FAILED", req
        assert req["step_id"] == "s1"
        assert req["screenshot"] and (runner.run_dir / req["screenshot"]).exists()
        assert req["session_endpoint"] == f"http://127.0.0.1:{cdp_port}"
        append_command(runner.run_dir, intervention_id=req["intervention_id"], command="claim", operator="alex")
        time.sleep(1.0)  # let the automation thread process the claim and inject the recorder
        _human_clicks_i_attest(cdp_port)
        time.sleep(0.5)
        append_command(runner.run_dir, intervention_id=req["intervention_id"], command="resume", operator="alex", mode="retry_step", note="attested on behalf of the branch")

    t = threading.Thread(target=operator, daemon=True)
    t.start()
    result = runner.run()
    t.join(timeout=5)

    assert result.status == "success", result.model_dump()
    assert result.outputs["savings_balance"] == "2431.17"
    assert len(result.handoffs) == 1
    h = result.handoffs[0]
    assert h.claimed_by == "alex" and h.resume_mode == "retry_step" and h.trigger == "CHECKPOINT_FAILED"
    assert h.action_count >= 1, "the human's click must be captured by the recorder"
    actions = [json.loads(line) for line in (runner.run_dir / "human_actions.jsonl").read_text().splitlines()]
    assert any(a.get("kind") == "click" and "attest" in json.dumps(a).lower() for a in actions), actions
    events = [json.loads(line) for line in (runner.run_dir / "events.jsonl").read_text().splitlines()]
    types = [e["type"] for e in events]
    for t_ in ("escalation", "handoff.requested", "handoff.claimed", "human_action", "handoff.released"):
        assert t_ in types, t_
    controllers = {e["controller"] for e in events if e["type"] == "human_action"}
    assert controllers == {"human"}, "human actions must be logged while the human holds control"
    assert (runner.run_dir / "screenshots" / "handoff_before.jpg").exists()
    assert (runner.run_dir / "screenshots" / "handoff_after.jpg").exists()
    state = json.loads((runner.run_dir / "state.json").read_text())
    transitions = [(t_["from_state"], t_["to_state"]) for t_ in state["history"]]
    assert ("RUNNING", "STUCK_EVALUATING") in transitions
    assert ("AWAITING_HUMAN", "HUMAN_IN_CONTROL") in transitions
    assert ("HUMAN_IN_CONTROL", "HANDBACK_VERIFYING") in transitions
    assert ("HANDBACK_VERIFYING", "RUNNING") in transitions


def test_automation_cannot_act_while_human_holds_control() -> None:
    from teller.hitl.state import ControlState, ControlViolation, InterventionRequest

    st = ControlState()
    token = st.token()
    token.require_automation()
    st.raise_intervention(InterventionRequest(intervention_id="int_x", run_id="r", mode="replay", trigger="T", reason="r"))
    with pytest.raises(ControlViolation):
        token.require_automation()
    st.claim("int_x", "alex")
    with pytest.raises(ControlViolation):
        token.require_automation()
    st.release("int_x", "retry_step")
    st.handback_ok()
    token.require_automation()


def test_escalation_timeout_when_nobody_claims(workdir: Path, mock_url: str) -> None:
    chaos_reset(mock_url)
    chaos(mock_url, "app_error")
    cfg = ReplayConfig(artifact_path=ARTIFACT, params={"member_id": "10001"}, root=workdir, runs_dir=workdir / "runs")
    ctl = HandoffController(mode="cli", headed=False, default_timeout_s=2)
    runner = ReplayRunner(cfg, escalator=ctl)
    # the artifact's handoff_timeout_s is 900; shrink it for the test via the loaded capability
    original = runner._preflight

    def preflight() -> None:
        original()
        runner.cap.escalation_policy.handoff_timeout_s = 2  # type: ignore[union-attr]

    runner._preflight = preflight  # type: ignore[method-assign]
    result = runner.run()
    assert result.status == "failure"
    assert result.failure.code == "ESCALATION_TIMEOUT"
    assert "APP_ERROR" in result.failure.message
