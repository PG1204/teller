"""Unit tests for predicate evaluation (``replay.detectors``) and the three-tier sweep
(``replay.classify``) against hand-built observations."""

from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Any

import pytest

from teller.artifact.model import AppProfile, Capability, Predicate, ValueRef
from teller.artifact.store import load_profile
from teller.replay.classify import Sweep, sweep
from teller.replay.detectors import EvalContext, css_selectors, describe, evaluate, observed_summary
from teller.surface.base import DialogInfo, Element, Observation

BASE = "http://127.0.0.1:8600"
PARAMS = {"member_id": "10001"}


def make_obs(
    *,
    main: str = "",
    nav: str = "Home Members Accounts",
    top: str = "Ledgerline Console",
    url: str = f"{BASE}/console/members/search",
    title: str = "Ledgerline Member Servicing Console",
    http_status: int | None = 200,
    selector_hits: dict[str, bool] | None = None,
    dialog: DialogInfo | None = None,
    elements: list[Element] | None = None,
) -> Observation:
    visible = {"": top, "nav": nav, "main": main}
    return Observation(
        url=url,
        title=title,
        frames=["", "nav", "main"],
        http_status=http_status,
        elements=elements or [],
        dialog=dialog,
        visible_text=visible,
        text_digest=hashlib.sha256("\n".join(visible.values()).encode()).hexdigest(),
        selector_hits=selector_hits or {},
    )


@pytest.fixture
def profile(repo_root: Path) -> AppProfile:
    return load_profile(repo_root / "apps" / "ledgerline-msc" / "profile.yaml")


@pytest.fixture
def capability(sample_raw: dict[str, Any]) -> Capability:
    return Capability.model_validate(sample_raw)


@pytest.fixture
def ctx(profile: AppProfile) -> EvalContext:
    return EvalContext(params=dict(PARAMS), detectors=profile.detectors)


# ---- evaluate: leaves ---------------------------------------------------------------------


def test_text_contains_is_case_insensitive_and_frame_scoped(ctx: EvalContext) -> None:
    obs = make_obs(main="Member Search\nMember #")
    assert evaluate(Predicate(text_contains="member search", frame="main"), obs, ctx)
    assert not evaluate(Predicate(text_contains="Member Search", frame="nav"), obs, ctx)


def test_text_contains_without_frame_searches_all_frames(ctx: EvalContext) -> None:
    obs = make_obs(main="Member Search")
    assert evaluate(Predicate(text_contains="Members"), obs, ctx)  # nav text
    assert evaluate(Predicate(text_contains="Member Search"), obs, ctx)
    assert not evaluate(Predicate(text_contains="Transactions"), obs, ctx)


def test_text_contains_substitutes_params(ctx: EvalContext) -> None:
    obs = make_obs(main="Member Detail\n10001 Dana Whitfield")
    assert evaluate(Predicate(text_contains="{member_id}", frame="main"), obs, ctx)
    assert not evaluate(Predicate(text_contains="{member_id}", frame="main"), make_obs(main="99999"), ctx)


def test_absent_declared_frame_is_tolerated_as_empty(ctx: EvalContext) -> None:
    obs = make_obs(main="Member Search")
    assert not evaluate(Predicate(text_contains="Member Search", frame="gone"), obs, ctx)


def test_text_matches_regex_case_insensitive(ctx: EvalContext) -> None:
    obs = make_obs(main="Application Error ORA-00600: internal error")
    assert evaluate(Predicate(text_matches="ora-[0-9]{5}", frame="main"), obs, ctx)
    assert not evaluate(Predicate(text_matches="^ORA", frame="main"), obs, ctx)


def test_url_and_title_matches(ctx: EvalContext) -> None:
    obs = make_obs(url=f"{BASE}/login?reason=expired", title="Ledgerline 4.2 Servicing Console")
    assert evaluate(Predicate(url_matches=r"/login\?reason=expired"), obs, ctx)
    assert not evaluate(Predicate(url_matches="/console$"), obs, ctx)
    assert evaluate(Predicate(title_matches="Ledgerline .* Servicing Console"), obs, ctx)
    assert not evaluate(Predicate(title_matches="^Other"), obs, ctx)


