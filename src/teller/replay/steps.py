"""Executing one artifact ``Step`` against a ``Surface``: resolve → gate/act → wait → expect.

Shared by the harness login subflow, the replay interpreter and the discovery loop's
recorder verification. It knows nothing about classification or escalation — it reports what
happened and the interpreter decides what it means.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any

from teller.artifact.model import Predicate, RiskClass, Step, WaitFor
from teller.hitl.state import ControlToken
from teller.replay.detectors import EvalContext, evaluate, subst
from teller.surface.base import Action, ActResult, Observation, Resolution, Surface


@dataclass
class StepOutcome:
    step_id: str
    resolution: Resolution | None
    act: ActResult | None
    waited_ms: int = 0
    wait_ok: bool = True
    expect_ok: bool | None = None
    observation: Observation | None = None
    extracted: str | None = None
    error: str | None = None
    detail: dict[str, Any] = field(default_factory=dict)

    @property
    def ok(self) -> bool:
        return (
            self.error is None
            and (self.resolution is None or self.resolution.ok)
            and (self.act is None or self.act.ok)
            and self.wait_ok
            and (self.expect_ok is not False)
        )


def build_action(step: Step, params: dict[str, str], secrets: dict[str, str]) -> Action:
    value = step.value.render(params, secrets) if step.value else None
    return Action(
        kind=step.action,
        value=value,
        option=subst(step.option, params) if step.option else None,
        key=step.key,
        url=subst(step.url, params) if step.url else None,
        accept=step.accept,
        clear_first=step.clear_first,
        frame=step.target.frame if step.target else None,
    )


def wait_for(
    surface: Surface,
    wait: WaitFor,
    params: dict[str, str],
    ctx: EvalContext,
    *,
    url_before: str,
    poll_ms: int = 250,
    on_poll: Any = None,
) -> tuple[bool, int, Observation | None]:
    """Poll until the wait predicate holds or the deadline passes.

    ``on_poll(obs)`` may return True to abort the wait early (the interpreter uses it to
    classify business outcomes / detectors the moment they appear — the "wait-race").
    """
    if wait.state == "none":
        return True, 0, None
    t0 = time.monotonic()
    deadline = t0 + wait.timeout_ms / 1000
    while True:
        obs = surface.observe(badges=False)
        if on_poll is not None and on_poll(obs):
            return False, int((time.monotonic() - t0) * 1000), obs
        if wait.state == "navigation":
            ok = obs.url != url_before and obs.dialog is None
        elif wait.state == "url":
            ok = evaluate(Predicate(url_matches=wait.url_matches), obs, ctx)
        elif wait.state == "text":
            ok = evaluate(Predicate(text_contains=wait.text, frame=wait.frame), obs, ctx)
        elif wait.state == "selector":
            ok = evaluate(Predicate(css_exists=wait.selector, frame=wait.frame), obs, ctx)
        else:  # pragma: no cover
            ok = True
        if ok:
            return True, int((time.monotonic() - t0) * 1000), obs
        if time.monotonic() >= deadline:
            return False, int((time.monotonic() - t0) * 1000), obs
        _sleep(surface, poll_ms)


def _sleep(surface: Surface, ms: int) -> None:
    page = getattr(surface, "page", None)
    if page is not None:
        page.wait_for_timeout(ms)  # keeps Playwright's event loop pumping
    else:  # pragma: no cover
        time.sleep(ms / 1000)


def run_step(
    surface: Surface,
    token: ControlToken,
    step: Step,
    params: dict[str, str],
    secrets: dict[str, str],
    ctx: EvalContext,
    *,
    declared_risk: RiskClass | None = None,
    confirmed: bool = False,
    on_poll: Any = None,
) -> StepOutcome:
    out = StepOutcome(step_id=step.id, resolution=None, act=None)
    resolution: Resolution | None = None
    if step.target is not None:
        resolution = surface.resolve(step.target, params)
        out.resolution = resolution
        if not resolution.ok:
            out.error = f"target could not be resolved ({resolution.failure})"
            return out
    action = build_action(step, params, secrets)
    url_before = surface.current_url()
    try:
        out.act = surface.act(
            token,
            action,
            resolution,
            declared_risk=declared_risk or step.risk_class,
            confirmed=confirmed,
        )
    except Exception as e:  # PolicyDenied / ConfirmationRequired / ControlViolation propagate
        out.error = str(e)
        raise
    if not out.act.ok:
        out.error = out.act.note or "action failed"
        return out
    if step.action == "type" and resolution is not None and resolution.handle is not None:
        if step.value is not None and step.value.secret is not None:
            actual = "<secret>"  # never keep credential values in run context
        else:
            try:
                actual = resolution.handle.input_value(timeout=1000)
            except Exception:  # noqa: BLE001 - surface-specific failure; fall back to intent
                actual = action.value or ""
        frame_key = (step.target.frame if step.target else None) or ""
        ctx.field_values[frame_key] = actual
        ctx.field_values["*"] = actual
    if step.action == "read":
        out.extracted = out.act.text
    if step.wait_for is not None:
        ok, waited, obs = wait_for(
            surface, step.wait_for, params, ctx, url_before=url_before, on_poll=on_poll
        )
        out.wait_ok, out.waited_ms, out.observation = ok, waited, obs
        if not ok:
            return out
    if step.expect is not None:
        obs = out.observation or surface.observe(badges=False)
        out.observation = obs
        out.expect_ok = evaluate(step.expect, obs, ctx)
    return out
