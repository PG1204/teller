"""Unit tests for the capability artifact pydantic models."""

from __future__ import annotations

import datetime as dt
import re
from collections.abc import Callable
from typing import Any

import pytest
import yaml
from pydantic import TypeAdapter, ValidationError

from teller.artifact.model import (
    LOCATOR_KINDS,
    AttrStableLocator,
    Capability,
    Locator,
    Param,
    Predicate,
    Provenance,
    RoleNameLocator,
    Step,
    Target,
    ValueRef,
    WaitFor,
)

Mutation = Callable[[dict[str, Any]], None]

LOCATOR_ADAPTER: TypeAdapter[Any] = TypeAdapter(Locator)

CLICK_TARGET: dict[str, Any] = {"locators": [{"kind": "text_exact", "text": "Go"}]}


def _validate_mutated(raw: dict[str, Any], mutate: Mutation) -> Capability:
    mutate(raw)
    return Capability.model_validate(raw)


def _assert_rejected(raw: dict[str, Any], mutate: Mutation, match: str) -> None:
    with pytest.raises(ValidationError, match=match):
        _validate_mutated(raw, mutate)


# --------------------------------------------------------------------------------------
# sample fixture
# --------------------------------------------------------------------------------------


def test_sample_fixture_validates(sample_raw: dict[str, Any]) -> None:
    cap = Capability.model_validate(sample_raw)
    assert cap.capability.id == "ledgerline.member.read_savings_balance"
    assert cap.schema_version == 1


def test_sample_step_ids_in_order(sample_raw: dict[str, Any]) -> None:
    cap = Capability.model_validate(sample_raw)
    assert [s.id for s in cap.steps] == ["s1", "s2", "s3", "s4", "s5", "s6"]


def test_sample_params_and_outputs(sample_raw: dict[str, Any]) -> None:
    cap = Capability.model_validate(sample_raw)
    assert set(cap.params) == {"member_id"}
    assert cap.params["member_id"].classification == "pii_low"
    assert set(cap.outputs) == {"savings_balance", "savings_account_number"}
    assert cap.outputs["savings_balance"].parse == "currency_usd"
    assert cap.outputs["savings_account_number"].source_step == "s6"


def test_sample_param_refs_and_risk_class(sample_raw: dict[str, Any]) -> None:
    cap = Capability.model_validate(sample_raw)
    assert cap.param_refs() == {"member_id"}
    assert cap.capability.risk_class == "read"


def test_step_lookup_helpers(sample_raw: dict[str, Any]) -> None:
    cap = Capability.model_validate(sample_raw)
    assert cap.step("s3").action == "click"
    assert cap.step_index("s3") == 2
    with pytest.raises(KeyError):
        cap.step("nope")


def test_content_hash_ignores_review_and_overrides(sample_raw: dict[str, Any]) -> None:
    base = Capability.model_validate(sample_raw)
    reviewed = base.model_copy(deep=True)
    reviewed.capability.review.notes = "looks fine"
    reviewed.overrides = {"steps": {"s1": {"idempotent": False}}}
    assert reviewed.content_hash() == base.content_hash()


def test_content_hash_changes_with_content(sample_raw: dict[str, Any]) -> None:
    base = Capability.model_validate(sample_raw)
    edited = base.model_copy(deep=True)
    edited.steps[0].intent = "something else"
    assert edited.content_hash() != base.content_hash()


# --------------------------------------------------------------------------------------
# ValueRef
# --------------------------------------------------------------------------------------


def test_value_ref_rejects_no_fields() -> None:
    with pytest.raises(ValidationError, match="exactly one"):
        ValueRef()


def test_value_ref_rejects_two_fields() -> None:
    with pytest.raises(ValidationError, match="exactly one"):
        ValueRef(param="a", literal="b")


def test_value_ref_render_param_and_secret() -> None:
    assert ValueRef(param="member_id").render({"member_id": "10001"}) == "10001"
    assert ValueRef(secret="PASS").render({}, {"PASS": "hunter2"}) == "hunter2"
    assert ValueRef(literal="x").render({}) == "x"


def test_value_ref_render_missing_param_raises_key_error() -> None:
    with pytest.raises(KeyError, match="member_id"):
        ValueRef(param="member_id").render({})


def test_value_ref_render_missing_secret_raises_key_error() -> None:
    with pytest.raises(KeyError, match="PASS"):
        ValueRef(secret="PASS").render({}, None)


# --------------------------------------------------------------------------------------
# Predicate / WaitFor
# --------------------------------------------------------------------------------------


def test_predicate_requires_exactly_one_leaf() -> None:
    with pytest.raises(ValidationError, match="exactly one condition"):
        Predicate()
    with pytest.raises(ValidationError, match="exactly one condition"):
        Predicate(text_contains="a", http_status=200)


def test_predicate_rejects_invalid_regex() -> None:
    with pytest.raises((ValidationError, re.error)):
        Predicate(text_matches="(unclosed")