def test_url_matches_substitutes_params(ctx: EvalContext) -> None:
    obs = make_obs(url=f"{BASE}/console/members/10001")
    assert evaluate(Predicate(url_matches="/members/{member_id}$"), obs, ctx)


def test_http_status_exact_and_gte(ctx: EvalContext) -> None:
    assert evaluate(Predicate(http_status=403), make_obs(http_status=403), ctx)
    assert not evaluate(Predicate(http_status=403), make_obs(http_status=200), ctx)
    assert evaluate(Predicate(http_status_gte=500), make_obs(http_status=503), ctx)
    assert not evaluate(Predicate(http_status_gte=500), make_obs(http_status=404), ctx)
    assert not evaluate(Predicate(http_status_gte=500), make_obs(http_status=None), ctx)
    assert not evaluate(Predicate(http_status=200), make_obs(http_status=None), ctx)


def test_css_exists_with_frame_uses_exact_key(ctx: EvalContext) -> None:
    obs = make_obs(selector_hits={"main|table.accounts": True, "nav|table.accounts": False})
    assert evaluate(Predicate(css_exists="table.accounts", frame="main"), obs, ctx)
    assert not evaluate(Predicate(css_exists="table.accounts", frame="nav"), obs, ctx)
    assert not evaluate(Predicate(css_exists="table.accounts", frame="other"), obs, ctx)


def test_css_exists_without_frame_matches_any_frame(ctx: EvalContext) -> None:
    obs = make_obs(selector_hits={"nav|a.active": False, "main|a.active": True, "main|div.x": False})
    assert evaluate(Predicate(css_exists="a.active"), obs, ctx)
    assert not evaluate(Predicate(css_exists="div.x"), obs, ctx)
    assert not evaluate(Predicate(css_exists="span.missing"), obs, ctx)


def test_value_equals_reads_latest_field_value(ctx: EvalContext) -> None:
    ctx.field_values = {"*": "10001", "main": "10001", "nav": "other"}
    obs = make_obs()
    assert evaluate(Predicate(value_equals=ValueRef(param="member_id")), obs, ctx)
    assert evaluate(Predicate(value_equals=ValueRef(param="member_id"), frame="main"), obs, ctx)
    assert not evaluate(Predicate(value_equals=ValueRef(param="member_id"), frame="nav"), obs, ctx)
    assert not evaluate(Predicate(value_equals=ValueRef(param="member_id"), frame="none"), obs, ctx)
    assert evaluate(Predicate(value_equals=ValueRef(literal="other"), frame="nav"), obs, ctx)


def test_value_equals_is_false_without_typed_value_or_for_secrets(ctx: EvalContext) -> None:
    obs = make_obs()
    assert not evaluate(Predicate(value_equals=ValueRef(param="member_id")), obs, ctx)
    ctx.field_values = {"*": "whatever"}
    assert not evaluate(Predicate(value_equals=ValueRef(secret="LEDGERLINE_PASS")), obs, ctx)


def test_output_present_requires_a_non_empty_output(ctx: EvalContext) -> None:
    obs = make_obs()
    ctx.outputs = {"savings_balance": "2431.17", "empty": ""}
    assert evaluate(Predicate(output_present="savings_balance"), obs, ctx)
    assert not evaluate(Predicate(output_present="empty"), obs, ctx)
    assert not evaluate(Predicate(output_present="missing"), obs, ctx)


def test_dialog_text_matches(ctx: EvalContext) -> None:
    with_dialog = make_obs(dialog=DialogInfo(type="alert", message="Your session will expire soon"))
    assert evaluate(Predicate(dialog_text_matches="session will expire"), with_dialog, ctx)
    assert evaluate(Predicate(dialog_text_matches="SESSION WILL"), with_dialog, ctx)
    assert not evaluate(Predicate(dialog_text_matches="post"), with_dialog, ctx)
    assert not evaluate(Predicate(dialog_text_matches="session"), make_obs(), ctx)


