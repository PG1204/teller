"""Drive the whole discovery loop without a model: a heuristic decider picks marks by name.

Proves the loop, recorder, emitter and evidence writing end to end against the live mock.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

from teller.artifact.store import ArtifactStore, load_capability
from teller.discovery.emit import OutputDecl, ParamDecl
from teller.discovery.llm import Decision, ToolFeedback
from teller.discovery.loop import DiscoveryConfig, DiscoveryRunner
from teller.policy.model import load_policy

pytestmark = pytest.mark.integration


class HeuristicDecider:
    """Reads the element table and follows a fixed plan by element name/role."""

    name = "scripted"
    model = "heuristic"

    def __init__(self) -> None:
        self.plan = [
            ("click", r"link 'Members'", {}),
            ("type_text", r"textbox 'Member #'", {"text": "{member_id}"}),
            ("click", r"button 'Go'", {}),
            ("click", r"row .*10001", {}),
            ("read_value", r"cell '\$[\d,.]+' cell\(Accounts: Share Savings / Current Balance\)", {"output_name": "savings_balance"}),
            ("read_value", r"cell '<account>' cell\(Accounts: Share Savings / Account #\)", {"output_name": "savings_account_number"}),
            ("assert_checkpoint", None, {"text_contains": "Member Detail"}),
            ("done", None, {"summary": "read the balance"}),
        ]
        self.i = 0
        self._t: list[dict] = []

    def start(self, system_prompt: str, tools: list, first_user_text: str) -> None:
        self._t.append({"role": "user", "text": first_user_text})

    def decide(self, observation_text: str, screenshot_jpeg: bytes | None, feedback: ToolFeedback | None) -> Decision:
        assert feedback is None or feedback.ok, f"previous action failed: {feedback}"
        tool, pattern, extra = self.plan[self.i]
        self.i += 1
        args = {"intent": f"step {self.i}", **extra}
        if pattern:
            m = re.search(r"^\[(\d+)\] " + pattern, observation_text, re.M)
            assert m, f"no element matching {pattern!r} in:\n{observation_text}"
            args["mark_id"] = int(m.group(1))
        self._t.append({"role": "model", "tool": tool, "args": args})
        return Decision(tool=tool, args=args)

    def usage(self) -> dict:
        return {"provider": self.name, "model": self.model, "calls": self.i}

    def transcript(self) -> list[dict]:
        return self._t


def _config(workdir: Path) -> DiscoveryConfig:
    store = ArtifactStore(workdir)
    tenant = store.tenant("local")
    return DiscoveryConfig(
        goal="Look up member 10001 and read the current balance and account number of their Share Savings account",
        params={"member_id": "10001"},
        tenant=tenant,
        profile=store.profile("ledgerline-msc"),
        policy=load_policy(workdir / tenant.policy),
        capability_id="ledgerline.member.read_savings_balance",
        title="Read a member's current Share Savings balance",
        param_decls={"member_id": ParamDecl(classification="pii_low", pattern="^[0-9]{5}$")},
        output_decls={
            "savings_balance": OutputDecl(type="decimal", classification="pii_low", parse="currency_usd"),
            "savings_account_number": OutputDecl(type="string", classification="pii_high"),
        },
        runs_dir=workdir / "runs",
        save_to=workdir / "capabilities",
        record_cassette=False,
    )


def test_discovery_loop_emits_a_replayable_draft(workdir: Path) -> None:
    runner = DiscoveryRunner(_config(workdir), HeuristicDecider())
    report = runner.run()

    assert report.status == "success", report.reason
    assert report.outputs == {"savings_balance": "$2,431.17", "savings_account_number": "0004411982"}
    assert report.artifact_path is not None and report.artifact_path.exists()

    cap = load_capability(report.artifact_path)
    assert [s.action for s in cap.steps] == ["click", "type", "click", "click", "read", "read"]
    s2 = cap.step("s2")
    assert s2.value is not None and s2.value.param == "member_id", "typed literal must be canonicalised"
    assert s2.expect is not None and s2.expect.value_equals is not None
    assert s2.target.locators[0].kind in ("role_name", "label_anchor")
    assert any(loc.kind == "label_anchor" for loc in s2.target.locators)
    s4 = cap.step("s4")
    assert "{member_id}" in json.dumps(s4.target.model_dump(mode="json")), "row text must reference the param"
    s5 = cap.step("s5")
    assert s5.extract is not None and s5.extract.parse == "currency_usd"
    assert s5.target.locators[0].kind == "table_cell"
    assert cap.step("s1").wait_for is not None and cap.step("s1").wait_for.text == "Member Search"
    assert cap.step("s1").expect is not None and cap.step("s1").expect.text_contains == "Member Search"
    assert cap.outputs["savings_account_number"].classification == "pii_high"
    assert cap.checkpoint.all is not None
    assert any(p.output_present == "savings_balance" for p in cap.checkpoint.all)
    assert cap.capability.status == "draft"

    # evidence: events, screenshots, transcript, result; nothing sensitive persisted
    run_dir = report.run_dir
    events = [json.loads(line) for line in (run_dir / "events.jsonl").read_text().splitlines()]
    types = {e["type"] for e in events}
    assert {"run.start", "login", "observe", "decide", "policy_check", "act", "checkpoint", "result", "run.end"} <= types
    assert (run_dir / "transcript.redacted.json").exists()
    assert (run_dir / "usage.json").exists()
    assert len(list((run_dir / "screenshots").glob("turn_*.jpg"))) >= 6
    blob = (run_dir / "events.jsonl").read_text() + (run_dir / "transcript.redacted.json").read_text() + (run_dir / "result.json").read_text()
    assert "Ledger!2026" not in blob
    assert "0004411982" not in blob, "pii_high output must be masked in evidence"
    result = json.loads((run_dir / "result.json").read_text())
    assert result["status"] == "success" and result["llm_invoked"] is True
    assert result["outputs"]["savings_account_number"] == "****1982"
