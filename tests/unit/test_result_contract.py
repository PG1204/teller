"""Unit tests for the result contract and error taxonomy."""

from __future__ import annotations

import re
from typing import Any

import pytest
from pydantic import TypeAdapter, ValidationError

from teller.replay.result import (
    EXIT_CODES,
    BusinessOutcomeResult,
    DeclinedCode,
    DeclinedResult,
    FailureCode,
    FailureResult,
    NeedsHumanResult,
    RecoveryCode,
    ReplayResult,
    ResultEnvelope,
    Status,
    SuccessResult,
    exit_code,
)

RESULT_ADAPTER: TypeAdapter[Any] = TypeAdapter(ReplayResult)


def _common() -> dict[str, Any]:
    return {
        "run_id": "run_2026-09-08T00-00-00_abcd",
        "mode": "replay",
        "tenant": "local",
        "evidence_dir": "runs/run_x",
        "timing": {"started_at": "2026-09-08T00:00:00+00:00"},
        "llm_invoked": False,
        "policy_sha256": "0" * 64,
    }


VARIANTS: dict[str, tuple[type, dict[str, Any]]] = {
    "success": (SuccessResult, {"outputs": {"savings_balance": "12.34"}}),
    "business_outcome": (
        BusinessOutcomeResult,
        {"outcome": {"code": "MEMBER_NOT_FOUND", "step_id": "s3"}},
    ),
    "failure": (FailureResult, {"failure": {"code": "TARGET_NOT_FOUND", "step_id": "s1"}}),
    "declined": (DeclinedResult, {"declined": {"code": "ABORTED_BY_HUMAN"}}),
    "needs_human": (NeedsHumanResult, {"intervention_id": "iv_1", "trigger": "UNEXPECTED_DIALOG"}),
}


@pytest.mark.parametrize("status", list(VARIANTS))
def test_each_variant_validates_with_minimal_fields(status: str) -> None:
    model, extra = VARIANTS[status]
    result = model.model_validate({**_common(), **extra})
    assert result.status == status
    assert result.schema_version == 1
    assert result.steps == [] and result.recoveries == []


@pytest.mark.parametrize("status", list(VARIANTS))
def test_union_routes_by_status(status: str) -> None:
    model, extra = VARIANTS[status]
    routed = RESULT_ADAPTER.validate_python({**_common(), **extra, "status": status})
    assert type(routed) is model


def test_union_rejects_unknown_status() -> None:
    with pytest.raises(ValidationError, match="status"):
        RESULT_ADAPTER.validate_python({**_common(), "status": "meh"})


def test_envelope_wraps_a_result() -> None:
    _, extra = VARIANTS["success"]
    env = ResultEnvelope.model_validate({"result": {**_common(), **extra, "status": "success"}})
    assert isinstance(env.result, SuccessResult)


def test_union_requires_explicit_status_tag() -> None:
    # A discriminated union cannot fall back to the variant's default; result.json always
    # carries ``status`` explicitly.
    _, extra = VARIANTS["success"]
    with pytest.raises(ValidationError, match="discriminator 'status'"):
        RESULT_ADAPTER.validate_python({**_common(), **extra})


@pytest.mark.parametrize(
    ("status", "code"),
    [("success", 0), ("business_outcome", 10), ("failure", 20), ("declined", 30)],
)
def test_exit_code_mapping(status: str, code: int) -> None:
    model, extra = VARIANTS[status]
    assert exit_code(model.model_validate({**_common(), **extra})) == code


def test_exit_codes_cover_every_status_and_are_distinct() -> None:
    assert set(EXIT_CODES) == set(Status)
    assert len(set(EXIT_CODES.values())) == len(EXIT_CODES)


def test_needs_human_requires_intervention_id() -> None:
    with pytest.raises(ValidationError, match="intervention_id"):
        NeedsHumanResult.model_validate({**_common(), "trigger": "UNEXPECTED_DIALOG"})


def test_unknown_extra_field_rejected() -> None:
    _, extra = VARIANTS["success"]
    with pytest.raises(ValidationError, match="Extra inputs are not permitted"):
        SuccessResult.model_validate({**_common(), **extra, "bogus": 1})


def test_failure_code_must_be_from_taxonomy() -> None:
    with pytest.raises(ValidationError):
        FailureResult.model_validate({**_common(), "failure": {"code": "NOPE"}})


UPPER_SNAKE = re.compile(r"^[A-Z][A-Z0-9_]*$")


@pytest.mark.parametrize("enum", [FailureCode, RecoveryCode, DeclinedCode])
def test_code_enum_values_are_upper_snake_and_match_names(enum: type) -> None:
    for member in enum:
        assert UPPER_SNAKE.fullmatch(member.value), member
        assert member.name == member.value, member


def test_codes_unique_across_the_three_enums() -> None:
    values = [m.value for e in (FailureCode, RecoveryCode, DeclinedCode) for m in e]
    assert len(values) == len(set(values))
