"""Replay — the production execution path. No model, no guessing.

Given a capability and typed params, the interpreter walks the steps:

    preflight (no browser) -> launch -> harness login -> fingerprint check
    per step: sweep -> resolve -> gate/act -> wait-race -> expect -> extract
    final checkpoint -> typed outputs -> result.json + exit code

Every observation is classified by ``classify.sweep`` (business outcome / recoverable / undeclared
/ unknown dialog). Remedies are bounded by ``max_times``; after a timeout the engine re-observes
and re-acts only if the step is ``idempotent``; session re-establishment restarts from
``restart_anchor`` only if no non-idempotent step has executed. Failures under
``on_hard_failure: pause`` become an intervention (handoff controller, P4); with ``--unattended``
they are terminal.

A guard test asserts this package never imports ``anthropic`` or ``google.genai``.
"""

from __future__ import annotations

import logging
import os
import re
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

from teller.artifact.model import (
    AppProfile,
    Capability,
    Predicate,
    RecoverableCondition,
    Step,
    Tenant,
)
from teller.artifact.store import ArtifactError, ArtifactStore, load_profile
from teller.evidence.log import EventLog, new_run_id, now_iso
from teller.hitl.state import ControlState, InterventionRequest, RunState
from teller.policy.gate import PolicyGate
from teller.policy.model import Policy, load_policy
from teller.policy.redact import Redactor
from teller.replay.classify import Sweep, sweep
from teller.replay.detectors import EvalContext, css_selectors, describe, evaluate, observed_summary
from teller.replay.parsers import ParseError, parse
from teller.replay.result import (
    BusinessOutcomeResult,
    CapabilityRef,
    Declined,
    DeclinedCode,
    DeclinedResult,
    Failure,
    FailureCode,
    FailureResult,
    Handoff,
    NeedsHumanResult,
    Outcome,
    Recovery,
    RecoveryCode,
    StepReport,
    SuccessResult,
    Timing,
    Warning,
)
from teller.replay.steps import StepOutcome, run_step
from teller.surface.base import ConfirmationRequired, Observation, PolicyDenied, Surface
from teller.surface.web_playwright import WebPlaywrightSurface

log = logging.getLogger("teller.replay")

TerminalResult = SuccessResult | BusinessOutcomeResult | FailureResult | DeclinedResult


class _Stop(Exception):
    """Internal control flow: carries the terminal result."""

    def __init__(self, result: TerminalResult):
        self.result = result


@dataclass
class ReplayConfig:
    artifact_path: Path
    params: dict[str, str]
    tenant: str = "local"
    root: Path = Path(".")
    headed: bool = False
    unattended: bool = False
    confirm_steps: set[str] = field(default_factory=set)  # "s7@1.0.0"
    emit_full_outputs: bool = False
    runs_dir: Path = Path("runs")
    operator: str = "operator"
    cdp_port: int | None = None
    max_run_seconds: int | None = None
    surface_factory: Any | None = None  # tests may inject a Surface


# The handoff controller (P4) implements this: pause on the same session, return a resume mode.
Escalator = Any


