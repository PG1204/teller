"""Replay the sample capability against the live mock: success, business outcomes, recoveries,
hard failures — each with the evidence the result contract promises."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from teller.replay.executor import ReplayConfig, ReplayRunner, version_in_range
from teller.replay.result import EXIT_CODES, Status, exit_code
from tests.integration.conftest import chaos, chaos_reset

pytestmark = pytest.mark.integration

ARTIFACT = Path("tests/fixtures/sample_capability.yaml")


def _run(workdir: Path, mock_url: str, member_id: str = "10001", **kw):
    chaos_reset(mock_url)
    cfg = ReplayConfig(artifact_path=ARTIFACT, params={"member_id": member_id}, tenant=kw.pop("tenant", "local"), root=workdir, runs_dir=workdir / "runs", **kw)
    runner = ReplayRunner(cfg, escalator=None)
    result = runner.run()
    run_dir = runner.run_dir
    assert (run_dir / "result.json").exists()
    assert (run_dir / "events.jsonl").exists()
    return result, run_dir, runner


def _events(run_dir: Path) -> list[dict]:
    return [json.loads(line) for line in (run_dir / "events.jsonl").read_text().splitlines()]


def test_success_returns_typed_outputs(workdir: Path, mock_url: str) -> None:
    result, run_dir, runner = _run(workdir, mock_url)
    assert result.status == "success", result.model_dump()
    assert result.outputs == {"savings_balance": "2431.17", "savings_account_number": "****1982"}
    assert runner.full_outputs["savings_account_number"] == "0004411982"
    assert result.llm_invoked is False
    assert [s.step_id for s in result.steps] == ["s1", "s2", "s3", "s4", "s5", "s6"]
    assert all(s.index_used == 0 for s in result.steps), "primary locators should resolve on the recorded app"
    assert exit_code(result) == 0
    assert len(list((run_dir / "screenshots").glob("s*_after.jpg"))) == 6
    blob = (run_dir / "result.json").read_text() + (run_dir / "events.jsonl").read_text()
    assert "0004411982" not in blob and "Ledger!2026" not in blob


def test_not_found_is_a_business_outcome_not_a_failure(workdir: Path, mock_url: str) -> None:
    result, run_dir, _ = _run(workdir, mock_url, member_id="20002")
    assert result.status == "business_outcome"
    assert result.outcome.code == "MEMBER_NOT_FOUND" and result.outcome.step_id == "s3"
    assert result.outcome.returns == {"member_id": "20002"}
    assert exit_code(result) == 10
    assert any(e["type"] == "condition_detected" and e["payload"]["kind"] == "outcome" for e in _events(run_dir))


def test_access_denied_is_declared_at_s4(workdir: Path, mock_url: str) -> None:
    result, _, _ = _run(workdir, mock_url, member_id="30003")
    assert result.status == "business_outcome"
    assert result.outcome.code == "ACCESS_DENIED" and result.outcome.step_id == "s4"


def test_no_savings_account_outcome(workdir: Path, mock_url: str) -> None:
    result, _, _ = _run(workdir, mock_url, member_id="10004")
    assert result.status == "business_outcome"
    assert result.outcome.code == "NO_SAVINGS_ACCOUNT" and result.outcome.step_id == "s5"


def test_app_error_is_a_hard_failure_with_rich_evidence(workdir: Path, mock_url: str) -> None:
    chaos_reset(mock_url)
    chaos(mock_url, "app_error")
    cfg = ReplayConfig(artifact_path=ARTIFACT, params={"member_id": "10001"}, root=workdir, runs_dir=workdir / "runs", unattended=False)
    runner = ReplayRunner(cfg, escalator=None)
    result = runner.run()
    assert result.status == "failure"
    assert result.failure.code == "APP_ERROR"
    assert result.failure.step_id is not None
    assert result.failure.screenshot and (runner.run_dir / result.failure.screenshot).exists()
    assert result.failure.dom_snapshot and (runner.run_dir / result.failure.dom_snapshot).exists()
    assert result.failure.observed and "ORA-00600" in json.dumps(result.failure.observed)
    assert exit_code(result) == 20


def test_known_interstitial_is_recovered(workdir: Path, mock_url: str) -> None:
    chaos_reset(mock_url)
    chaos(mock_url, "interstitial_known")
    cfg = ReplayConfig(artifact_path=ARTIFACT, params={"member_id": "10001"}, root=workdir, runs_dir=workdir / "runs")
    result = ReplayRunner(cfg, escalator=None).run()
    assert result.status == "success", result.model_dump()
    assert [r.code for r in result.recoveries] == ["INTERSTITIAL_DISMISSED"]
    assert result.recoveries[0].condition_id == "compliance_notice"


def test_slow_load_is_waited_through(workdir: Path, mock_url: str) -> None:
    chaos_reset(mock_url)
    chaos(mock_url, "slow", times=1)
    cfg = ReplayConfig(artifact_path=ARTIFACT, params={"member_id": "10001"}, root=workdir, runs_dir=workdir / "runs")
    result = ReplayRunner(cfg, escalator=None).run()
    assert result.status == "success", result.model_dump()
    assert "SLOW_LOAD_WAITED" in [r.code for r in result.recoveries]


def test_unknown_interstitial_is_a_checkpoint_failure(workdir: Path, mock_url: str) -> None:
    chaos_reset(mock_url)
    chaos(mock_url, "interstitial_unknown")
    cfg = ReplayConfig(artifact_path=ARTIFACT, params={"member_id": "10001"}, root=workdir, runs_dir=workdir / "runs")
    result = ReplayRunner(cfg, escalator=None).run()
    assert result.status == "failure"
    assert result.failure.code in ("CHECKPOINT_FAILED", "STEP_TIMEOUT", "TARGET_NOT_FOUND")
    assert "Attestation" in json.dumps(result.failure.observed)


def test_unknown_dialog_is_dismissed_and_reported(workdir: Path, mock_url: str) -> None:
    chaos_reset(mock_url)
    chaos(mock_url, "dialog_unknown")
    cfg = ReplayConfig(artifact_path=ARTIFACT, params={"member_id": "10001"}, root=workdir, runs_dir=workdir / "runs")
    result = ReplayRunner(cfg, escalator=None).run()
    assert result.status == "failure"
    assert result.failure.code == "UNEXPECTED_DIALOG"
    assert "pending batch" in result.failure.message


def test_known_dialog_is_handled(workdir: Path, mock_url: str) -> None:
    chaos_reset(mock_url)
    chaos(mock_url, "dialog_known")
    cfg = ReplayConfig(artifact_path=ARTIFACT, params={"member_id": "10001"}, root=workdir, runs_dir=workdir / "runs")
    result = ReplayRunner(cfg, escalator=None).run()
    assert result.status == "success", result.model_dump()
    assert "KNOWN_DIALOG_HANDLED" in [r.code for r in result.recoveries]


def test_session_expiry_reestablishes_and_restarts(workdir: Path, mock_url: str) -> None:
    chaos_reset(mock_url)
    chaos(mock_url, "expire_session")
    cfg = ReplayConfig(artifact_path=ARTIFACT, params={"member_id": "10001"}, root=workdir, runs_dir=workdir / "runs")
    result = ReplayRunner(cfg, escalator=None).run()
    assert result.status == "success", result.model_dump()
    assert "SESSION_REESTABLISHED" in [r.code for r in result.recoveries]


def test_param_invalid_fails_before_launch(workdir: Path, mock_url: str) -> None:
    result, run_dir, _ = _run(workdir, mock_url, member_id="abc")
    assert result.status == "failure" and result.failure.code == "PARAM_INVALID"
    assert not list((run_dir / "screenshots").glob("*.jpg")), "no browser should have launched"


def test_unattended_requires_approval(workdir: Path, mock_url: str) -> None:
    result, _, _ = _run(workdir, mock_url, unattended=True)
    assert result.status == "failure" and result.failure.code == "NOT_APPROVED"


def test_tenant_override_changes_locator_and_reports_drift(workdir: Path, mock_url: str) -> None:
    # example-b relabels the nav item; on this build the override's primary locator misses and the
    # engine falls back, recording a DRIFT_WARNING and a suggested override for the reviewer.
    result, run_dir, _ = _run(workdir, mock_url, tenant="example-b")
    assert result.status == "success", result.model_dump()
    assert any(w.code == "DRIFT_WARNING" and w.step_id == "s1" for w in result.warnings)
    assert (run_dir / "suggested_overrides.yaml").exists()


def test_version_range_helper() -> None:
    assert version_in_range("4.2", ">=4.1 <5")
    assert not version_in_range("5.0", ">=4.1 <5")
    assert version_in_range("4.2.7", "*")
    assert not version_in_range("3.9", ">=4")


def test_exit_code_table() -> None:
    assert EXIT_CODES[Status.SUCCESS] == 0 and EXIT_CODES[Status.BUSINESS_OUTCOME] == 10
