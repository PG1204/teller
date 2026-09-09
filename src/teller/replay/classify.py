"""Classification — the one place the three-tier rule lives in code.

    The app said no to the DATA (a detector the artifact declares as a business outcome
    *at this step*)                                        -> BUSINESS_OUTCOME
    A declared, bounded remedy exists for what we see       -> RECOVERY (apply, re-sweep)
    A profile detector fired with no disposition here       -> FAILURE / UNDECLARED_CONDITION
    A dialog nobody declared                                -> FAILURE / UNEXPECTED_DIALOG
    otherwise                                               -> nothing to report

``sweep()`` is evaluated on every observation the replay engine takes: before acting, on every
250 ms poll of the wait-race after acting, and after a resolution failure. Classification is a
property of the artifact (``at_steps``), not of the engine.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from teller.artifact.model import AppProfile, Capability, RecoverableCondition, Step
from teller.replay.detectors import EvalContext, evaluate
from teller.surface.base import Observation


@dataclass
class Sweep:
    kind: Literal["outcome", "recoverable", "undeclared", "unknown_dialog", "none"]
    code: str | None = None  # outcome code or detector name
    condition: RecoverableCondition | None = None
    source: str | None = None  # "artifact" | "profile"
    message: str = ""

    @property
    def empty(self) -> bool:
        return self.kind == "none"


def sweep(
    obs: Observation,
    step: Step | None,
    cap: Capability,
    profile: AppProfile,
    ctx: EvalContext,
    budgets: dict[str, int],
) -> Sweep:
    """Classify the current observation for the step being executed (or None between steps)."""
    # 1. a parked dialog: a declared remedy may answer it; otherwise it is unexpected
    if obs.dialog is not None:
        for source, conds in (("artifact", cap.recoverable_conditions), ("profile", profile.recoverable_conditions)):
            for cond in conds:
                if cond.detect.dialog_text_matches and evaluate(cond.detect, obs, ctx):
                    if budgets.get(cond.id, 0) < cond.max_times:
                        return Sweep("recoverable", condition=cond, source=source, code=cond.id)
                    return Sweep("undeclared", code=cond.id, message=f"remedy {cond.id} exhausted (max_times={cond.max_times})")
        return Sweep("unknown_dialog", message=obs.dialog.message)
    # 2. business outcomes declared at this step
    if step is not None:
        for code, bo in cap.business_outcomes.items():
            if step.id in bo.at_steps and evaluate(bo.detect, obs, ctx):
                return Sweep("outcome", code=code, message=bo.description)
    # 3. recoverable conditions, artifact first then vendor profile
    for source, conds in (("artifact", cap.recoverable_conditions), ("profile", profile.recoverable_conditions)):
        for cond in conds:
            if cond.detect.dialog_text_matches:
                continue
            if evaluate(cond.detect, obs, ctx):
                if budgets.get(cond.id, 0) < cond.max_times:
                    return Sweep("recoverable", condition=cond, source=source, code=cond.id)
                return Sweep("undeclared", code=cond.id, message=f"remedy {cond.id} exhausted (max_times={cond.max_times})")
    # 4. vendor detectors with no disposition here
    for name, pred in profile.detectors.items():
        if name in ("login_wall",) and step is None:
            continue
        if evaluate(pred, obs, ctx):
            # a detector that is also the trigger of a recoverable we already budgeted is handled above
            if any(c.detect.detector == name for c in profile.recoverable_conditions):
                continue
            return Sweep("undeclared", code=name, message=f"detector {name!r} fired at {step.id if step else 'preflight'} with no declared disposition")
    return Sweep("none")