class ReplayRunner:
    def __init__(self, cfg: ReplayConfig, escalator: Escalator | None = None):
        self.cfg = cfg
        self.escalator = escalator
        self.run_id = new_run_id("rep")
        self.run_dir = cfg.runs_dir / self.run_id
        self.run_dir.mkdir(parents=True, exist_ok=True)
        (self.run_dir / "screenshots").mkdir(exist_ok=True)
        self.store = ArtifactStore(cfg.root)
        self.tenant: Tenant | None = None
        self.cap: Capability | None = None
        self.profile: AppProfile | None = None
        self.policy: Policy | None = None
        self.redactor: Redactor | None = None
        self.log: EventLog | None = None
        self.state: ControlState | None = None
        self.surface: Surface | None = None
        self.ctx = EvalContext(params=dict(cfg.params))
        self.outputs: dict[str, str] = {}
        self.recoveries: list[Recovery] = []
        self.warnings: list[Warning] = []
        self.steps: list[StepReport] = []
        self.handoffs: list[Handoff] = []
        self.budgets: dict[str, int] = {}
        self.non_idempotent_executed = False
        self.started = time.monotonic()
        self.paused_ms = 0
        self.started_at = now_iso()
        self.full_outputs: dict[str, str] = {}

    # ------------------------------------------------------------------ entry

    def run(self) -> TerminalResult:
        try:
            self._preflight()
            assert self.cap and self.profile and self.policy and self.redactor and self.log
            self._launch()
            result = self._execute()
        except _Stop as s:
            result = s.result
        except PolicyDenied as e:
            result = self._failure(FailureCode.POLICY_VIOLATION, str(e))
        except Exception as e:  # noqa: BLE001 - always leave a debuggable result behind
            log.exception("replay crashed")
            result = self._failure(FailureCode.INTERNAL_ERROR, f"{type(e).__name__}: {e}")
        finally:
            if self.surface is not None:
                try:
                    self.surface.stop()
                except Exception:  # noqa: BLE001
                    pass
        self._write_result(result)
        if self.log:
            self.log.emit("result", {"status": result.status, **self._result_summary(result)})
            self.log.emit("run.end", {"status": result.status, "duration_ms": result.timing.duration_ms})
        if self.state:
            try:
                self.state.finish(reason=result.status)
            except Exception:  # noqa: BLE001
                pass
        return result

    # ------------------------------------------------------------------ preflight

    def _preflight(self) -> None:
        cfg = self.cfg
        try:
            self.tenant = self.store.tenant(cfg.tenant)
            self.cap = self.store.load(cfg.artifact_path, self.tenant)
            self.profile = load_profile(self.store.root / "apps" / self.cap.capability.app.profile / "profile.yaml")
            self.policy = load_policy(self.store.root / self.tenant.policy)
        except (ArtifactError, FileNotFoundError, OSError) as e:
            self.policy = self.policy or Policy(origins=["http://invalid"], actions_allow=["read"])
            self.redactor = Redactor(self.policy)
            self.log = EventLog(self.run_dir, self.run_id, "replay", redact=self.redactor.scrub)
            raise _Stop(self._failure(FailureCode.ARTIFACT_INVALID, str(e))) from e
        cap, policy = self.cap, self.policy
        self.redactor = Redactor(policy, sensitive_selectors=self.profile.sensitive_selectors)
        self.redactor.register_params(cap, cfg.params)
        self.log = EventLog(self.run_dir, self.run_id, "replay", redact=self.redactor.scrub)
        self.state = ControlState(self.run_dir, on_transition=self._on_transition, max_handoffs=cap.escalation_policy.max_handoffs)
        self.ctx.detectors = dict(self.profile.detectors)
        self.log.emit("run.start", {
            "capability": cap.capability.id, "version": cap.capability.version, "status": cap.capability.status,
            "tenant": self.tenant.tenant, "params": self.redactor.masked_params(cap, cfg.params),
            "unattended": cfg.unattended, "overrides": cap.overrides or None, "policy_sha256": policy.sha256(),
        })
        # params contract
        problems: list[str] = []
        for name, spec in cap.params.items():
            val = cfg.params.get(name)
            if val is None:
                if spec.required:
                    problems.append(f"missing required param {name!r}")
                continue
            if spec.pattern and not re.fullmatch(spec.pattern, str(val)):
                problems.append(f"param {name!r} does not match {spec.pattern!r}")
            if spec.enum and str(val) not in spec.enum:
                problems.append(f"param {name!r} not in {spec.enum}")
            if spec.type == "integer" and not re.fullmatch(r"-?\d+", str(val)):
                problems.append(f"param {name!r} must be an integer")
        for name in cfg.params:
            if name not in cap.params:
                problems.append(f"unknown param {name!r}")
        if problems:
            raise _Stop(self._failure(FailureCode.PARAM_INVALID, "; ".join(problems)))
        # static policy check
        violations = static_policy_check(cap, policy, self.tenant)
        if violations:
            raise _Stop(self._failure(FailureCode.POLICY_VIOLATION, "; ".join(violations)))
        # approval gate for unattended runs
        if cfg.unattended and cap.capability.status != "approved":
            raise _Stop(self._failure(FailureCode.NOT_APPROVED, f"--unattended requires status approved (is {cap.capability.status})"))
        # version range
        if self.tenant.app_version and not version_in_range(self.tenant.app_version, cap.capability.app.version_range):
            self.warnings.append(Warning(code="VERSION_RANGE_MISMATCH", message=f"tenant app_version {self.tenant.app_version} outside {cap.capability.app.version_range!r}"))
            self.log.emit("warning", {"code": "VERSION_RANGE_MISMATCH", "app_version": self.tenant.app_version})

    # ------------------------------------------------------------------ launch

    def _launch(self) -> None:
        assert self.cap and self.profile and self.policy and self.redactor and self.tenant and self.log
        cfg = self.cfg
        gate = PolicyGate(self.policy)
        if cfg.surface_factory is not None:
            self.surface = cfg.surface_factory(gate, self.redactor)
        else:
            self.surface = WebPlaywrightSurface(
                gate, self.redactor, headed=cfg.headed, cdp_port=cfg.cdp_port,
                on_event=lambda t, p: self.log.emit("policy_check" if t == "policy_check" else "act" if t == "act" else "warning", {"surface": t, **p}) if t in ("policy_check", "act", "dialog.opened", "popup.closed") else None,
            )
        surface = self.surface
        if isinstance(surface, WebPlaywrightSurface):
            surface.probe_selectors = css_selectors(list(self.profile.detectors.values()) + [c.detect for c in self.profile.recoverable_conditions] + [c.detect for c in self.cap.recoverable_conditions] + [b.detect for b in self.cap.business_outcomes.values()])
        base = self.tenant.base_url.rstrip("/")
        login = self.profile.login
        entry = (login.entry_url if login and login.entry_url else self.cap.capability.app.entry_url).replace("{base_url}", base)
        surface.start(entry)
        if login:
            self._login()
        cap_entry = self.cap.capability.app.entry_url.replace("{base_url}", base)
        if surface.current_url().split("?")[0].rstrip("/") not in (cap_entry.rstrip("/"), cap_entry.rstrip("/") + "/home"):
            from teller.surface.base import Action

            surface.act(self.state.token(), Action(kind="navigate", url=cap_entry), None)
        obs = self._observe()
        fp = self.profile.ui_fingerprint
        drift = []
        if fp.frame_names and not set(fp.frame_names) <= set(obs.frames):
            drift.append(f"frames {obs.frames} != {fp.frame_names}")
        if fp.title_pattern and not re.search(fp.title_pattern, obs.title or ""):
            drift.append(f"title {obs.title!r} !~ {fp.title_pattern!r}")
        if drift:
            self.warnings.append(Warning(code="DRIFT_SUSPECTED", message="; ".join(drift)))
            self.log.emit("warning", {"code": "DRIFT_SUSPECTED", "detail": drift})

    def _login(self) -> None:
        assert self.profile and self.profile.login and self.surface and self.state and self.log and self.tenant
        secrets: dict[str, str] = {}
        for name in self.tenant.credentials:
            val = os.environ.get(name)
            if not val:
                raise _Stop(self._failure(FailureCode.ARTIFACT_INVALID, f"credential {name} is not set in the environment"))
            secrets[name] = val
            self.redactor.register_secret(val)  # type: ignore[union-attr]
        token = self.state.token()
        base = self.tenant.base_url.rstrip("/")
        entry = (self.profile.login.entry_url or "{base_url}/login").replace("{base_url}", base)
        if self.surface.current_url().split("?")[0] != entry.split("?")[0] or "main" in self._observe().frames:
            from teller.surface.base import Action

            if self.surface.pending_dialog():
                self.surface.handle_dialog(False)
            self.surface.act(token, Action(kind="navigate", url=entry), None)
        for step in self.profile.login.steps:
            r = run_step(self.surface, token, step, self.cfg.params, secrets, self.ctx)
            if not r.ok:
                why = r.error or ("wait_for did not hold" if not r.wait_ok else "expectation failed")
                raise _Stop(self._hard_failure(FailureCode.CHECKPOINT_FAILED, f"harness login failed at {step.id}: {why}", step, r.observation or self._observe(), expected=f"wait_for {step.wait_for.model_dump(exclude_none=True)}" if step.wait_for else None))
        if self.profile.login.checkpoint and not evaluate(self.profile.login.checkpoint, self._observe(), self.ctx):
            raise _Stop(self._hard_failure(FailureCode.CHECKPOINT_FAILED, "harness login checkpoint failed", None, self._observe(), expected=describe(self.profile.login.checkpoint, self.cfg.params)))
        self.log.emit("login", {"ok": True})

    # ------------------------------------------------------------------ execution

    def _execute(self) -> TerminalResult:
        assert self.cap and self.log
        cap = self.cap
        i = 0
        while i < len(cap.steps):
            step = cap.steps[i]
            self._check_clock()
            try:
                outcome = self._run_one(step)
            except _Retry:  # a human fixed the state; run the same step again...
                if step.expect and evaluate(step.expect, self._observe(), self.ctx):
                    # ...unless the human's actions already produced this step's postcondition
                    self.steps.append(StepReport(step_id=step.id, status="human", note="postcondition already held after handback"))
                    self.log.emit("checkpoint", {"expect": describe(step.expect, self.cfg.params), "ok": True, "by": "handback"}, step_id=step.id)
                    i += 1
                continue
            except _Skip:  # a human performed the step; verify its postcondition and move on
                if step.expect and not evaluate(step.expect, self._observe(), self.ctx):
                    return self._hard_failure(FailureCode.HANDBACK_STATE_MISMATCH, f"skip_step: {step.id} postcondition does not hold", step, self._observe())
                self.steps.append(StepReport(step_id=step.id, status="human", note="performed by operator"))
                i += 1
                continue
            except _Complete:  # a human finished the flow; only the final checkpoint remains
                break
            if outcome == "restart":
                i = cap.step_index(cap.restart_anchor or cap.steps[0].id)
                self.non_idempotent_executed = False
                continue
            i += 1
        obs = self._observe()
        self.ctx.outputs = self.outputs
        if not evaluate(cap.checkpoint, obs, self.ctx):
            return self._hard_failure(FailureCode.CHECKPOINT_FAILED, "final checkpoint did not hold", None, obs, expected=describe(cap.checkpoint, self.cfg.params))
        self.log.emit("checkpoint", {"final": True, "ok": True})
        return self._success()

    def _run_one(self, step: Step, *, attempt: int = 1) -> str:
        """Execute one step fully (with remedies); returns 'next' or 'restart'."""
        assert self.cap and self.profile and self.surface and self.state and self.log
        cap, profile, surface = self.cap, self.profile, self.surface
        t0 = time.monotonic()
        # 1. sweep the current state before acting
        obs = self._observe()
        if attempt > 1 and step.expect and evaluate(step.expect, obs, self.ctx):
            # a remedy (interstitial acknowledged, session re-established, ...) already produced this
            # step's postcondition: acting again would look for a control that is legitimately gone
            self.steps.append(StepReport(step_id=step.id, status="ok", note="postcondition already held after recovery", screenshot=self._shot(f"{step.id}_after")))
            self.log.emit("checkpoint", {"expect": describe(step.expect, self.cfg.params), "ok": True, "after": "recovery"}, step_id=step.id)
            return "next"
        sw = sweep(obs, step, cap, profile, self.ctx, self.budgets)
        if not sw.empty:
            action = self._dispose(sw, step, obs)
            if action == "restart":
                return "restart"
            if action == "retry":
                return self._run_one(step, attempt=attempt + 1)
        # 2..4 resolve, gate, act, wait-race
        confirmed = f"{step.id}@{cap.capability.version}" in self.cfg.confirm_steps
        raced: dict[str, Sweep] = {}

        def on_poll(o: Observation) -> bool:
            s = sweep(o, step, cap, profile, self.ctx, self.budgets)
            if not s.empty:
                raced["hit"] = s
                return True
            return False

        before_shot = None
        if step.risk_class != "read":
            before_shot = self._shot(f"{step.id}_before")
        try:
            r = run_step(surface, self.state.token(), step, self.cfg.params, self._secrets_for(step), self.ctx, confirmed=confirmed, on_poll=on_poll)
        except ConfirmationRequired as e:
            return self._irreversible(step, str(e))
        except PolicyDenied as e:
            raise _Stop(self._hard_failure(FailureCode.POLICY_VIOLATION, str(e), step, obs)) from e
        if r.resolution is not None and r.resolution.index_used:
            self._locator_fallback(step, r)
        if r.resolution is not None and not r.resolution.ok:
            return self._resolution_failed(step, r, attempt)
        if r.act is not None and not r.act.ok:
            raise _Stop(self._hard_failure(FailureCode.SURFACE_CRASHED if "closed" in (r.error or "") else FailureCode.CHECKPOINT_FAILED, r.error or "action failed", step, self._observe()))
        if not step.idempotent:
            self.non_idempotent_executed = True
        if "hit" in raced:
            action = self._dispose(raced["hit"], step, r.observation or self._observe())
            if action == "restart":
                return "restart"
            if action == "retry":
                return self._run_one(step, attempt=attempt + 1)
            # recovered without changing the step's outcome: re-evaluate expect below
            r.observation = self._observe()
            r.wait_ok = True
            r.expect_ok = evaluate(step.expect, r.observation, self.ctx) if step.expect else None
        if not r.wait_ok:
            return self._wait_timed_out(step, r, attempt)
        if r.expect_ok is False:
            obs2 = r.observation or self._observe()
            sw2 = sweep(obs2, step, cap, profile, self.ctx, self.budgets)
            if not sw2.empty:
                action = self._dispose(sw2, step, obs2)
                if action in ("restart", "retry"):
                    return action if action == "restart" else self._run_one(step, attempt=attempt + 1)
            raise _Stop(self._hard_failure(FailureCode.CHECKPOINT_FAILED, f"expectation failed after {step.id}", step, obs2, expected=describe(step.expect, self.cfg.params) if step.expect else None))
        if step.extract is not None:
            self._extract(step, r)
        shot = self._shot(f"{step.id}_after")
        if before_shot:
            self.log.emit("warning", {"code": "WRITE_STEP", "step": step.id, "before": before_shot, "after": shot, "risk_class": step.risk_class}, step_id=step.id)
        self.steps.append(StepReport(step_id=step.id, status="ok", index_used=r.resolution.index_used if r.resolution else None, locator_kind=r.resolution.kind if r.resolution else None, duration_ms=int((time.monotonic() - t0) * 1000), screenshot=shot))
        self.log.emit("checkpoint", {"expect": describe(step.expect, self.cfg.params) if step.expect else None, "ok": True, "waited_ms": r.waited_ms}, step_id=step.id)
        return "next"

    # ------------------------------------------------------------------ dispositions

    def _dispose(self, sw: Sweep, step: Step, obs: Observation) -> str:
        """Act on a sweep hit. Returns 'continue' | 'retry' | 'restart' or raises _Stop."""
        assert self.log and self.cap
        self.log.emit("condition_detected", {"kind": sw.kind, "code": sw.code, "message": sw.message, "observed": observed_summary(obs)}, step_id=step.id)
        if sw.kind == "outcome":
            bo = self.cap.business_outcomes[sw.code or ""]
            returns = {k: self._subst(v) for k, v in bo.returns.items()}
            self._shot(f"{step.id}_outcome")
            raise _Stop(self._terminal(BusinessOutcomeResult, outcome=Outcome(code=sw.code or "", step_id=step.id, message=bo.description, returns=returns)))
        if sw.kind == "recoverable" and sw.condition is not None:
            return self._remedy(sw.condition, sw.source or "artifact", step, obs)
        if sw.kind == "unknown_dialog":
            info = self.surface.handle_dialog(False)  # type: ignore[union-attr]
            raise _Stop(self._hard_failure(FailureCode.UNEXPECTED_DIALOG, f"dialog {info.message if info else ''!r} matched no declared condition; dismissed (cancel)", step, self._observe()))
        code = FailureCode.APP_ERROR if sw.code == "app_error" else FailureCode.UNDECLARED_CONDITION
        raise _Stop(self._hard_failure(code, sw.message or f"undeclared condition {sw.code}", step, obs))

    def _remedy(self, cond: RecoverableCondition, source: str, step: Step, obs: Observation) -> str:
        assert self.surface and self.state and self.log and self.cap and self.profile
        self.budgets[cond.id] = self.budgets.get(cond.id, 0) + 1
        rem = cond.remedy
        code_map = {"click": RecoveryCode.INTERSTITIAL_DISMISSED, "dismiss_dialog": RecoveryCode.KNOWN_DIALOG_HANDLED, "wait_and_retry": RecoveryCode.SLOW_LOAD_WAITED, "run_subflow": RecoveryCode.SESSION_REESTABLISHED}
        if rem.action == "click" and rem.target is not None:
            res = self.surface.resolve(rem.target, self.cfg.params)
            if not res.ok:
                raise _Stop(self._hard_failure(FailureCode.TARGET_NOT_FOUND, f"remedy {cond.id}: target not found", step, obs, diagnostics=res.diagnostics))
            from teller.surface.base import Action

            self.surface.act(self.state.token(), Action(kind="click", frame=rem.target.frame), res)
            self._settle()
        elif rem.action == "dismiss_dialog":
            self.surface.handle_dialog(bool(rem.accept))
        elif rem.action == "wait_and_retry":
            for ms in rem.backoff_ms or [1000]:
                self._sleep(ms)
                o = self._observe()
                if not evaluate(cond.detect, o, self.ctx):
                    break
        elif rem.action == "run_subflow":
            if self.non_idempotent_executed:
                raise _Stop(self._hard_failure(FailureCode.REAUTH_UNSAFE, "session lost after a non-idempotent step executed; refusing to re-authenticate and restart", step, obs))
            if self.surface.pending_dialog():
                self.surface.handle_dialog(False)
            self._login()
            self.recoveries.append(Recovery(code=code_map[rem.action], condition_id=cond.id, step_id=step.id, times=self.budgets[cond.id]))
            self.log.emit("recovery", {"code": code_map[rem.action], "condition": cond.id, "source": source, "then": "restart_from_anchor"}, step_id=step.id)
            return "restart"
        self.recoveries.append(Recovery(code=code_map[rem.action], condition_id=cond.id, step_id=step.id, times=self.budgets[cond.id]))
        self.log.emit("recovery", {"code": code_map[rem.action], "condition": cond.id, "source": source}, step_id=step.id)
        return "retry"

    def _resolution_failed(self, step: Step, r: StepOutcome, attempt: int) -> str:
        assert self.cap and self.profile
        obs = self._observe()
        sw = sweep(obs, step, self.cap, self.profile, self.ctx, self.budgets)
        if not sw.empty:
            action = self._dispose(sw, step, obs)
            if action == "restart":
                return "restart"
            return self._run_one(step, attempt=attempt + 1)
        if step.expect and evaluate(step.expect, obs, self.ctx):
            # the control is gone because the step's effect is already on screen (e.g. a redirect
            # after a recovery landed on the target page): verified state beats re-acting
            self.steps.append(StepReport(step_id=step.id, status="ok", note="target absent but postcondition holds", screenshot=self._shot(f"{step.id}_after")))
            self.log.emit("checkpoint", {"expect": describe(step.expect, self.cfg.params), "ok": True, "resolved": False}, step_id=step.id)
            return "next"
        if attempt == 1 and r.resolution is not None and r.resolution.failure == "not_found":
            # one bounded re-observe: the page may still be rendering
            self._sleep(750)
            return self._run_one(step, attempt=attempt + 1)
        code = FailureCode.TARGET_AMBIGUOUS if r.resolution and r.resolution.failure == "ambiguous" else FailureCode.TARGET_NOT_FOUND
        raise _Stop(self._hard_failure(code, r.error or "target could not be resolved", step, obs, diagnostics=r.resolution.diagnostics if r.resolution else []))

    def _wait_timed_out(self, step: Step, r: StepOutcome, attempt: int) -> str:
        assert self.cap and self.profile
        obs = r.observation or self._observe()
        sw = sweep(obs, step, self.cap, self.profile, self.ctx, self.budgets)
        if not sw.empty:
            action = self._dispose(sw, step, obs)
            if action == "restart":
                return "restart"
            return self._run_one(step, attempt=attempt + 1)
        if step.expect and evaluate(step.expect, obs, self.ctx):
            return "next"  # slow but arrived
        changed = bool(r.act and (r.act.navigated or r.act.dialog_opened))
        if changed:
            # the app responded, just not with the expected state: a checkpoint failure, not a timeout
            raise _Stop(self._hard_failure(FailureCode.CHECKPOINT_FAILED, f"after {step.id} the page changed but the expected state never appeared", step, obs, expected=describe(step.expect, self.cfg.params) if step.expect else (f"wait_for {step.wait_for.model_dump(exclude_none=True)}" if step.wait_for else None)))
        if step.idempotent and attempt == 1:
            self.log.emit("warning", {"code": "REACT_IDEMPOTENT", "step": step.id}, step_id=step.id)
            return self._run_one(step, attempt=attempt + 1)
        raise _Stop(self._hard_failure(FailureCode.STEP_TIMEOUT, f"wait_for did not hold within {step.wait_for.timeout_ms if step.wait_for else 0} ms", step, obs, expected=f"wait_for {step.wait_for.model_dump(exclude_none=True) if step.wait_for else None}"))

    def _irreversible(self, step: Step, reason: str) -> str:
        assert self.log and self.cap
        self.log.emit("escalation", {"trigger": "IRREVERSIBLE_STEP", "reason": reason}, step_id=step.id)
        if self.cap.escalation_policy.on_irreversible_step == "refuse" or self.cfg.unattended:
            raise _Stop(self._terminal(DeclinedResult, declined=Declined(code=DeclinedCode.HUMAN_REQUIRED_UNATTENDED, step_id=step.id, note=f"{reason}; pass --confirm-step {step.id}@{self.cap.capability.version} or run attended")))
        mode = self._escalate("IRREVERSIBLE_STEP", reason, step, allowed=["confirm", "decline", "abort"])
        if mode == "confirm":
            self.cfg.confirm_steps.add(f"{step.id}@{self.cap.capability.version}")
            return self._run_one(step)
        raise _Stop(self._terminal(DeclinedResult, declined=Declined(code=DeclinedCode.CONFIRMATION_DECLINED, step_id=step.id, by=self.handoffs[-1].claimed_by if self.handoffs else None, note=reason)))

    def _locator_fallback(self, step: Step, r: StepOutcome) -> None:
        assert self.log and r.resolution
        idx = r.resolution.index_used or 0
        self.recoveries.append(Recovery(code=RecoveryCode.LOCATOR_FALLBACK_USED, step_id=step.id, note=f"index {idx} ({r.resolution.kind})"))
        path = self.run_dir / "suggested_overrides.yaml"
        reordered = [step.target.locators[idx]] + [loc for i, loc in enumerate(step.target.locators) if i != idx]  # type: ignore[union-attr]
        patch = {"overrides": {self.cap.capability.id: {"steps": {step.id: {"target": {"locators": [loc.model_dump(exclude_none=True) for loc in reordered]}}}}}}  # type: ignore[union-attr]
        path.write_text(yaml.safe_dump(patch, sort_keys=False), encoding="utf-8")
        self.warnings.append(Warning(code="DRIFT_WARNING", step_id=step.id, index_used=idx, message=f"primary locator failed; {r.resolution.kind} resolved", suggest_override=str(path)))
        self.log.emit("warning", {"code": "DRIFT_WARNING", "index_used": idx, "kind": r.resolution.kind, "diagnostics": r.resolution.diagnostics}, step_id=step.id)

    def _extract(self, step: Step, r: StepOutcome) -> None:
        assert self.cap and self.log and step.extract
        spec = self.cap.outputs[step.extract.output]
        raw = r.extracted or ""
        try:
            value = parse(raw, step.extract.parse or spec.parse, regex=spec.regex, pattern=spec.pattern)
        except ParseError as e:
            raise _Stop(self._hard_failure(FailureCode.OUTPUT_PARSE_FAILED, str(e), step, r.observation or self._observe(), expected=f"{spec.parse} parse", observed_raw=self.redactor.scrub_text(raw))) from e  # type: ignore[union-attr]
        self.outputs[step.extract.output] = value
        self.redactor.register_output(self.cap, step.extract.output, value)  # type: ignore[union-attr]
        self.log.emit("extract", {"output": step.extract.output, "value": self.redactor.masked_outputs(self.cap, {step.extract.output: value})[step.extract.output]}, step_id=step.id)  # type: ignore[union-attr]

    # ------------------------------------------------------------------ escalation

    def _escalate(self, trigger: str, reason: str, step: Step | None, *, allowed: list[str]) -> str | None:
        """Raise an intervention; hand the live session to a human if a controller exists."""
        assert self.state and self.log and self.cap and self.surface
        shot = self._shot(f"{step.id if step else 'run'}_escalation")
        req = InterventionRequest(
            intervention_id=InterventionRequest.new_id(), run_id=self.run_id, mode="replay",
            capability_id=self.cap.capability.id, step_id=step.id if step else None, intent=step.intent if step else None,
            trigger=trigger, reason=reason, url=self.surface.current_url(), screenshot=shot,
            last_events=self.log.tail(5), allowed_resolutions=allowed, session_endpoint=self.surface.session_endpoint(),
        )
        self.state.raise_intervention(req)
        partial = NeedsHumanResult(**self._common(), intervention_id=req.intervention_id, trigger=trigger, step_id=req.step_id)
        self._write_result(partial)
        if self.escalator is None:
            return None
        t0 = time.monotonic()
        mode = self.escalator(self, req)
        self.paused_ms += int((time.monotonic() - t0) * 1000)
        return mode

    def _hard_failure(self, code: FailureCode, message: str, step: Step | None, obs: Observation | None, *, expected: str | None = None, diagnostics: list[dict] | None = None, observed_raw: str | None = None) -> TerminalResult:
        assert self.cap
        sid = step.id if step else None
        shot = self._shot(f"{sid or 'run'}_fail")
        dom = None
        try:
            dom = self.surface.dom_snapshot(str(self.run_dir / f"{sid or 'run'}_fail.html"))  # type: ignore[union-attr]
        except Exception:  # noqa: BLE001
            pass
        observed = observed_summary(obs) if obs else None
        if observed is not None and observed_raw is not None:
            observed["raw"] = observed_raw
        failure = Failure(code=code, step_id=sid, message=message, expected=expected, observed=observed, screenshot=shot, dom_snapshot=dom, diagnostics=diagnostics or [])
        if step is not None:
            self.steps.append(StepReport(step_id=step.id, status="failed", note=message[:200], screenshot=shot))
        self.log.emit("error", {"code": code, "message": message, "expected": expected, "observed": observed}, step_id=sid)  # type: ignore[union-attr]
        if self.cap.escalation_policy.on_hard_failure == "pause" and not self.cfg.unattended and step is not None and self.state and self.state.handoffs < self.cap.escalation_policy.max_handoffs:
            mode = self._escalate(code, message, step, allowed=["retry_step", "skip_step", "complete", "abort"])
            if mode == "retry_step":
                raise _Retry(step)
            if mode == "skip_step":
                raise _Skip(step)
            if mode == "complete":
                raise _Complete()
            if mode == "abort":
                return self._terminal(DeclinedResult, declined=Declined(code=DeclinedCode.ABORTED_BY_HUMAN, step_id=sid, by=self.handoffs[-1].claimed_by if self.handoffs else None, note=message))
            if mode is None and self.escalator is not None:
                return self._terminal(FailureResult, failure=Failure(code=FailureCode.ESCALATION_TIMEOUT, step_id=sid, message=f"nobody claimed the intervention; original: {code}: {message}", screenshot=shot, dom_snapshot=dom, observed=observed))
        return self._terminal(FailureResult, failure=failure)

    def _failure(self, code: FailureCode, message: str, *, step_id: str | None = None) -> FailureResult:
        if self.log:
            self.log.emit("error", {"code": code, "message": message}, step_id=step_id)
        return self._terminal(FailureResult, failure=Failure(code=code, step_id=step_id, message=message))

    # ------------------------------------------------------------------ results

    def _success(self) -> SuccessResult:
        assert self.cap and self.redactor
        outputs = dict(self.outputs) if self.cfg.emit_full_outputs else self.redactor.masked_outputs(self.cap, self.outputs)
        self.full_outputs = dict(self.outputs)  # in-memory only; the file carries masked values
        return SuccessResult(**self._common(), outputs=outputs)

    def _terminal(self, cls: type, **kw: Any) -> Any:
        return cls(**self._common(), **kw)

    def _common(self) -> dict[str, Any]:
        cap = self.cap
        params = self.redactor.masked_params(cap, self.cfg.params) if (cap and self.redactor) else dict(self.cfg.params)
        return {
            "run_id": self.run_id, "mode": "replay", "tenant": self.cfg.tenant, "params": params,
            "capability": CapabilityRef(id=cap.capability.id, version=cap.capability.version, status=cap.capability.status) if cap else None,
            "recoveries": list(self.recoveries), "warnings": list(self.warnings), "steps": list(self.steps), "handoffs": list(self.handoffs),
            "evidence_dir": str(self.run_dir), "timing": Timing(started_at=self.started_at, finished_at=now_iso(), duration_ms=int((time.monotonic() - self.started) * 1000), paused_ms=self.paused_ms),
            "llm_invoked": False, "policy_sha256": self.policy.sha256() if self.policy else "", "artifact_path": str(self.cfg.artifact_path),
        }

    def _result_summary(self, r: Any) -> dict[str, Any]:
        if isinstance(r, SuccessResult):
            return {"outputs": r.outputs}
        if isinstance(r, BusinessOutcomeResult):
            return {"outcome": r.outcome.code, "step": r.outcome.step_id}
        if isinstance(r, FailureResult):
            return {"code": r.failure.code, "step": r.failure.step_id, "message": r.failure.message}
        if isinstance(r, DeclinedResult):
            return {"code": r.declined.code, "step": r.declined.step_id}
        return {}

    def _write_result(self, result: Any) -> None:
        (self.run_dir / "result.json").write_text(result.model_dump_json(indent=2, exclude_none=True), encoding="utf-8")

    # ------------------------------------------------------------------ helpers

    def _on_transition(self, t: Any) -> None:
        if self.log:
            self.log.set_controller(t.controller.value)
            self.log.emit("handoff.claimed" if t.to_state is RunState.HUMAN_IN_CONTROL else "handoff.released" if t.from_state is RunState.HUMAN_IN_CONTROL else "escalation", {"from": t.from_state, "to": t.to_state, "reason": t.reason}, actor=t.actor)

    def _observe(self) -> Observation:
        assert self.surface
        return self.surface.observe(badges=False)

    def _shot(self, name: str) -> str | None:
        try:
            p = self.run_dir / "screenshots" / f"{name}.jpg"
            self.surface.screenshot(str(p))  # type: ignore[union-attr]
            return str(p.relative_to(self.run_dir))
        except Exception:  # noqa: BLE001
            return None

    def _sleep(self, ms: int) -> None:
        page = getattr(self.surface, "page", None)
        if page is not None:
            page.wait_for_timeout(ms)
        else:  # pragma: no cover
            time.sleep(ms / 1000)

    def _settle(self, ms: int = 600) -> None:
        deadline = time.monotonic() + ms / 1000
        last = None
        while time.monotonic() < deadline:
            self._sleep(150)
            o = self._observe()
            key = (o.url, o.text_digest)
            if key == last:
                return
            last = key

    def _secrets_for(self, step: Step) -> dict[str, str]:
        if step.value and step.value.secret:
            val = os.environ.get(step.value.secret)
            return {step.value.secret: val} if val else {}
        return {}

    def _subst(self, s: str) -> str:
        out = s
        for k, v in self.cfg.params.items():
            out = out.replace("{" + k + "}", str(v))
        return out

    def _check_clock(self) -> None:
        limit = self.cfg.max_run_seconds or (self.policy.max_run_seconds if self.policy else 360)
        active = time.monotonic() - self.started - self.paused_ms / 1000
        if active > limit:
            raise _Stop(self._hard_failure(FailureCode.RUN_TIMEOUT, f"active wall clock exceeded {limit}s", None, None))


