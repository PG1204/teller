"""Result contract and error taxonomy.

The one-sentence classification rule (also in REPORT §3):

    The app said no to the DATA            -> BUSINESS_OUTCOME   (caller must branch)
    A declared, bounded remedy fixed it    -> RECOVERY           (reported, never terminal)
    A human stopped or declined the run    -> DECLINED           (terminal, not a bug)
    Anything else that stops the run       -> FAILURE            (debuggable: step, expected, observed)

``needs_human`` is a *run state*, not a terminal result: while paused the engine writes a partial
``result.json`` with ``status: needs_human`` so an operator (or a watching agent) can see it, and
the terminal result replaces it when the run finishes.

Exit codes let an agent harness branch without parsing: 0 success, 10 business outcome,
20 failure, 30 declined (a partial needs_human file is never the exit of a finished process).
"""

from __future__ import annotations

from enum import StrEnum
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field


class Status(StrEnum):
    SUCCESS = "success"
    BUSINESS_OUTCOME = "business_outcome"
    FAILURE = "failure"
    DECLINED = "declined"
    NEEDS_HUMAN = "needs_human"  # intermediate only


EXIT_CODES: dict[Status, int] = {
    Status.SUCCESS: 0,
    Status.BUSINESS_OUTCOME: 10,
    Status.FAILURE: 20,
    Status.DECLINED: 30,
    Status.NEEDS_HUMAN: 40,  # only if a process is killed while paused
}


class RecoveryCode(StrEnum):
    """Reported in ``recoveries[]``; never terminal on their own."""

    INTERSTITIAL_DISMISSED = "INTERSTITIAL_DISMISSED"  # declared interstitial clicked away
    KNOWN_DIALOG_HANDLED = "KNOWN_DIALOG_HANDLED"  # declared dialog accepted/dismissed
    SLOW_LOAD_WAITED = "SLOW_LOAD_WAITED"  # waited through a declared loading state
    SESSION_REESTABLISHED = "SESSION_REESTABLISHED"  # login subflow re-run, restarted from anchor
    LOCATOR_FALLBACK_USED = "LOCATOR_FALLBACK_USED"  # index_used > 0; also a DRIFT_WARNING


class FailureCode(StrEnum):
    """Each failure carries step_id, expected, observed, screenshot, dom_snapshot, diagnostics."""

    TARGET_NOT_FOUND = "TARGET_NOT_FOUND"  # every locator exhausted; diagnostics attached
    TARGET_AMBIGUOUS = "TARGET_AMBIGUOUS"  # >1 visible match, fingerprint cannot separate
    CHECKPOINT_FAILED = "CHECKPOINT_FAILED"  # action ran, step.expect / final checkpoint false
    UNDECLARED_CONDITION = "UNDECLARED_CONDITION"  # a detector fired where not declared
    UNEXPECTED_DIALOG = "UNEXPECTED_DIALOG"  # dialog text matched nothing; dismissed
    APP_ERROR = "APP_ERROR"  # 5xx / error page detector
    STEP_TIMEOUT = "STEP_TIMEOUT"  # wait_for exhausted, remedies exhausted
    RECOVERY_LIMIT_EXCEEDED = "RECOVERY_LIMIT_EXCEEDED"  # a remedy's max_times exhausted
    REAUTH_UNSAFE = "REAUTH_UNSAFE"  # session lost after a non-idempotent step
    OUTPUT_PARSE_FAILED = "OUTPUT_PARSE_FAILED"  # extracted text failed the declared parser
    POLICY_VIOLATION = "POLICY_VIOLATION"  # step would leave the allowlist; stopped before acting
    PARAM_INVALID = "PARAM_INVALID"  # preflight: params failed the artifact's contract
    ARTIFACT_INVALID = "ARTIFACT_INVALID"  # preflight: artifact/profile/tenant failed to load
    NOT_APPROVED = "NOT_APPROVED"  # preflight: --unattended on a draft artifact
    SURFACE_CRASHED = "SURFACE_CRASHED"  # browser/page closed unexpectedly
    ESCALATION_TIMEOUT = "ESCALATION_TIMEOUT"  # needs_human with nobody claiming in time
    HANDOFF_LIMIT_EXCEEDED = "HANDOFF_LIMIT_EXCEEDED"  # max_handoffs reached
    HANDBACK_STATE_MISMATCH = "HANDBACK_STATE_MISMATCH"  # human said done, verification failed
    RUN_TIMEOUT = "RUN_TIMEOUT"  # wall clock exhausted
    INTERNAL_ERROR = "INTERNAL_ERROR"  # engine exception; traceback path in evidence