def test_invalid_regex_surfaces_as_validation_error() -> None:
    with pytest.raises(ValidationError):
        Predicate(text_matches="(unclosed")
    with pytest.raises(ValidationError):
        Param(pattern="(unclosed")


def test_predicate_describe_leaf_with_frame() -> None:
    assert Predicate(text_contains="Go", frame="main").describe() == (
        "text_contains='Go' in frame 'main'"
    )


def test_predicate_describe_value_ref_leaf() -> None:
    assert Predicate(value_equals=ValueRef(param="m")).describe() == "value_equals={'param': 'm'}"


def test_predicate_describe_composites() -> None:
    p = Predicate(all=[Predicate(http_status=403), Predicate(text_contains="x")])
    assert p.describe() == "all(http_status=403, text_contains='x')"
    q = Predicate(any_of=[Predicate(css_exists="b")])
    assert q.describe() == "any_of(css_exists='b')"


def test_wait_for_text_requires_text() -> None:
    with pytest.raises(ValidationError, match="requires 'text'"):
        WaitFor(state="text")


def test_wait_for_url_requires_url_matches() -> None:
    with pytest.raises(ValidationError, match="requires 'url_matches'"):
        WaitFor(state="url")


def test_wait_for_navigation_needs_no_extra_field() -> None:
    assert WaitFor(state="navigation").timeout_ms == 5000


# --------------------------------------------------------------------------------------
# Step validators
# --------------------------------------------------------------------------------------


def test_step_type_requires_value() -> None:
    with pytest.raises(ValidationError, match="type requires value"):
        Step(id="s", action="type", intent="i", target=Target.model_validate(CLICK_TARGET))


def test_step_read_requires_extract() -> None:
    with pytest.raises(ValidationError, match="read requires extract"):
        Step(id="s", action="read", intent="i", target=Target.model_validate(CLICK_TARGET))


def test_step_irreversible_cannot_be_idempotent() -> None:
    with pytest.raises(ValidationError, match="cannot be idempotent"):
        Step(
            id="s",
            action="click",
            intent="i",
            idempotent=True,
            risk_class="irreversible_write",
            target=Target.model_validate(CLICK_TARGET),
        )


def test_step_click_requires_target() -> None:
    with pytest.raises(ValidationError, match="requires target"):
        Step(id="s", action="click", intent="i")


def test_step_press_requires_key_and_navigate_requires_url() -> None:
    with pytest.raises(ValidationError, match="press requires key"):
        Step(id="s", action="press", intent="i")
    with pytest.raises(ValidationError, match="navigate requires url"):
        Step(id="s", action="navigate", intent="i")


def test_step_id_pattern() -> None:
    with pytest.raises(ValidationError):
        Step(id="S1", action="press", intent="i", key="Enter")


# --------------------------------------------------------------------------------------
# Capability cross-reference validation (fixture mutated)
# --------------------------------------------------------------------------------------


def _set_on_outcome(step_index: int, code: str) -> Mutation:
    def mutate(raw: dict[str, Any]) -> None:
        raw["steps"][step_index]["on_outcome"] = [code]

    return mutate


def _mut_extract_unknown(raw: dict[str, Any]) -> None:
    raw["steps"][4]["extract"]["output"] = "nope"


def _mut_restart_anchor(raw: dict[str, Any]) -> None:
    raw["restart_anchor"] = "s99"


def _mut_risk_mismatch(raw: dict[str, Any]) -> None:
    raw["steps"][0]["idempotent"] = False
    raw["steps"][0]["risk_class"] = "irreversible_write"


def _mut_approved_without_review(raw: dict[str, Any]) -> None:
    raw["capability"]["status"] = "approved"


def _mut_duplicate_ids(raw: dict[str, Any]) -> None:
    raw["steps"][1]["id"] = "s1"


def _mut_unknown_placeholder(raw: dict[str, Any]) -> None:
    raw["steps"][0]["expect"]["text_contains"] = "Hello {unknown}"


def _mut_pii_high_example(raw: dict[str, Any]) -> None:
    raw["params"]["member_id"]["classification"] = "pii_high"


def _mut_unknown_param_ref(raw: dict[str, Any]) -> None:
    raw["steps"][1]["value"] = {"param": "ghost"}


def _mut_outcome_at_unknown_step(raw: dict[str, Any]) -> None:
    raw["business_outcomes"]["MEMBER_NOT_FOUND"]["at_steps"] = ["s42"]


def _mut_lower_outcome_code(raw: dict[str, Any]) -> None:
    raw["business_outcomes"]["lower"] = raw["business_outcomes"].pop("NO_SAVINGS_ACCOUNT")
    raw["steps"][4]["on_outcome"] = []


def _mut_output_source_step_unknown(raw: dict[str, Any]) -> None:
    raw["outputs"]["savings_balance"]["source_step"] = "s77"


