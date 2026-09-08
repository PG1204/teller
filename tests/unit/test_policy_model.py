"""Unit tests for the policy allowlist model."""

from __future__ import annotations

import re
from pathlib import Path

import pytest
from pydantic import ValidationError

from teller.policy.model import Policy, load_policy

BASE = "http://127.0.0.1:8600"


@pytest.fixture
def policy(repo_root: Path) -> Policy:
    return load_policy(repo_root / "policies" / "ledgerline.yaml")


def test_policy_loads(policy: Policy) -> None:
    assert policy.origins == [BASE]
    assert policy.downloads is False
    assert policy.max_steps == 20


def test_sha256_is_stable_across_loads(repo_root: Path, policy: Policy) -> None:
    again = load_policy(repo_root / "policies" / "ledgerline.yaml")
    assert policy.sha256() == again.sha256()
    assert len(policy.sha256()) == 64


def test_sha256_changes_with_content(policy: Policy) -> None:
    changed = policy.model_copy(update={"max_steps": 21})
    assert changed.sha256() != policy.sha256()


@pytest.mark.parametrize(
    "url",
    [f"{BASE}/console/members/10001", f"{BASE}/login", f"{BASE}/", f"{BASE}/static/app.css"],
)
def test_url_allowed_true(policy: Policy, url: str) -> None:
    assert policy.url_allowed(url) is True


@pytest.mark.parametrize(
    "url",
    [
        pytest.param(f"{BASE}/__chaos", id="chaos-root"),
        pytest.param(f"{BASE}/__chaos/reset", id="chaos-sub"),
        pytest.param(f"{BASE}/console/admin/x", id="admin"),
        pytest.param("https://evil.example/console", id="other-origin"),
        pytest.param("http://127.0.0.1:8601/console", id="other-port"),
        pytest.param("javascript:go('/x')", id="javascript"),
        pytest.param("data:text/html,hi", id="data"),
    ],
)
def test_url_allowed_false(policy: Policy, url: str) -> None:
    assert policy.url_allowed(url) is False


def test_about_blank_allowed(policy: Policy) -> None:
    assert policy.url_allowed("about:blank") is True
    assert policy.url_allowed("about:srcdoc") is False


def test_action_allowed(policy: Policy) -> None:
    assert policy.action_allowed("click") is True
    assert policy.action_allowed("download") is False
    assert policy.action_allowed("run_subflow") is False


def test_deny_wins_over_allow(policy: Policy) -> None:
    # /console/** allows it, /console/admin/** denies it.
    assert policy.route_allowed(f"{BASE}/console/admin/users") is False


def test_double_star_spans_segments() -> None:
    p = Policy(origins=["http://h"], routes_allow=["/a/**"], actions_allow=["click"])
    assert p.route_allowed("http://h/a/b") is True
    assert p.route_allowed("http://h/a/b/c") is True
    assert p.route_allowed("http://h/b") is False


def test_single_star_matches_within_segment() -> None:
    p = Policy(origins=["http://h"], routes_allow=["/a/*"], actions_allow=["click"])
    assert p.route_allowed("http://h/a/b") is True


def test_single_star_does_not_span_segments() -> None:
    p = Policy(origins=["http://h"], routes_allow=["/a/*"], actions_allow=["click"])
    assert p.route_allowed("http://h/a/b/c") is False


def test_origin_normalisation_ignores_case_and_path() -> None:
    p = Policy(origins=["HTTP://Example.COM:8080"], actions_allow=["click"])
    assert p.origin_allowed("http://example.com:8080/any/path") is True
    assert p.origin_allowed("http://example.com/any/path") is False


def _bad_controls_policy() -> Policy:
    return Policy(
        origins=["http://h"],
        actions_allow=["click"],
        risk={"irreversible": {"controls": ["(unclosed"]}},
    )


def test_invalid_regex_in_risk_controls_rejected() -> None:
    with pytest.raises((ValidationError, re.error)):
        _bad_controls_policy()


def test_invalid_regex_in_redaction_rejected() -> None:
    with pytest.raises((ValidationError, re.error)):
        Policy(origins=["http://h"], actions_allow=["click"], redaction={"regexes": {"x": "["}})


def test_invalid_regex_surfaces_as_validation_error() -> None:
    with pytest.raises(ValidationError):
        _bad_controls_policy()


def test_policy_requires_origins_and_actions() -> None:
    with pytest.raises(ValidationError):
        Policy(origins=[], actions_allow=["click"])
    with pytest.raises(ValidationError):
        Policy(origins=["http://h"], actions_allow=[])


def test_unknown_action_kind_rejected() -> None:
    with pytest.raises(ValidationError):
        Policy(origins=["http://h"], actions_allow=["download"])
