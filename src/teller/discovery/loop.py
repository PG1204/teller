"""The discovery loop: observe -> decide -> act, until done, stuck, or out of budget.

The model (behind ``Decider``) only ever chooses a tool and a mark id. Everything else is the
harness: login with credentials the model never sees, the ``PolicyGate`` inside ``Surface.act``,
the ``Recorder`` that captures each executed action as an artifact step, stuck detection, and
evidence (events, badged screenshots, redacted transcript, usage, optional cassette).

Stuck triggers (cheapest first): MODEL_SELF_REPORT (ask_human) · NO_PROGRESS (3 identical
observations) · OSCILLATION (A-B-A-B within 6 actions) · POLICY_BLOCKED_TWICE ·
IRREVERSIBLE_STEP · UNKNOWN_STATE (refusal, prose-only twice, blank page) · BUDGET.
Escalation writes an ``InterventionRequest``; the handoff controller may hand the live
session to a human and resume, otherwise the run ends as ``needs_human``.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from teller.artifact.model import AppProfile, Capability, RiskClass, Tenant
from teller.artifact.store import save_capability, to_yaml
from teller.discovery.emit import OutputDecl, ParamDecl, emit_capability
from teller.discovery.llm import Decider, DeciderError, ToolFeedback, cassette_from_transcript
from teller.discovery.prompts import SYSTEM_PROMPT, format_observation, goal_message
from teller.discovery.recorder import RecordedStep, Recorder, build_target, canonicalize
from teller.discovery.tools import TOOLS, validate_call
from teller.evidence.log import EventLog, new_run_id, now_iso
from teller.hitl.state import ControlState, InterventionRequest, RunState
from teller.policy.gate import PolicyGate
from teller.policy.model import Policy
from teller.policy.redact import Redactor
from teller.replay.detectors import EvalContext, css_selectors, evaluate
from teller.replay.result import (
    CapabilityRef,
    Declined,
    DeclinedCode,
    DeclinedResult,
    Failure,
    FailureCode,
    FailureResult,
    Handoff,
    NeedsHumanResult,
    StepReport,
    SuccessResult,
    Timing,
)
from teller.replay.steps import run_step
from teller.surface.base import Action, ConfirmationRequired, Observation, PolicyDenied
from teller.surface.web_playwright import WebPlaywrightSurface

log = logging.getLogger("teller.discovery")


@dataclass
class DiscoveryConfig:
    goal: str
    params: dict[str, str]
    tenant: Tenant
    profile: AppProfile
    policy: Policy
    capability_id: str
    title: str
    description: str | None = None
    version: str = "1.0.0"
    param_decls: dict[str, ParamDecl] = field(default_factory=dict)
    output_decls: dict[str, OutputDecl] = field(default_factory=dict)
    max_steps: int = 20
    max_seconds: int = 360
    headed: bool = False
    runs_dir: Path = Path("runs")
    save_to: Path | None = Path("capabilities")
    record_cassette: bool = True
    operator: str = "operator"
    cdp_port: int | None = None
    settle_ms: int = 1500
    version_range: str = "*"
    surface: str = "web_legacy"
    entry_url: str = "{base_url}/console"


@dataclass
class DiscoveryReport:
    run_id: str
    run_dir: Path
    status: str
    capability: Capability | None
    artifact_path: Path | None
    outputs: dict[str, str]
    turns: int
    result: Any
    reason: str = ""


# Called when the loop is stuck. Returns a resume mode (retry|continue|complete) or None to stop.
Escalator = Callable[["DiscoveryRunner", InterventionRequest], str | None]


class DiscoveryRunner:
    def __init__(self, cfg: DiscoveryConfig, decider: Decider, escalator: Escalator | None = None):
        self.cfg = cfg
        self.decider = decider
        self.escalator = escalator
        self.run_id = new_run_id("disc")
        self.run_dir = cfg.runs_dir / self.run_id
        self.run_dir.mkdir(parents=True, exist_ok=True)
        (self.run_dir / "screenshots").mkdir(exist_ok=True)
        self.redactor = Redactor(cfg.policy, sensitive_selectors=cfg.profile.sensitive_selectors)
        self.log = EventLog(self.run_dir, self.run_id, "discovery", redact=self.redactor.scrub)
        self.state = ControlState(self.run_dir, on_transition=self._on_transition, redact=self.redactor.scrub)
        self.gate = PolicyGate(cfg.policy)
        self.surface = WebPlaywrightSurface(
            self.gate, self.redactor, headed=cfg.headed, cdp_port=cfg.cdp_port,
            on_event=lambda t, p: self.log.emit("act" if t == "act" else "observe" if t == "observe" else "policy_check" if t == "policy_check" else "warning", {"surface": t, **p}),
        )
        self.recorder = Recorder(params=dict(cfg.params))
        self.ctx = EvalContext(params=dict(cfg.params), detectors=cfg.profile.detectors)
        self.turn = 0
        self.started = time.monotonic()
        self.started_at = now_iso()
        self.paused_ms = 0
        self._history: list[tuple[str, str]] = []  # (url, digest)
        self._actions: list[tuple[str, str, str]] = []  # (tool, hint, frame)
        self._blocked_in_a_row = 0
        self._no_tool_in_a_row = 0
        self._pending: RecordedStep | None = None
        self._screens: list[str] = []
        self.status = "running"
        self.handoffs: list[Handoff] = []
        self.max_handoffs = 2

    # ---------------------------------------------------------------- plumbing

    def _on_transition(self, t: Any) -> None:
        self.log.set_controller(t.controller.value)
        kind = ("handoff.claimed" if t.to_state is RunState.HUMAN_IN_CONTROL
                else "handoff.released" if t.from_state is RunState.HUMAN_IN_CONTROL else "escalation")
        self.log.emit(kind, {"from": t.from_state, "to": t.to_state, "reason": t.reason}, actor=t.actor)

    def _secrets(self) -> dict[str, str]:
        out: dict[str, str] = {}
        for name in self.cfg.tenant.credentials:
            val = os.environ.get(name)
            if not val:
                raise DeciderError(f"credential {name} is not set in the environment")
            out[name] = val
            self.redactor.register_secret(val)
        return out

    def _observe(self, *, badges: bool = True, save: bool = True) -> Observation:
        obs = self.surface.observe(badges=badges)
        hits = [n for n, p in self.cfg.profile.detectors.items() if evaluate(p, obs, self.ctx)]
        obs.detector_hits = hits
        if save and obs.screenshot_jpeg:
            path = self.run_dir / "screenshots" / f"turn_{self.turn:02d}.jpg"
            path.write_bytes(obs.screenshot_jpeg)
            obs.screenshot_path = str(path.relative_to(self.run_dir))
            self._screens.append(obs.screenshot_path)
        return obs

    def _settle(self) -> None:
        """Wait (bounded) until two consecutive observations agree — no fixed sleeps."""
        deadline = time.monotonic() + self.cfg.settle_ms / 1000
        last = None
        while time.monotonic() < deadline:
            self.surface.page.wait_for_timeout(200)
            o = self.surface.observe(badges=False)
            key = (o.url, o.text_digest, o.dialog.message if o.dialog else None)
            if key == last:
                return
            last = key

    # ---------------------------------------------------------------- lifecycle

    def run(self) -> DiscoveryReport:
        cfg = self.cfg
        self.log.emit("run.start", {"goal": cfg.goal, "params": self.redactor.masked_params_raw(cfg.params, cfg.param_decls), "tenant": cfg.tenant.tenant, "provider": self.decider.name, "model": self.decider.model, "max_steps": cfg.max_steps})
        for name, val in cfg.params.items():
            d = cfg.param_decls.get(name)
            if d:
                self.redactor.register_value(val, d.classification)
        try:
            secrets = self._secrets()
            base = cfg.tenant.base_url.rstrip("/")
            entry = (cfg.profile.login.entry_url if cfg.profile.login else f"{base}/").replace("{base_url}", base)
            self.surface.probe_selectors = css_selectors(list(cfg.profile.detectors.values()))
            self.surface.start(entry)
            self._login(secrets)
            report = self._loop()
        except (DeciderError, PolicyDenied) as e:
            report = self._finish_failure(FailureCode.INTERNAL_ERROR if isinstance(e, DeciderError) else FailureCode.POLICY_VIOLATION, str(e))
        except Exception as e:  # noqa: BLE001 - always leave evidence behind
            log.exception("discovery crashed")
            report = self._finish_failure(FailureCode.INTERNAL_ERROR, f"{type(e).__name__}: {e}")
        finally:
            self._write_evidence()
            self.surface.stop()
        return report

    def _login(self, secrets: dict[str, str]) -> None:
        login = self.cfg.profile.login
        if not login:
            return
        token = self.state.token()
        for step in login.steps:
            r = run_step(self.surface, token, step, self.cfg.params, secrets, self.ctx)
            if not r.ok:
                raise DeciderError(f"harness login failed at {step.id}: {r.error}")
        if login.checkpoint:
            obs = self.surface.observe(badges=False)
            if not evaluate(login.checkpoint, obs, self.ctx):
                raise DeciderError("harness login checkpoint failed")
        self.log.emit("login", {"ok": True, "steps": [s.id for s in login.steps]})

    # ---------------------------------------------------------------- the loop

    def _loop(self, feedback: ToolFeedback | None = None, *, first: bool = True) -> DiscoveryReport:
        cfg = self.cfg
        if first:
            self.decider.start(SYSTEM_PROMPT, TOOLS, goal_message(cfg.goal, cfg.params, {k: v.description or k for k, v in cfg.output_decls.items()} or None))
        token = self.state.token()
        while True:
            self.turn += 1
            if self.turn > cfg.max_steps:
                return self._escalate_or_finish("BUDGET", f"max_steps ({cfg.max_steps}) reached without done()")
            if time.monotonic() - self.started - self.paused_ms / 1000 > cfg.max_seconds:
                return self._escalate_or_finish("BUDGET", f"wall clock ({cfg.max_seconds}s) exhausted")

            obs = self._observe()
            if self._pending is not None:
                self._pending.after = obs
                self.recorder.record(self._pending)
                self._pending = None
            self.log.emit("observe", {"turn": self.turn, "url": obs.url, "elements": len(obs.elements), "dialog": obs.dialog.message if obs.dialog else None, "detectors": obs.detector_hits, "screenshot": obs.screenshot_path})

            # cheap stuck checks on the observation itself
            self._history.append((obs.url, obs.text_digest))
            if len(self._history) >= 3 and len({h for h in self._history[-3:]}) == 1:
                return self._escalate_or_finish("NO_PROGRESS", "three consecutive identical observations")
            if obs.dialog is None and not obs.elements:
                return self._escalate_or_finish("UNKNOWN_STATE", "page shows nothing actionable")
            if "app_error" in obs.detector_hits or "session_expired" in obs.detector_hits or "login_wall" in obs.detector_hits:
                return self._escalate_or_finish("UNKNOWN_STATE", f"detector fired: {obs.detector_hits}")

            obs_text = format_observation(obs, turn=self.turn)
            decision = self.decider.decide(obs_text, obs.screenshot_jpeg, feedback)
            self.log.emit("decide", {"turn": self.turn, "tool": decision.tool, "args": decision.args, "text": decision.text, "stop_reason": decision.stop_reason, "usage": decision.raw_usage, "latency_ms": decision.latency_ms})

            if decision.tool == "__refusal__":
                return self._escalate_or_finish("UNKNOWN_STATE", "model refused to continue")
            if decision.tool == "__no_tool__":
                self._no_tool_in_a_row += 1
                if self._no_tool_in_a_row >= 2:
                    return self._escalate_or_finish("UNKNOWN_STATE", "model replied in prose twice without a tool call")
                feedback = ToolFeedback("none", False, "You must call exactly one tool each turn.")
                continue
            self._no_tool_in_a_row = 0
            err = validate_call(decision.tool, decision.args)
            if err:
                feedback = ToolFeedback(decision.tool, False, err)
                continue
            args = decision.args
            intent = str(args.get("intent", ""))

            # terminal tools
            if decision.tool == "ask_human":
                return self._escalate_or_finish("MODEL_SELF_REPORT", str(args.get("reason", "")))
            if decision.tool == "done":
                missing = [o for o in cfg.output_decls if o not in self.recorder.outputs]
                if missing:
                    feedback = ToolFeedback("done", False, f"Not done: outputs {missing} have not been recorded with read_value.")
                    continue
                cp_text = str(args.get("checkpoint_text") or "").strip()
                if cp_text:
                    frame = "main" if "main" in obs.frames else None
                    visible = (obs.visible_text.get(frame or "", "") if frame else obs.text_of()).lower()
                    if cp_text.lower() in visible:
                        self.recorder.add_checkpoint(cp_text, frame)
                        self.log.emit("checkpoint", {"text_contains": cp_text, "frame": frame, "ok": True})
                    else:
                        feedback = ToolFeedback("done", False, f"checkpoint_text {cp_text!r} is not visible on the current screen; use text that is.")
                        continue
                return self._finish_success(obs, str(args.get("summary", "")))
            if decision.tool == "assert_checkpoint":
                text = str(args["text_contains"])
                frame = "main" if "main" in obs.frames else None
                ok = text.lower() in (obs.visible_text.get(frame or "", "") if frame else obs.text_of()).lower()
                if ok:
                    self.recorder.add_checkpoint(text, frame)
                    self.log.emit("checkpoint", {"text_contains": text, "frame": frame, "ok": True})
                feedback = ToolFeedback("assert_checkpoint", ok, "Checkpoint recorded." if ok else f"{text!r} is not visible on the current screen.")
                continue

            # acting tools
            try:
                feedback = self._act(token, decision.tool, args, intent, obs)
            except PolicyDenied as e:
                self._blocked_in_a_row += 1
                self.log.emit("policy_check", {"blocked": True, "reason": str(e), "tool": decision.tool})
                if self._blocked_in_a_row >= 2:
                    return self._escalate_or_finish("POLICY_BLOCKED_TWICE", str(e))
                feedback = ToolFeedback(decision.tool, False, f"Blocked by policy: {e}. Choose a different action.")
                continue
            except ConfirmationRequired as e:
                return self._escalate_or_finish("IRREVERSIBLE_STEP", f"{e} (tool={decision.tool}, intent={intent!r})")
            self._blocked_in_a_row = 0
            # oscillation check
            if len(self._actions) >= 4 and self._actions[-1] == self._actions[-3] and self._actions[-2] == self._actions[-4]:
                return self._escalate_or_finish("OSCILLATION", "repeating the same two actions")

    def _act(self, token: Any, tool: str, args: dict[str, Any], intent: str, obs: Observation) -> ToolFeedback:
        cfg = self.cfg
        element = None
        mark = args.get("mark_id")
        if tool in ("click", "type_text", "select_option", "read_value") or (tool in ("press_key", "scroll") and mark is not None):
            try:
                element = obs.element(int(mark))
            except (KeyError, TypeError, ValueError):
                return ToolFeedback(tool, False, f"mark id {mark!r} is not on the current screen; look at the element table.")
        kind_map = {"click": "click", "type_text": "type", "select_option": "select", "press_key": "press", "navigate": "navigate", "read_value": "read", "scroll": "scroll", "dismiss_dialog": "dismiss_dialog"}
        kind = kind_map[tool]
        raw_value = args.get("text")
        value = None
        if raw_value is not None:
            # expand {param} placeholders the model may have typed; canonicalise afterwards
            value = str(raw_value)
            for k, v in cfg.params.items():
                value = value.replace("{" + k + "}", str(v))
        action = Action(kind=kind, value=value, option=args.get("option"), key=args.get("key"), url=self._abs_url(args.get("url")), accept=args.get("accept"), clear_first=bool(args.get("clear_first", True)), frame=element.frame if element else ("main" if "main" in obs.frames else None))
        resolution = self.surface.resolve_mark(element) if element is not None else None
        if element is not None and (resolution is None or not resolution.ok):
            return ToolFeedback(tool, False, "that element is no longer on screen; observe again")
        before = obs
        result = self.surface.act(token, action, resolution)
        risk: RiskClass = result.risk_class
        self._actions.append((tool, (element.name or element.text) if element else str(args.get("url") or args.get("key") or ""), element.frame if element else ""))
        if not result.ok:
            return ToolFeedback(tool, False, result.note or "the action failed")
        self._settle()
        extracted = result.text if kind == "read" else None
        output_name = str(args["output_name"]) if tool == "read_value" else None
        if output_name and extracted is not None:
            decl = cfg.output_decls.get(output_name, OutputDecl())
            self.redactor.register_value(extracted, decl.classification)
        canon_value = canonicalize(value, cfg.params) if value is not None else None
        step = RecordedStep(kind=kind, intent=intent, element=element, target=build_target(element, cfg.params, for_read=(kind == "read")) if element else None, value=canon_value, option=args.get("option"), key=args.get("key"), url=action.url, accept=args.get("accept"), clear_first=action.clear_first, risk_class=risk, act=result, before=before, output_name=output_name, extracted=extracted, turn=self.turn, screenshot=obs.screenshot_path)
        self._pending = step
        desc = f"{tool} on [{element.mark_id}] {element.role} {element.name!r}" if element else tool
        note = desc
        if kind == "read":
            shown = extracted if cfg.output_decls.get(output_name or "", OutputDecl()).classification not in ("pii_high", "secret") else "<masked>"
            note = f"{desc} -> recorded output {output_name} = {shown!r}"
        elif result.navigated:
            note = f"{desc}; the page changed"
        elif result.dialog_opened:
            note = f"{desc}; a dialog opened — answer it with dismiss_dialog"
        self.log.emit("act", {"turn": self.turn, "tool": tool, "intent": intent, "target": element.short() if element else None, "risk_class": risk, "navigated": result.navigated, "dialog_opened": result.dialog_opened, "output": output_name}, step_id=f"s{len(self.recorder.steps) + 1}")
        return ToolFeedback(tool, True, note)

    def _abs_url(self, url: Any) -> str | None:
        if not url:
            return None
        u = str(url)
        if u.startswith("/"):
            return self.cfg.tenant.base_url.rstrip("/") + u
        return u

    # ---------------------------------------------------------------- endings

    def _escalate_or_finish(self, trigger: str, reason: str) -> DiscoveryReport:
        self.log.emit("escalation", {"trigger": trigger, "reason": reason, "turn": self.turn})
        if self.escalator is not None and self.state.handoffs >= self.max_handoffs:
            return self._finish_failure(FailureCode.HANDOFF_LIMIT_EXCEEDED, f"{trigger}: {reason} (max_handoffs={self.max_handoffs} reached)")
        shot = self.run_dir / "screenshots" / f"escalation_{self.turn:02d}.jpg"
        try:
            self.surface.screenshot(str(shot))
        except Exception:  # noqa: BLE001
            shot = None  # type: ignore[assignment]
        req = InterventionRequest(
            intervention_id=InterventionRequest.new_id(), run_id=self.run_id, mode="discovery",
            goal=self.cfg.goal, step_id=f"s{len(self.recorder.steps) + 1}", trigger=trigger, reason=reason,
            url=self.surface.current_url(), screenshot=str(shot.relative_to(self.run_dir)) if shot else None,
            last_events=self.log.tail(5), session_endpoint=self.surface.session_endpoint(),
            allowed_resolutions=["retry_step", "complete", "abort"],
        )
        self.state.raise_intervention(req)
        if self.escalator is not None:
            t0 = time.monotonic()
            mode = self.escalator(self, req)
            self.paused_ms += int((time.monotonic() - t0) * 1000)
            if mode in ("retry_step", "skip_step"):
                self._history.clear()
                self._actions.clear()
                return self._loop_after_resume()
            if mode == "complete":
                return self._finish_success(self.surface.observe(badges=False), "completed by operator")
            if mode in ("abort", "decline"):
                code = DeclinedCode.CONFIRMATION_DECLINED if mode == "decline" else DeclinedCode.ABORTED_BY_HUMAN
                by = self.handoffs[-1].claimed_by if self.handoffs else None
                return self._finish_declined(code, by, reason)
            return self._finish_failure(FailureCode.ESCALATION_TIMEOUT, f"nobody claimed the intervention ({trigger}: {reason})")
        self.status = "needs_human"
        result = NeedsHumanResult(
            run_id=self.run_id, mode="discovery", goal=self.cfg.goal, tenant=self.cfg.tenant.tenant,
            params=self._masked_params(), steps=self._step_reports(), handoffs=list(self.handoffs), evidence_dir=str(self.run_dir),
            timing=self._timing(), llm_invoked=True, policy_sha256=self.cfg.policy.sha256(),
            intervention_id=req.intervention_id, trigger=trigger, step_id=req.step_id,
        )
        self._write_result(result)
        self.state.finish(reason=f"{trigger}: no operator")
        return DiscoveryReport(self.run_id, self.run_dir, "needs_human", None, None, dict(self.recorder.outputs), self.turn, result, reason=f"{trigger}: {reason}")

    def _loop_after_resume(self) -> DiscoveryReport:
        # a human intervened; tell the model and continue with a fresh observation
        note = "An operator intervened in the live session and handed control back. Continue from the current screen."
        self.recorder.human_steps.append(f"s{len(self.recorder.steps) + 1}")
        return self._loop(ToolFeedback("ask_human", True, note), first=False)

    def _finish_success(self, obs: Observation, summary: str) -> DiscoveryReport:
        if self._pending is not None:
            self._pending.after = obs
            self.recorder.record(self._pending)
            self._pending = None
        transcript_sha = self._write_transcript()
        cap = emit_capability(
            self.recorder, goal=self.cfg.goal, params=self.cfg.params, param_decls=self.cfg.param_decls,
            output_decls=self.cfg.output_decls, capability_id=self.cfg.capability_id, title=self.cfg.title,
            description=self.cfg.description, profile=self.cfg.profile.profile, version_range=self.cfg.version_range,
            surface=self.cfg.surface, entry_url=self.cfg.entry_url, discovered_by=f"{self.decider.name}:{self.decider.model}",
            run_id=self.run_id, transcript_sha256=transcript_sha, version=self.cfg.version, notes=f"model summary: {summary}" if summary else None,
        )
        draft_dir = self.run_dir / "draft"
        draft_path = save_capability(cap, draft_dir)
        artifact_path = save_capability(cap, self.cfg.save_to) if self.cfg.save_to else draft_path
        try:
            artifact_path = Path(artifact_path).resolve().relative_to(Path.cwd().resolve())
        except ValueError:
            pass
        self.log.emit("result", {"status": "success", "outputs": self.redactor.masked_outputs(cap, self.recorder.outputs), "artifact": str(artifact_path), "steps": len(cap.steps)})
        self.status = "success"
        result = SuccessResult(
            run_id=self.run_id, mode="discovery", goal=self.cfg.goal, tenant=self.cfg.tenant.tenant,
            capability=CapabilityRef(id=cap.capability.id, version=cap.capability.version, status="draft"),
            params=self._masked_params(), outputs=self.redactor.masked_outputs(cap, self.recorder.outputs),
            steps=self._step_reports(), handoffs=list(self.handoffs), evidence_dir=str(self.run_dir), timing=self._timing(),
            llm_invoked=True, policy_sha256=self.cfg.policy.sha256(), artifact_path=str(artifact_path),
        )
        self._write_result(result)
        self.state.finish(reason="done")
        return DiscoveryReport(self.run_id, self.run_dir, "success", cap, artifact_path, dict(self.recorder.outputs), self.turn, result)

    def _finish_declined(self, code: DeclinedCode, by: str | None, note: str) -> DiscoveryReport:
        self.status = "declined"
        result = DeclinedResult(
            run_id=self.run_id, mode="discovery", goal=self.cfg.goal, tenant=self.cfg.tenant.tenant,
            params=self._masked_params(), steps=self._step_reports(), handoffs=list(self.handoffs), evidence_dir=str(self.run_dir),
            timing=self._timing(), llm_invoked=True, policy_sha256=self.cfg.policy.sha256(),
            declined=Declined(code=code, by=by, note=note, step_id=f"s{len(self.recorder.steps) + 1}"),
        )
        self._write_result(result)
        self.log.emit("result", {"status": "declined", "code": code})
        self.state.finish(reason=code)
        return DiscoveryReport(self.run_id, self.run_dir, "declined", None, None, dict(self.recorder.outputs), self.turn, result, reason=note)

    def _finish_failure(self, code: FailureCode, message: str) -> DiscoveryReport:
        self.status = "failure"
        shot = None
        try:
            shot = self.surface.screenshot(str(self.run_dir / "screenshots" / "failure.jpg"))
            self.surface.dom_snapshot(str(self.run_dir / "failure.html"))
        except Exception:  # noqa: BLE001
            pass
        result = FailureResult(
            run_id=self.run_id, mode="discovery", goal=self.cfg.goal, tenant=self.cfg.tenant.tenant,
            params=self._masked_params(), steps=self._step_reports(), evidence_dir=str(self.run_dir),
            timing=self._timing(), llm_invoked=True, policy_sha256=self.cfg.policy.sha256(),
            failure=Failure(code=code, message=message, screenshot=shot, step_id=f"s{len(self.recorder.steps) + 1}"),
        )
        self._write_result(result)
        self.log.emit("result", {"status": "failure", "code": code, "message": message})
        try:
            self.state.finish(reason=code)
        except Exception:  # noqa: BLE001
            pass
        return DiscoveryReport(self.run_id, self.run_dir, "failure", None, None, dict(self.recorder.outputs), self.turn, result, reason=message)

    # ---------------------------------------------------------------- evidence

    def _masked_params(self) -> dict[str, str]:
        return self.redactor.masked_params_raw(self.cfg.params, self.cfg.param_decls)

    def _step_reports(self) -> list[StepReport]:
        return [
            StepReport(step_id=f"s{i}", status="ok", index_used=0, locator_kind="mark", duration_ms=s.act.duration_ms if s.act else 0, screenshot=s.screenshot, note=s.intent)
            for i, s in enumerate(self.recorder.steps, start=1)
        ]

    def _timing(self) -> Timing:
        return Timing(started_at=self.started_at, finished_at=now_iso(), duration_ms=int((time.monotonic() - self.started) * 1000), paused_ms=self.paused_ms)

    def _write_result(self, result: Any) -> None:
        data = self.redactor.scrub(result.model_dump(mode="json", exclude_none=True))
        (self.run_dir / "result.json").write_text(json.dumps(data, indent=2), encoding="utf-8")

    def _write_transcript(self) -> str | None:
        turns = self.redactor.scrub(self.decider.transcript())
        # images were never stored in the transcript; point at the saved screenshots by turn
        data = {"provider": self.decider.name, "model": self.decider.model, "screenshots": self._screens, "turns": turns}
        blob = json.dumps(data, indent=2)
        (self.run_dir / "transcript.redacted.json").write_text(blob, encoding="utf-8")
        return hashlib.sha256(blob.encode()).hexdigest()

    def _write_evidence(self) -> None:
        try:
            if not (self.run_dir / "transcript.redacted.json").exists():
                self._write_transcript()
            (self.run_dir / "usage.json").write_text(json.dumps(self.decider.usage(), indent=2), encoding="utf-8")
            if self.cfg.record_cassette and self.decider.name != "scripted":
                cassette = self.redactor.scrub(cassette_from_transcript(self.decider.name, self.decider.model, self.decider.transcript()))
                (self.run_dir / "cassette.json").write_text(json.dumps(cassette, indent=2), encoding="utf-8")
            self.log.emit("run.end", {"status": self.status, "turns": self.turn, "usage": self.decider.usage()})
        except Exception:  # noqa: BLE001
            log.exception("failed writing evidence")


def draft_yaml(cap: Capability) -> str:
    return to_yaml(cap)
