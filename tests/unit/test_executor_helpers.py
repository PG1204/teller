"""Unit tests for the pure helpers in ``teller.replay.executor``: version ranges and the static
policy check (no browser, no run)."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
import yaml

from teller.artifact.model import Capability, Tenant
from teller.policy.model import Policy, load_policy
from teller.replay.executor import static_policy_check, version_in_range


@pytest.mark.parametrize(
    ("version", "spec", "expected"),
    [
        ("4.2", "*", True),
        ("4.2", "", True),
        ("4.2", "   *  ", True),
        ("4.2", ">=4.1 <5", True),
        ("4.1", ">=4.1 <5", True),
        ("4.1.9", ">=4.1 <5", True),
        ("4.9.9", ">=4.1 <5", True),
        ("5.0", ">=4.1 <5", False),
        ("5", ">=4.1 <5", False),
        ("3.9", ">=4.1 <5", False),
        ("4.0", ">=4.1 <5", False),
        ("4.2", "4.2", True),
        ("4.2.7", "4.2", True),
        ("4.3", "4.2", False),
        ("4.2", "==4.2", True),
        ("4.2", "=4.2", True),
        ("4.0", ">=4", True),
        ("4", ">=4", True),
        ("4.2", ">=4", True),
        ("3.9", ">=4", False),
        ("4", ">=4.1", False),
        ("4.2", ">4.2", False),
        ("4.2.1", ">4.2", True),
        ("4.2", "<=4.2", True),
        ("4.2.1", "<=4.2", False),
        ("4.9", "<5", True),
        ("5.1", "<5", False),
        ("4.2", "~4", True),  # unknown syntax never blocks
        ("v4.2 build 17", ">=4.1 <5", True),  # digits are extracted from noisy strings
    ],
)
def test_version_in_range(version: str, spec: str, expected: bool) -> None:
    assert version_in_range(version, spec) is expected


@pytest.fixture
def policy(repo_root: Path) -> Policy:
    return load_policy(repo_root / "policies" / "ledgerline.yaml")


@pytest.fixture
def tenant(repo_root: Path) -> Tenant:
    return Tenant.model_validate(yaml.safe_load((repo_root / "tenants" / "local.yaml").read_text()))


def test_fixture_passes_static_policy_check(
    sample_raw: dict[str, Any], policy: Policy, tenant: Tenant
) -> None:
    cap = Capability.model_validate(sample_raw)
    assert static_policy_check(cap, policy, tenant) == []
    assert static_policy_check(cap, policy, None) == []


def test_disallowed_action_is_reported(sample_raw: dict[str, Any], policy: Policy, tenant: Tenant) -> None:
    narrowed = policy.model_copy(
        update={"actions_allow": [a for a in policy.actions_allow if a != "scroll"]}
    )
    assert not narrowed.action_allowed("scroll")
    sample_raw["steps"][0]["action"] = "scroll"
    cap = Capability.model_validate(sample_raw)
    problems = static_policy_check(cap, narrowed, tenant)
    assert problems == ["s1: action 'scroll' not allowed by policy"]
    # the unmodified policy still allows it
    assert static_policy_check(cap, policy, tenant) == []


def test_navigate_to_another_host_is_reported(
    sample_raw: dict[str, Any], policy: Policy, tenant: Tenant
) -> None:
    sample_raw["steps"][0]["action"] = "navigate"
    sample_raw["steps"][0]["url"] = "https://evil.example/console"
    cap = Capability.model_validate(sample_raw)
    problems = static_policy_check(cap, policy, tenant)
    assert problems == ["s1: navigate to 'https://evil.example/console' denied by policy"]


def test_navigate_to_denied_route_is_reported(
    sample_raw: dict[str, Any], policy: Policy, tenant: Tenant
) -> None:
    sample_raw["steps"][0]["action"] = "navigate"
    sample_raw["steps"][0]["url"] = "{base_url}/__chaos/reset"
    cap = Capability.model_validate(sample_raw)
    problems = static_policy_check(cap, policy, tenant)
    assert len(problems) == 1
    assert problems[0].startswith("s1: navigate to '{base_url}/__chaos/reset' denied")


def test_navigate_within_tenant_with_placeholders_is_fine(
    sample_raw: dict[str, Any], policy: Policy, tenant: Tenant
) -> None:
    sample_raw["steps"][0]["action"] = "navigate"
    sample_raw["steps"][0]["url"] = "{base_url}/console/members/{member_id}"
    cap = Capability.model_validate(sample_raw)
    assert static_policy_check(cap, policy, tenant) == []
    # without a tenant the {base_url} form is not resolvable and is not checked
    assert static_policy_check(cap, policy, None) == []


def test_entry_url_outside_policy_is_reported(
    sample_raw: dict[str, Any], policy: Policy, tenant: Tenant
) -> None:
    sample_raw["capability"]["app"]["entry_url"] = "{base_url}/console/admin/home"
    cap = Capability.model_validate(sample_raw)
    problems = static_policy_check(cap, policy, tenant)
    assert problems == ["entry_url '{base_url}/console/admin/home' denied by policy"]


def test_problems_accumulate(sample_raw: dict[str, Any], policy: Policy, tenant: Tenant) -> None:
    narrowed = policy.model_copy(update={"actions_allow": ["click", "type", "read"]})
    sample_raw["steps"][0]["action"] = "navigate"
    sample_raw["steps"][0]["url"] = "https://evil.example/"
    cap = Capability.model_validate(sample_raw)
    problems = static_policy_check(cap, narrowed, tenant)
    assert "s1: action 'navigate' not allowed by policy" in problems
    assert "s1: navigate to 'https://evil.example/' denied by policy" in problems
    assert len(problems) == 2