def test_detector_indirection_through_context(ctx: EvalContext) -> None:
    obs = make_obs(http_status=403)
    assert evaluate(Predicate(detector="access_denied"), obs, ctx)
    assert not evaluate(Predicate(detector="app_error"), obs, ctx)
    assert not evaluate(Predicate(detector="no_such_detector"), obs, ctx)
    assert not evaluate(Predicate(detector="access_denied"), obs, EvalContext())


# ---- evaluate: combinators ----------------------------------------------------------------


def test_all_any_of_none_of(ctx: EvalContext) -> None:
    obs = make_obs(main="Member Detail\n10001 Dana Whitfield", http_status=200)
    detail = Predicate(text_contains="Member Detail", frame="main")
    forbidden = Predicate(http_status=403)
    savings = Predicate(text_contains="Share Savings", frame="main")
    assert evaluate(Predicate(all=[detail, Predicate(text_contains="{member_id}", frame="main")]), obs, ctx)
    assert not evaluate(Predicate(all=[detail, forbidden]), obs, ctx)
    assert evaluate(Predicate(any_of=[forbidden, detail]), obs, ctx)
    assert not evaluate(Predicate(any_of=[forbidden, savings]), obs, ctx)
    assert evaluate(Predicate(none_of=[forbidden, savings]), obs, ctx)
    assert not evaluate(Predicate(none_of=[forbidden, detail]), obs, ctx)
    assert evaluate(Predicate(all=[]), obs, ctx)
    assert not evaluate(Predicate(any_of=[]), obs, ctx)
    assert evaluate(Predicate(none_of=[]), obs, ctx)


def test_nested_combinators_from_fixture(capability: Capability, ctx: EvalContext) -> None:
    no_savings = capability.business_outcomes["NO_SAVINGS_ACCOUNT"].detect
    assert evaluate(no_savings, make_obs(main="Member Detail\nAccounts\nChecking"), ctx)
    assert not evaluate(no_savings, make_obs(main="Member Detail\nAccounts\nShare Savings"), ctx)
    assert not evaluate(no_savings, make_obs(main="Search Results"), ctx)


# ---- describe / css_selectors / observed_summary -----------------------------------------


def test_describe_substitutes_params_and_names_the_frame() -> None:
    p = Predicate(text_contains="{member_id}", frame="main")
    assert describe(p, PARAMS) == "text_contains='10001' in frame 'main'"
    assert describe(Predicate(http_status=403), PARAMS) == "http_status=403"
    nested = Predicate(any_of=[Predicate(http_status=403), Predicate(text_contains="You are not authorized")])
    assert describe(nested, PARAMS) == "any_of(http_status=403, text_contains='You are not authorized')"
    assert describe(Predicate(value_equals=ValueRef(param="member_id")), PARAMS) == "value_equals={'param': 'member_id'}"
    assert describe(Predicate(none_of=[Predicate(detector="app_error")]), {}) == "none_of(detector='app_error')"


def test_css_selectors_collects_nested_unique_sorted() -> None:
    preds = [
        Predicate(all=[
            Predicate(css_exists="table.accounts", frame="main"),
            Predicate(any_of=[
                Predicate(css_exists="a.active"),
                Predicate(none_of=[Predicate(css_exists="div.blocker"), Predicate(text_contains="x")]),
            ]),
        ]),
        Predicate(css_exists="a.active"),
        Predicate(http_status=500),
    ]
    assert css_selectors(preds) == ["a.active", "div.blocker", "table.accounts"]
    assert css_selectors([]) == []


def test_observed_summary_truncates_and_flattens_text() -> None:
    long_main = "Member Detail\n" + "x" * 500
    el = Element(mark_id=1, role="link", name="Members", tag="a", frame="nav", bbox=(0, 0, 10, 10))
    obs = make_obs(main=long_main, elements=[el], dialog=DialogInfo(type="confirm", message="Sure?"))
    summary = observed_summary(obs, limit=50)
    assert summary["url"] == obs.url
    assert summary["title"] == obs.title
    assert summary["http_status"] == 200
    assert summary["dialog"] == {"type": "confirm", "message": "Sure?", "default_value": None}
    head = summary["visible_text_head"]
    assert isinstance(head, str) and len(head) == 50
    assert "\n" not in head
    assert head.startswith("Ledgerline Console Home Members Accounts Member")
    assert summary["element_count"] == 1
    assert summary["detector_hits"] == []
    assert observed_summary(make_obs())["dialog"] is None