class DeclinedCode(StrEnum):
    """A human made a decision that ended the run. Not a defect, not a business outcome."""

    ABORTED_BY_HUMAN = "ABORTED_BY_HUMAN"
    CONFIRMATION_DECLINED = "CONFIRMATION_DECLINED"  # irreversible step refused by the operator


class Base(BaseModel):
    model_config = ConfigDict(extra="forbid")


class CapabilityRef(Base):
    id: str
    version: str
    status: str


class StepReport(Base):
    step_id: str
    status: Literal["ok", "failed", "skipped", "outcome", "human"]
    index_used: int | None = None
    locator_kind: str | None = None
    duration_ms: int = 0
    screenshot: str | None = None
    note: str | None = None


class Recovery(Base):
    code: RecoveryCode
    condition_id: str | None = None
    step_id: str | None = None
    times: int = 1
    note: str | None = None


class Warning(Base):
    code: Literal["DRIFT_WARNING", "DRIFT_SUSPECTED", "VERSION_RANGE_MISMATCH", "ATTENDED_FORCED"]
    step_id: str | None = None
    index_used: int | None = None
    message: str = ""
    suggest_override: str | None = None


class Handoff(Base):
    intervention_id: str
    trigger: str
    reason: str = ""
    step_id: str | None = None
    claimed_by: str | None = None
    claimed_at: str | None = None
    released_at: str | None = None
    resume_mode: str | None = None
    note: str | None = None
    action_count: int = 0
    human_actions_path: str | None = None


class Outcome(Base):
    code: str = Field(description="Declared in the artifact's business_outcomes.")
    step_id: str
    message: str = ""
    returns: dict[str, str] = Field(default_factory=dict)


class Failure(Base):
    code: FailureCode
    step_id: str | None = None
    message: str = ""
    expected: str | None = None
    observed: dict | None = None
    screenshot: str | None = None
    dom_snapshot: str | None = None
    diagnostics: list[dict] = Field(default_factory=list)


class Declined(Base):
    code: DeclinedCode
    step_id: str | None = None
    by: str | None = None
    note: str | None = None


class Timing(Base):
    started_at: str
    finished_at: str | None = None
    duration_ms: int = 0
    paused_ms: int = Field(default=0, description="Wall clock spent waiting on a human.")


class _Common(Base):
    schema_version: Literal[1] = 1
    run_id: str
    mode: Literal["replay", "discovery"]
    capability: CapabilityRef | None = None
    goal: str | None = None
    tenant: str
    params: dict[str, str] = Field(default_factory=dict, description="Masked by classification.")
    recoveries: list[Recovery] = Field(default_factory=list)
    warnings: list[Warning] = Field(default_factory=list)
    steps: list[StepReport] = Field(default_factory=list)
    handoffs: list[Handoff] = Field(default_factory=list)
    evidence_dir: str
    timing: Timing
    llm_invoked: bool
    policy_sha256: str
    artifact_path: str | None = None


class SuccessResult(_Common):
    status: Literal["success"] = "success"
    outputs: dict[str, str]


class BusinessOutcomeResult(_Common):
    status: Literal["business_outcome"] = "business_outcome"
    outcome: Outcome


class FailureResult(_Common):
    status: Literal["failure"] = "failure"
    failure: Failure


class DeclinedResult(_Common):
    status: Literal["declined"] = "declined"
    declined: Declined


class NeedsHumanResult(_Common):
    """Partial result written while paused; replaced by a terminal result on finish."""

    status: Literal["needs_human"] = "needs_human"
    intervention_id: str
    trigger: str
    step_id: str | None = None


ReplayResult = Annotated[
    SuccessResult | BusinessOutcomeResult | FailureResult | DeclinedResult | NeedsHumanResult,
    Field(discriminator="status"),
]


class ResultEnvelope(Base):
    """Only used to export a single JSON Schema for ``result.json``."""

    result: ReplayResult


def exit_code(result: SuccessResult | BusinessOutcomeResult | FailureResult | DeclinedResult | NeedsHumanResult) -> int:
    return EXIT_CODES[Status(result.status)]
