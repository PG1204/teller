"""Unit tests for loading, merging, saving and approving capability artifacts."""

from __future__ import annotations

import copy
from pathlib import Path
from typing import Any

import pytest
import yaml

from teller.artifact.model import Capability
from teller.artifact.store import (
    ArtifactError,
    ArtifactStore,
    apply_overrides,
    approve,
    load_capability,
    save_capability,
    to_yaml,
)

CAP_ID = "ledgerline.member.read_savings_balance"


def _locator_names(cap: Capability, step_id: str) -> list[str]:
    names: list[str] = []
    for loc in cap.step(step_id).target.locators:  # type: ignore[union-attr]
        names.append(getattr(loc, "name", None) or getattr(loc, "text", ""))
    return names


# --------------------------------------------------------------------------------------
# apply_overrides
# --------------------------------------------------------------------------------------


def test_apply_overrides_empty_patch_returns_input(sample_raw: dict[str, Any]) -> None:
    assert apply_overrides(sample_raw, {}) is sample_raw


def test_apply_overrides_deep_merges_mappings(sample_raw: dict[str, Any]) -> None:
    patch = {"capability": {"title": "Renamed"}, "escalation_policy": {"max_handoffs": 1}}
    out = apply_overrides(sample_raw, patch)
    assert out["capability"]["title"] == "Renamed"
    assert out["capability"]["id"] == CAP_ID  # sibling keys survive
    assert out["escalation_policy"] == {**sample_raw["escalation_policy"], "max_handoffs": 1}
    assert sample_raw["capability"]["title"] != "Renamed"  # input not mutated


def test_apply_overrides_steps_keyed_by_id_merge_into_right_step(
    sample_raw: dict[str, Any],
) -> None:
    out = apply_overrides(sample_raw, {"steps": {"s2": {"idempotent": False}}})
    assert [s["id"] for s in out["steps"]] == [s["id"] for s in sample_raw["steps"]]
    s2 = out["steps"][1]
    assert s2["idempotent"] is False
    assert s2["target"] == sample_raw["steps"][1]["target"]  # rest of the step untouched
    assert out["steps"][0] == sample_raw["steps"][0]


def test_apply_overrides_replaces_lists_wholesale(sample_raw: dict[str, Any]) -> None:
    new_locators = [{"kind": "text_exact", "text": "Member Lookup"}]
    out = apply_overrides(sample_raw, {"steps": {"s1": {"target": {"locators": new_locators}}}})
    target = out["steps"][0]["target"]
    assert target["locators"] == new_locators
    assert target["text_hint"] == "Members"  # sibling mapping keys still merged


def test_apply_overrides_unknown_step_raises(sample_raw: dict[str, Any]) -> None:
    with pytest.raises(ArtifactError, match="unknown step 's99'"):
        apply_overrides(sample_raw, {"steps": {"s99": {"idempotent": False}}})


def test_apply_overrides_steps_must_be_mapping(sample_raw: dict[str, Any]) -> None:
    with pytest.raises(ArtifactError, match="must be a mapping"):
        apply_overrides(sample_raw, {"steps": [{"id": "s1"}]})


def test_apply_overrides_records_patch(sample_raw: dict[str, Any]) -> None:
    patch = {"steps": {"s1": {"idempotent": False}}}
    out = apply_overrides(sample_raw, patch)
    assert out["overrides"] == patch
    assert out["overrides"] is not patch  # copied, not aliased


# --------------------------------------------------------------------------------------
# load with tenant
# --------------------------------------------------------------------------------------


def test_load_with_tenant_example_b_relabels_s1(repo_root: Path, fixture_path: Path) -> None:
    cap = ArtifactStore(repo_root).load(fixture_path, "example-b")
    assert _locator_names(cap, "s1") == ["Member Lookup", "Member Lookup"]
    assert cap.step("s1").target.text_hint == "Member Lookup"  # type: ignore[union-attr]
    assert cap.step("s1").target.frame == "nav"  # type: ignore[union-attr]
    assert set(cap.overrides) == {"steps"}