# ---- classify.sweep -----------------------------------------------------------------------


def _sweep(
    obs: Observation,
    step_id: str | None,
    capability: Capability,
    profile: AppProfile,
    ctx: EvalContext,
    budgets: dict[str, int] | None = None,
) -> Sweep:
    step = capability.step(step_id) if step_id else None
    return sweep(obs, step, capability, profile, ctx, budgets or {})


def test_member_not_found_is_an_outcome_only_at_declaring_step(
    capability: Capability, profile: AppProfile, ctx: EvalContext
) -> None:
    obs = make_obs(main="Search Results\nNo members matched your search.")
    at_s3 = _sweep(obs, "s3", capability, profile, ctx)
    assert at_s3.kind == "outcome"
    assert at_s3.code == "MEMBER_NOT_FOUND"
    assert at_s3.message == "No member exists with that member_id"
    assert not at_s3.empty
    at_s2 = _sweep(obs, "s2", capability, profile, ctx)
    assert at_s2.kind == "none"
    assert at_s2.empty


def test_access_denied_outcome_at_s4_but_undeclared_at_s3(
    capability: Capability, profile: AppProfile, ctx: EvalContext
) -> None:
    obs = make_obs(main="You are not authorized to view this member.", http_status=403)
    at_s4 = _sweep(obs, "s4", capability, profile, ctx)
    assert (at_s4.kind, at_s4.code) == ("outcome", "ACCESS_DENIED")
    at_s3 = _sweep(obs, "s3", capability, profile, ctx)
    assert (at_s3.kind, at_s3.code) == ("undeclared", "access_denied")
    assert "no declared disposition" in at_s3.message
    assert "s3" in at_s3.message


def test_no_savings_account_outcome_at_s5(
    capability: Capability, profile: AppProfile, ctx: EvalContext
) -> None:
    obs = make_obs(main="Member Detail\n10001 Dana Whitfield\nAccounts\nChecking $10.00")
    sw = _sweep(obs, "s5", capability, profile, ctx)
    assert (sw.kind, sw.code) == ("outcome", "NO_SAVINGS_ACCOUNT")


def test_compliance_notice_is_recoverable_until_budget_exhausted(
    capability: Capability, profile: AppProfile, ctx: EvalContext
) -> None:
    obs = make_obs(main="Compliance Notice\nPlease acknowledge the updated policy.")
    fresh = _sweep(obs, "s1", capability, profile, ctx)
    assert fresh.kind == "recoverable"
    assert fresh.code == "compliance_notice"
    assert fresh.source == "artifact"
    assert fresh.condition is not None
    assert fresh.condition.remedy.action == "click"
    assert fresh.condition.max_times == 1
    exhausted = _sweep(obs, "s1", capability, profile, ctx, budgets={"compliance_notice": 1})
    assert exhausted.kind == "undeclared"
    assert exhausted.code == "compliance_notice"
    assert "exhausted (max_times=1)" in exhausted.message
    assert exhausted.condition is None


def test_recoverable_applies_between_steps_too(
    capability: Capability, profile: AppProfile, ctx: EvalContext
) -> None:
    obs = make_obs(main="Loading, please wait")
    sw = _sweep(obs, None, capability, profile, ctx, budgets={"slow_load": 2})
    assert (sw.kind, sw.code, sw.source) == ("recoverable", "slow_load", "artifact")
    assert _sweep(obs, None, capability, profile, ctx, budgets={"slow_load": 3}).kind == "undeclared"


