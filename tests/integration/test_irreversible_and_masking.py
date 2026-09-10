"""Irreversible steps and evidence masking against the live mock."""

from __future__ import annotations

import copy
from pathlib import Path

import pytest
import yaml

from teller.artifact.model import Capability
from teller.artifact.store import save_capability
from teller.policy.gate import PolicyGate
from teller.policy.model import load_policy
from teller.policy.redact import Redactor
from teller.replay.executor import ReplayConfig, ReplayRunner
from tests.integration.conftest import chaos_reset

pytestmark = pytest.mark.integration


def _post_transaction_capability(workdir: Path) -> Path:
    """A hand-authored irreversible flow: open a member, go to Post Transaction, press Post.

    Tests use member 10003 so the posted deposit never changes balances other tests assert on."""
    base = yaml.safe_load((workdir / "tests/fixtures/sample_capability.yaml").read_text())
    cap = copy.deepcopy(base)
    cap["capability"]["id"] = "ledgerline.member.post_transaction"
    cap["capability"]["risk_class"] = "irreversible_write"
    cap["capability"]["provenance"]["discovered_by"] = "human"
    cap["outputs"] = {}
    cap["business_outcomes"] = {}
    cap["steps"] = cap["steps"][:4] + [
        {"id": "s5", "action": "click", "intent": "Open the Post Transaction form", "idempotent": True, "risk_class": "read",
         "target": {"frame": "main", "text_hint": "Post Transaction", "locators": [{"kind": "role_name", "role": "link", "name": "Post Transaction"}]},
         "wait_for": {"state": "text", "text": "Post Transaction", "frame": "main", "timeout_ms": 5000}},
        {"id": "s6", "action": "type", "intent": "Enter the amount", "idempotent": True, "risk_class": "read",
         "target": {"frame": "main", "locators": [{"kind": "attr_stable", "attr": "name", "value": "txtF9"}]}, "value": {"literal": "10.00"}},
        {"id": "s7", "action": "click", "intent": "Post the transaction", "idempotent": False, "risk_class": "irreversible_write",
         "target": {"frame": "main", "text_hint": "Post", "locators": [{"kind": "role_name", "role": "button", "name": "Post"}, {"kind": "attr_stable", "attr": "name", "value": "cmdPost"}]},
         "wait_for": {"state": "text", "text": "Transaction Posted", "frame": "main", "timeout_ms": 5000},
         "expect": {"text_contains": "Transaction Posted", "frame": "main"}},
    ]
    for s in cap["steps"]:
        s.pop("on_outcome", None)
    cap["checkpoint"] = {"text_contains": "Transaction Posted", "frame": "main"}
    model = Capability.model_validate(cap)
    return save_capability(model, workdir / "capabilities")


def test_irreversible_step_is_never_performed_unattended(workdir: Path, mock_url: str) -> None:
    chaos_reset(mock_url)
    path = _post_transaction_capability(workdir)
    cfg = ReplayConfig(artifact_path=path, params={"member_id": "10003"}, root=workdir, runs_dir=workdir / "runs", unattended=True)
    # unattended requires approval: approve it first so the gate, not the approval check, is what stops us
    from teller.artifact.store import approve, load_capability

    save_capability(approve(load_capability(path), "reviewer"), workdir / "capabilities")
    result = ReplayRunner(cfg, escalator=None).run()
    assert result.status == "declined", result.model_dump()
    assert result.declined.code == "HUMAN_REQUIRED_UNATTENDED" and result.declined.step_id == "s7"
    events = (workdir / "runs" / result.run_id / "events.jsonl").read_text()
    assert '"confirm_required":true' in events
    assert "Transaction Posted" not in events, "the Post button must not have been pressed"


def test_pre_authorised_irreversible_step_runs(workdir: Path, mock_url: str) -> None:
    chaos_reset(mock_url)
    path = _post_transaction_capability(workdir)
    cfg = ReplayConfig(artifact_path=path, params={"member_id": "10003"}, root=workdir, runs_dir=workdir / "runs", confirm_steps={"s7@1.0.0"})
    result = ReplayRunner(cfg, escalator=None).run()
    assert result.status == "success", result.model_dump()
    assert any(s.step_id == "s7" and s.status == "ok" for s in result.steps)


def test_persisted_screenshots_mask_sensitive_text(workdir: Path, mock_url: str) -> None:
    """Every screenshot path (not only the model's observation) re-wraps regex/literal matches."""
    import os

    from teller.artifact.store import ArtifactStore, load_profile
    from teller.hitl.state import ControlState
    from teller.replay.detectors import EvalContext
    from teller.replay.steps import run_step
    from teller.surface.web_playwright import WebPlaywrightSurface

    st = ArtifactStore(workdir)
    tenant = st.tenant("local")
    profile = load_profile(workdir / "apps/ledgerline-msc/profile.yaml")
    policy = load_policy(workdir / tenant.policy)
    red = Redactor(policy, sensitive_selectors=profile.sensitive_selectors)
    cap = st.load(workdir / "tests/fixtures/sample_capability.yaml", tenant)
    surf = WebPlaywrightSurface(PolicyGate(policy), red)
    token = ControlState().token()
    params = {"member_id": "10001"}
    secrets = {k: os.environ[k] for k in tenant.credentials}
    ctx = EvalContext(params=params, detectors=profile.detectors)
    try:
        surf.start(profile.login.entry_url.replace("{base_url}", tenant.base_url))
        for step in profile.login.steps:
            assert run_step(surf, token, step, params, secrets, ctx).ok
        for step in cap.steps[:4]:
            assert run_step(surf, token, step, params, secrets, ctx).ok
        out = workdir / "shot.jpg"
        surf.screenshot(str(out))  # the replay path, outside observe()
        assert surf.last_mask_count >= 3, "three 10-digit account numbers must have been wrapped for masking"
        masked = out.read_bytes()
        raw = surf._screenshot_bytes(redact=False)
        assert masked != raw and len(masked) > 1000
        html = surf.dom_snapshot(str(workdir / "snap.html"))
        text = Path(html).read_text()
        assert "0004411982" not in text and "4821" not in text
    finally:
        surf.stop()
