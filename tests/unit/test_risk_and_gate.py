"""Risk classification and the gate's confirmation path — the conservative handling of irreversible
actions the brief asks for, exercised directly."""

from __future__ import annotations

from pathlib import Path

import pytest

from teller.policy.gate import PolicyGate
from teller.policy.model import load_policy
from teller.policy.risk import RiskClassifier
from teller.surface.base import Action, Element

ROOT = Path(__file__).resolve().parents[2]
URL = "http://127.0.0.1:8600/console/members/10001"


def _el(**kw) -> Element:
    base = dict(mark_id=1, role="button", name="Go", text="Go", tag="input", frame="main", bbox=(0, 0, 10, 10), input_type="submit", is_submit=True)
    base.update(kw)
    return Element(**base)


@pytest.fixture(scope="module")
def policy():
    return load_policy(ROOT / "policies" / "ledgerline.yaml")


def test_read_actions_are_read(policy) -> None:
    rc = RiskClassifier(policy)
    for kind in ("read", "scroll", "navigate", "press", "select", "type"):
        risk, _ = rc.classify(Action(kind=kind), _el(), URL)
        assert risk == "read"


def test_click_on_link_is_read_even_on_an_irreversible_route(policy) -> None:
    rc = RiskClassifier(policy)
    link = _el(role="link", name="Post Transaction", text="Post Transaction", tag="a", input_type=None, is_submit=False, attrs={"href": "/console/members/10001/transactions/new"})
    assert rc.classify(Action(kind="click"), link, URL)[0] == "read", "a plain navigation link only opens the form"
    script_link = _el(role="link", name="Post", text="Post", tag="a", input_type=None, is_submit=False, attrs={"href": "javascript:doPost()"})
    assert rc.classify(Action(kind="click"), script_link, URL)[0] == "irreversible_write", "a script link that posts is a committing control"
    plain = _el(role="link", name="Member Search", text="Member Search", tag="a", input_type=None, is_submit=False, attrs={"href": "/console/members/search"})
    assert rc.classify(Action(kind="click"), plain, URL + "/transactions/new")[0] == "read"


def test_submit_on_irreversible_route_requires_confirmation(policy) -> None:
    gate = PolicyGate(policy)
    post = _el(name="Post", text="Post", attrs={"name": "cmdPost"})
    d = gate.check(Action(kind="click"), post, URL + "/transactions/new")
    assert d.allowed and d.confirm_required and d.risk_class == "irreversible_write"


def test_submit_on_reversible_route_is_reversible_write(policy) -> None:
    gate = PolicyGate(policy)
    open_btn = _el(name="Open", text="Open", attrs={"name": "cmdOpen"})
    d = gate.check(Action(kind="click"), open_btn, URL + "/subaccounts/new")
    assert d.allowed and not d.confirm_required and d.risk_class == "reversible_write"


def test_declared_risk_only_tightens(policy) -> None:
    rc = RiskClassifier(policy)
    go = _el()
    assert rc.classify(Action(kind="click"), go, URL + "/search", declared="irreversible_write")[0] == "irreversible_write"
    post = _el(name="Post", text="Post")
    assert rc.classify(Action(kind="click"), post, URL + "/transactions/new", declared="read")[0] == "irreversible_write"


def test_accepting_a_dialog_that_posts_is_irreversible(policy) -> None:
    rc = RiskClassifier(policy)
    assert rc.classify(Action(kind="dismiss_dialog", accept=True), None, URL, dialog_text="Post this transaction? This cannot be undone.")[0] == "irreversible_write"
    assert rc.classify(Action(kind="dismiss_dialog", accept=True), None, URL, dialog_text="Open this sub-account?")[0] == "reversible_write"
    assert rc.classify(Action(kind="dismiss_dialog", accept=False), None, URL, dialog_text="Post this transaction?")[0] == "read"


def test_gate_denies_offlist_navigation_and_links(policy) -> None:
    gate = PolicyGate(policy)
    assert not gate.check(Action(kind="navigate", url="https://evil.example/"), None, URL).allowed
    assert not gate.check(Action(kind="navigate", url="/__chaos"), None, URL).allowed
    ext = _el(role="link", tag="a", input_type=None, is_submit=False, attrs={"href": "https://evil.example/x"})
    assert not gate.check(Action(kind="click"), ext, URL).allowed
    assert not gate.check(Action(kind="click"), _el(), "https://evil.example/console").allowed, "off-allowlist current page: refuse to act"
    assert not gate.check(Action(kind="click"), _el(disabled=True), URL).allowed