@pytest.mark.parametrize(
    ("mutate", "match"),
    [
        pytest.param(_set_on_outcome(0, "NOT_A_THING"), "not declared", id="undeclared-outcome"),
        pytest.param(
            _set_on_outcome(0, "MEMBER_NOT_FOUND"), "not declared at this step", id="wrong-step"
        ),
        pytest.param(_mut_extract_unknown, "extract.output 'nope' unknown", id="extract-output"),
        pytest.param(_mut_restart_anchor, "restart_anchor 's99' is not a step", id="anchor"),
        pytest.param(_mut_risk_mismatch, "risk_class must equal the max", id="risk-mismatch"),
        pytest.param(_mut_approved_without_review, "status=approved requires", id="approved"),
        pytest.param(_mut_duplicate_ids, "duplicate step ids", id="duplicate-ids"),
        pytest.param(_mut_unknown_placeholder, r"placeholder \{unknown\}", id="placeholder"),
        pytest.param(_mut_pii_high_example, "example is only stored", id="pii-high-example"),
        pytest.param(_mut_unknown_param_ref, "unknown param 'ghost'", id="value-param"),
        pytest.param(_mut_outcome_at_unknown_step, "at_steps 's42' is not a step", id="at-steps"),
        pytest.param(_mut_lower_outcome_code, "must be UPPER_SNAKE", id="outcome-code"),
        pytest.param(_mut_output_source_step_unknown, "source_step 's77'", id="source-step"),
    ],
)
def test_capability_cross_ref_rejections(
    sample_raw: dict[str, Any], mutate: Mutation, match: str
) -> None:
    _assert_rejected(sample_raw, mutate, match)


def test_capability_rejects_unknown_top_level_key(sample_raw: dict[str, Any]) -> None:
    def mutate(raw: dict[str, Any]) -> None:
        raw["stepz"] = []

    _assert_rejected(sample_raw, mutate, "Extra inputs are not permitted")


def test_capability_base_url_placeholder_is_not_a_param(sample_raw: dict[str, Any]) -> None:
    def mutate(raw: dict[str, Any]) -> None:
        raw["steps"][0]["expect"]["text_contains"] = "{base_url}"

    cap = _validate_mutated(sample_raw, mutate)
    assert "base_url" in cap.param_refs()


def test_param_pii_high_with_example_rejected_directly() -> None:
    with pytest.raises(ValidationError, match="example is only stored"):
        Param(classification="pii_high", example="123-45-6789")
    assert Param(classification="pii_high").example is None


# --------------------------------------------------------------------------------------
# Locator discriminated union
# --------------------------------------------------------------------------------------

MINIMAL_LOCATORS: dict[str, dict[str, Any]] = {
    "role_name": {"role": "button", "name": "Go"},
    "label_anchor": {"label": "Member #"},
    "text_exact": {"text": "Members"},
    "attr_stable": {"attr": "name", "value": "txtF1"},
    "table_cell": {
        "table_anchor": "Accounts",
        "row_match": {"column": "Product", "equals": "Share Savings"},
        "column": "Current Balance",
    },
    "xpath_anchored": {"anchor_text": "Member #", "xpath": "ancestor::tr[1]"},
    "coords_verified": {"x": 10, "y": 20},
    "ax_path": {"ax_path": "window/pane/button"},
}


def test_minimal_locator_table_covers_every_kind() -> None:
    assert set(MINIMAL_LOCATORS) == set(LOCATOR_KINDS)
    assert len(LOCATOR_KINDS) == 8


@pytest.mark.parametrize("kind", LOCATOR_KINDS)
def test_each_locator_kind_parses_from_dict(kind: str) -> None:
    loc = LOCATOR_ADAPTER.validate_python({"kind": kind, **MINIMAL_LOCATORS[kind]})
    assert loc.kind == kind


def test_unknown_locator_kind_rejected() -> None:
    with pytest.raises(ValidationError, match="kind"):
        LOCATOR_ADAPTER.validate_python({"kind": "css", "selector": "#x"})


def test_locator_default_surfaces() -> None:
    assert "desktop" in RoleNameLocator(role="button", name="Go").surfaces
    assert "desktop" not in AttrStableLocator(attr="name", value="x").surfaces


def test_target_requires_at_least_one_locator() -> None:
    with pytest.raises(ValidationError):
        Target(locators=[])


# --------------------------------------------------------------------------------------
# Timestamps
# --------------------------------------------------------------------------------------


def test_bare_yaml_timestamp_is_stored_as_string() -> None:
    raw = yaml.safe_load("discovered_by: human\nrecorded_at: 2026-09-08T00:00:00Z\n")
    assert isinstance(raw["recorded_at"], dt.datetime)  # PyYAML parsed it as a datetime
    prov = Provenance.model_validate(raw)
    assert isinstance(prov.recorded_at, str)
    assert prov.recorded_at.startswith("2026-09-08T00:00:00")


def test_fixture_recorded_at_is_string(sample_raw: dict[str, Any]) -> None:
    cap = Capability.model_validate(sample_raw)
    assert isinstance(cap.capability.provenance.recorded_at, str)
