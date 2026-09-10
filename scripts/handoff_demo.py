"""Headless handoff demo: replay pauses on an unknown interstitial, a second CDP client (standing in
for the operator's hands) clicks through it on the SAME browser session, control is handed back.

Usage: scripts/handoff_demo.py capabilities/<artifact>.yaml   (mock console running; chaos armed)
The interactive equivalent is `teller replay ... --headed` plus the operator console on :8787.
"""

from __future__ import annotations

import socket
import sys
import threading
import time
from pathlib import Path

from dotenv import load_dotenv

from teller.evidence.log import configure_console_logging
from teller.hitl.commands import append_command, read_intervention
from teller.hitl.handoff import HandoffController
from teller.replay.executor import ReplayConfig, ReplayRunner
from teller.replay.result import exit_code


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def operator(run_dir: Path, cdp_port: int) -> None:
    """Wait for the intervention, claim, act on the live session over CDP, hand back."""
    from playwright.sync_api import sync_playwright

    deadline = time.time() + 60
    while time.time() < deadline:
        req = read_intervention(run_dir)
        if req and not req.get("released_at"):
            break
        time.sleep(0.3)
    else:
        return
    append_command(run_dir, intervention_id=req["intervention_id"], command="claim", operator="demo-operator")
    time.sleep(1.5)  # automation registers the claim and injects the recorder
    with sync_playwright() as pw:
        browser = pw.chromium.connect_over_cdp(f"http://127.0.0.1:{cdp_port}")
        page = next(p for ctx in browser.contexts for p in ctx.pages if "/console" in p.url)
        main = next(f for f in page.frames if f.name == "main")
        main.get_by_role("button", name="I attest").click()
        page.wait_for_timeout(1500)  # let the attestation POST + redirect land on the same session
        browser.close()
    time.sleep(0.5)
    append_command(run_dir, intervention_id=req["intervention_id"], command="resume", operator="demo-operator", mode="retry_step", note="Attested to the privacy policy on behalf of the branch; automation may retry the step.")


def main() -> int:
    load_dotenv()  # mock credentials, like the teller CLI does
    configure_console_logging()
    artifact = Path(sys.argv[1] if len(sys.argv) > 1 else "capabilities/ledgerline.member.read_savings_balance@1.0.0.yaml")
    cdp_port = free_port()
    cfg = ReplayConfig(artifact_path=artifact, params={"member_id": "10001"}, root=Path("."), runs_dir=Path("runs"), cdp_port=cdp_port)
    runner = ReplayRunner(cfg, escalator=HandoffController(mode="cli", headed=False))
    t = threading.Thread(target=operator, args=(runner.run_dir, cdp_port), daemon=True)
    t.start()
    result = runner.run()
    t.join(timeout=5)
    print(result.model_dump_json(indent=2, exclude_none=True, include={"status", "run_id", "outputs", "handoffs", "failure", "evidence_dir"}))
    return exit_code(result)


if __name__ == "__main__":
    sys.exit(main())