def test_load_with_tenant_local_keeps_base(repo_root: Path, fixture_path: Path) -> None:
    cap = ArtifactStore(repo_root).load(fixture_path, "local")
    assert _locator_names(cap, "s1") == ["Members", "Members", ""]
    assert cap.overrides == {}


def test_store_resolves_relative_paths_against_root(repo_root: Path) -> None:
    cap = ArtifactStore(repo_root).load("tests/fixtures/sample_capability.yaml")
    assert cap.capability.id == CAP_ID


# --------------------------------------------------------------------------------------
# save / load round trip
# --------------------------------------------------------------------------------------


def test_save_then_load_round_trips(repo_root: Path, fixture_path: Path, tmp_path: Path) -> None:
    cap = ArtifactStore(repo_root).load(fixture_path, "example-b")
    out = save_capability(cap, tmp_path)
    assert out.name == f"{CAP_ID}@1.0.0.yaml"
    back = load_capability(out)
    assert back.model_dump(exclude={"overrides"}) == cap.model_dump(exclude={"overrides"})
    assert back.overrides == {}


def test_saved_yaml_has_no_overrides_key(repo_root: Path, fixture_path: Path) -> None:
    cap = ArtifactStore(repo_root).load(fixture_path, "example-b")
    assert cap.overrides  # precondition: a patch was applied
    dumped = yaml.safe_load(to_yaml(cap))
    assert "overrides" not in dumped


# --------------------------------------------------------------------------------------
# approval rule
# --------------------------------------------------------------------------------------


def test_approve_sets_status_and_hash(fixture_path: Path) -> None:
    cap = load_capability(fixture_path)
    approved = approve(cap, "alice")
    assert cap.capability.status == "draft"  # original untouched
    assert approved.capability.status == "approved"
    assert approved.capability.review.approved_by == "alice"
    assert approved.capability.review.approved_at
    assert approved.capability.review.artifact_sha256 == cap.content_hash()


def test_save_edited_approved_artifact_reverts_to_draft(fixture_path: Path, tmp_path: Path) -> None:
    approved = approve(load_capability(fixture_path), "alice")
    approved.steps[0].intent = "edited after approval"
    saved = load_capability(save_capability(approved, tmp_path))
    assert saved.capability.status == "draft"
    review = saved.capability.review
    assert review.approved_by is None
    assert review.approved_at is None
    assert review.artifact_sha256 is None


def test_save_unchanged_approved_artifact_stays_approved(
    fixture_path: Path, tmp_path: Path
) -> None:
    approved = approve(load_capability(fixture_path), "alice")
    saved = load_capability(save_capability(approved, tmp_path))
    assert saved.capability.status == "approved"
    assert saved.capability.review.approved_by == "alice"


# --------------------------------------------------------------------------------------
# error paths
# --------------------------------------------------------------------------------------


def test_unsupported_schema_version_raises(sample_raw: dict[str, Any], tmp_path: Path) -> None:
    raw = copy.deepcopy(sample_raw)
    raw["schema_version"] = 2
    p = tmp_path / "v2.yaml"
    p.write_text(yaml.safe_dump(raw), encoding="utf-8")
    with pytest.raises(ArtifactError, match="unsupported schema_version 2"):
        load_capability(p)


def test_missing_file_raises(tmp_path: Path) -> None:
    with pytest.raises(ArtifactError, match="file not found"):
        load_capability(tmp_path / "nope.yaml")


def test_non_mapping_top_level_raises(tmp_path: Path) -> None:
    p = tmp_path / "list.yaml"
    p.write_text("- a\n- b\n", encoding="utf-8")
    with pytest.raises(ArtifactError, match="expected a mapping"):
        load_capability(p)


def test_validation_error_is_wrapped_with_path(sample_raw: dict[str, Any], tmp_path: Path) -> None:
    raw = copy.deepcopy(sample_raw)
    raw["restart_anchor"] = "s99"
    p = tmp_path / "bad.yaml"
    p.write_text(yaml.safe_dump(raw), encoding="utf-8")
    with pytest.raises(ArtifactError, match=r"(?s)bad\.yaml.*restart_anchor"):
        load_capability(p)