class _Retry(Exception):
    def __init__(self, step: Step):
        self.step = step


class _Skip(Exception):
    def __init__(self, step: Step):
        self.step = step


class _Complete(Exception):
    pass


# ----------------------------------------------------------------------------------------
# static checks (also exposed as `teller policy check`)
# ----------------------------------------------------------------------------------------


def static_policy_check(cap: Capability, policy: Policy, tenant: Tenant | None = None) -> list[str]:
    problems: list[str] = []
    base = tenant.base_url.rstrip("/") if tenant else ""
    for step in cap.steps:
        if not policy.action_allowed(step.action):
            problems.append(f"{step.id}: action {step.action!r} not allowed by policy")
        if step.action == "navigate" and step.url:
            url = step.url.replace("{base_url}", base)
            if url.startswith("http") and not policy.url_allowed(re.sub(r"\{[a-zA-Z_]+\}", "1", url)):
                problems.append(f"{step.id}: navigate to {step.url!r} denied by policy")
        if step.risk_class == "irreversible_write" and cap.escalation_policy.on_irreversible_step not in ("require_confirmation", "refuse"):
            problems.append(f"{step.id}: irreversible step without an escalation policy")
    entry = cap.capability.app.entry_url.replace("{base_url}", base) if base else None
    if entry and entry.startswith("http") and not policy.url_allowed(entry):
        problems.append(f"entry_url {cap.capability.app.entry_url!r} denied by policy")
    return problems


def version_in_range(version: str, spec: str) -> bool:
    """Tiny semver-ish range check: '*', '>=4.1 <5', '4.2', '>=4'."""
    spec = spec.strip()
    if spec in ("*", ""):
        return True

    def parts(v: str) -> tuple[int, ...]:
        return tuple(int(x) for x in re.findall(r"\d+", v)[:3]) or (0,)

    v = parts(version)
    for clause in spec.split():
        m = re.fullmatch(r"(>=|<=|>|<|=|==)?\s*([\d.]+)", clause)
        if not m:
            return True  # unknown syntax: do not block
        op, num = m.group(1) or "==", parts(m.group(2))
        vv = v + (0,) * (len(num) - len(v))
        ok = {">=": vv >= num, "<=": vv <= num, ">": vv > num, "<": vv[: len(num)] < num, "==": vv[: len(num)] == num, "=": vv[: len(num)] == num}[op]
        if not ok:
            return False
    return True


def load_predicates_for_probe(cap: Capability, profile: AppProfile) -> list[Predicate]:
    return list(profile.detectors.values()) + [c.detect for c in profile.recoverable_conditions] + [c.detect for c in cap.recoverable_conditions] + [b.detect for b in cap.business_outcomes.values()]