def test_session_expiry_dialog_is_recoverable_from_profile(
    capability: Capability, profile: AppProfile, ctx: EvalContext
) -> None:
    obs = make_obs(dialog=DialogInfo(type="alert", message="Your session will expire in 2 minutes"))
    sw = _sweep(obs, "s2", capability, profile, ctx)
    assert sw.kind == "recoverable"
    assert sw.code == "session_expiry_alert"
    assert sw.source == "profile"
    assert sw.condition is not None
    assert sw.condition.remedy.action == "dismiss_dialog"
    assert sw.condition.remedy.accept is True
    exhausted = _sweep(obs, "s2", capability, profile, ctx, budgets={"session_expiry_alert": 2})
    assert (exhausted.kind, exhausted.code) == ("undeclared", "session_expiry_alert")


def test_unknown_dialog_wins_over_everything_else(
    capability: Capability, profile: AppProfile, ctx: EvalContext
) -> None:
    # even a business-outcome text on the page: the parked dialog is classified first
    obs = make_obs(
        main="No members matched your search.",
        dialog=DialogInfo(type="confirm", message="Are you sure you want to post this?"),
    )
    sw = _sweep(obs, "s3", capability, profile, ctx)
    assert sw.kind == "unknown_dialog"
    assert sw.message == "Are you sure you want to post this?"
    assert sw.code is None


def test_session_expired_url_is_recoverable_from_profile(
    capability: Capability, profile: AppProfile, ctx: EvalContext
) -> None:
    obs = make_obs(url=f"{BASE}/login?reason=expired", main="Your session has expired. Sign in.")
    sw = _sweep(obs, "s3", capability, profile, ctx)
    assert (sw.kind, sw.code, sw.source) == ("recoverable", "session_expired", "profile")
    assert sw.condition is not None
    assert sw.condition.remedy.action == "run_subflow"
    assert sw.condition.remedy.subflow == "login"
    exhausted = _sweep(obs, "s3", capability, profile, ctx, budgets={"session_expired": 1})
    assert (exhausted.kind, exhausted.code) == ("undeclared", "session_expired")


def test_login_wall_is_ignored_in_preflight_but_undeclared_mid_run(
    capability: Capability, profile: AppProfile, ctx: EvalContext
) -> None:
    obs = make_obs(url=f"{BASE}/login", main="User ID\nPassword\nSign In")
    assert _sweep(obs, None, capability, profile, ctx).kind == "none"
    mid = _sweep(obs, "s1", capability, profile, ctx)
    assert (mid.kind, mid.code) == ("undeclared", "login_wall")


@pytest.mark.parametrize(
    "obs",
    [
        pytest.param(make_obs(main="Application Error ORA-00600: internal error"), id="ora-text"),
        pytest.param(make_obs(main="", http_status=500), id="http-500"),
    ],
)
def test_app_error_is_undeclared(
    obs: Observation, capability: Capability, profile: AppProfile, ctx: EvalContext
) -> None:
    sw = _sweep(obs, "s4", capability, profile, ctx)
    assert (sw.kind, sw.code) == ("undeclared", "app_error")
    assert "'app_error'" in sw.message


def test_not_found_page_detector_is_undeclared(
    capability: Capability, profile: AppProfile, ctx: EvalContext
) -> None:
    sw = _sweep(make_obs(main="ERR-4041 page not found"), None, capability, profile, ctx)
    assert (sw.kind, sw.code) == ("undeclared", "not_found_page")
    assert "preflight" in sw.message


def test_plain_detail_page_is_nothing_to_report(
    capability: Capability, profile: AppProfile, ctx: EvalContext
) -> None:
    obs = make_obs(
        url=f"{BASE}/console/members/10001",
        main="Member Detail\n10001 Dana Whitfield\nAccounts\nShare Savings 1234567890 $2,431.17",
    )
    for step_id in ("s4", "s5", "s6", None):
        sw = _sweep(obs, step_id, capability, profile, ctx)
        assert sw.kind == "none", step_id
        assert sw.code is None and sw.condition is None and sw.source is None


def test_outcome_takes_precedence_over_recoverable(
    capability: Capability, profile: AppProfile, ctx: EvalContext
) -> None:
    obs = make_obs(main="No members matched your search.\nCompliance Notice")
    assert _sweep(obs, "s3", capability, profile, ctx).kind == "outcome"
    assert _sweep(obs, "s2", capability, profile, ctx).kind == "recoverable"
