"""Unit tests for the discovery recorder (locator bundles) and the emitter's inference rules."""

from __future__ import annotations

import hashlib
from typing import Any

import pytest

from teller.artifact.model import (
    AttrStableLocator,
    Capability,
    CoordsVerifiedLocator,
    LabelAnchorLocator,
    RoleNameLocator,
    TableCellLocator,
    TextExactLocator,
    XPathAnchoredLocator,
)
from teller.discovery.emit import OutputDecl, ParamDecl, emit_capability
from teller.discovery.recorder import (
    RecordedStep,
    Recorder,
    _row_key_token,
    build_target,
    canonicalize,
    infer_parser,
)
from teller.surface.base import ActResult, DialogInfo, Element, Observation

BASE = "http://127.0.0.1:8600"
PARAMS = {"member_id": "10001"}


# ---- canonicalize / _row_key_token / infer_parser ----------------------------------------


def test_canonicalize_replaces_longest_value_first() -> None:
    params = {"one": "1", "member_id": "10001"}
    assert canonicalize("Member 10001", params) == "Member {member_id}"
    assert canonicalize("row 1 of 10001", params) == "row {one} of {member_id}"


def test_canonicalize_leaves_unrelated_text_and_handles_none() -> None:
    assert canonicalize("Dana Whitfield", PARAMS) == "Dana Whitfield"
    assert canonicalize(None, PARAMS) is None
    assert canonicalize("", PARAMS) == ""
    assert canonicalize("10001 anything", {}) == "10001 anything"


def test_canonicalize_skips_empty_values_and_stringifies() -> None:
    assert canonicalize("abc", {"blank": ""}) == "abc"
    assert canonicalize("id 42", {"n": 42}) == "id {n}"  # type: ignore[dict-item]


def test_row_key_token_prefers_placeholder_else_first_token() -> None:
    assert _row_key_token("Dana Whitfield {member_id} Active") == "{member_id}"
    assert _row_key_token("10001 Dana Whitfield Active") == "10001"
    assert _row_key_token("   ") == "   "
    assert _row_key_token("") == ""


@pytest.mark.parametrize(
    ("value", "parser"),
    [
        ("$2,431.17", "currency_usd"),
        ("-$12.00", "currency_usd"),
        ("2431.17", "currency_usd"),
        ("$ 1,000", "currency_usd"),
        ("10001", "int"),
        ("-3", "int"),
        ("Dana Whitfield", "string"),
        ("1234567890", "int"),
        ("(12.00)", "string"),
    ],
)
def test_infer_parser(value: str, parser: str) -> None:
    assert infer_parser(value) == parser


# ---- build_target ------------------------------------------------------------------------


def _nav_link() -> Element:
    return Element(mark_id=1, role="link", name="Members", text="Members", tag="a", frame="nav", bbox=(20, 140, 84, 16))


def _member_textbox() -> Element:
    return Element(
        mark_id=2, role="textbox", tag="input", frame="main", bbox=(200, 100, 120, 20),
        input_type="text", label="Member #", label_relation="same_row_right", stable_attr=("name", "txtF1"),
    )


def _result_row() -> Element:
    return Element(
        mark_id=3, role="row", name="10001 Dana Whitfield Active", text="10001 Dana Whitfield Active",
        tag="tr", frame="main", bbox=(0, 200, 600, 20),
    )


def _balance_cell(*, sensitive: bool = False) -> Element:
    return Element(
        mark_id=4, role="cell", name="$2,431.17", text="$2,431.17", tag="td", frame="main",
        bbox=(400, 300, 100, 20), sensitive=sensitive,
        table={"anchor": "Accounts", "header": "Current Balance", "row_key_header": "Product", "row_key": "Share Savings", "col": 3},
    )


def test_nav_link_bundle_role_then_text_then_coords() -> None:
    target = build_target(_nav_link(), PARAMS)
    assert target.kinds() == ["role_name", "text_exact", "coords_verified"]
    assert target.frame == "nav"
    assert target.tag_hint == "a"
    assert target.text_hint == "Members"
    role = target.locators[0]
    assert isinstance(role, RoleNameLocator) and role.role == "link" and role.name == "Members"
    assert role.robustness
    text = target.locators[1]
    assert isinstance(text, TextExactLocator) and text.text == "Members" and text.tag == "a"
    coords = target.locators[-1]
    assert isinstance(coords, CoordsVerifiedLocator)
    assert (coords.x, coords.y) == (62, 148)
    assert coords.verify_text == "Members"


def test_textbox_bundle_uses_label_anchor_and_stable_attr() -> None:
    target = build_target(_member_textbox(), PARAMS)
    assert target.kinds() == ["label_anchor", "attr_stable", "coords_verified"]
    label = target.locators[0]
    assert isinstance(label, LabelAnchorLocator)
    assert label.label == "Member #"
    assert label.relation == "same_row_right"
    assert label.control == "text_input"
    attr = target.locators[1]
    assert isinstance(attr, AttrStableLocator)
    assert (attr.attr, attr.value, attr.tag) == ("name", "txtF1", "input")
    assert target.text_hint is None
    assert target.tag_hint == "input"


def test_password_and_select_controls_are_typed() -> None:
    pw = _member_textbox().model_copy(update={"input_type": "password", "label": "Password"})
    assert build_target(pw, PARAMS).locators[0].control == "password"  # type: ignore[union-attr]
    sel = Element(mark_id=5, role="combobox", tag="select", frame="main", bbox=(0, 0, 10, 10), label="Product")
    assert build_target(sel, PARAMS).locators[0].control == "select"  # type: ignore[union-attr]


def test_row_bundle_identifies_by_param_key_and_leaks_no_other_data() -> None:
    target = build_target(_result_row(), PARAMS)
    assert target.kinds() == ["text_exact", "xpath_anchored", "coords_verified"]
    text = target.locators[0]
    assert isinstance(text, TextExactLocator)
    assert text.text == "{member_id}"
    assert text.tag == "tr"
    xpath = target.locators[1]
    assert isinstance(xpath, XPathAnchoredLocator)
    assert xpath.anchor_text == "{member_id}"
    assert xpath.xpath == "ancestor::tr[1]"
    assert target.text_hint == "{member_id}"
    dumped = target.model_dump_json()
    assert "Dana" not in dumped and "Whitfield" not in dumped and "10001" not in dumped


def test_row_without_param_uses_first_token() -> None:
    target = build_target(_result_row(), {})
    assert target.locators[0].text == "10001"  # type: ignore[union-attr]
    assert "Dana" not in target.model_dump_json()


def test_table_cell_for_read_skips_text_strategies() -> None:
    target = build_target(_balance_cell(), PARAMS, for_read=True)
    assert target.kinds() == ["table_cell", "xpath_anchored", "coords_verified"]
    cell = target.locators[0]
    assert isinstance(cell, TableCellLocator)
    assert cell.table_anchor == "Accounts"
    assert cell.row_match.column == "Product"
    assert cell.row_match.equals == "Share Savings"
    assert cell.column == "Current Balance"
    xpath = target.locators[1]
    assert isinstance(xpath, XPathAnchoredLocator)
    assert xpath.anchor_text == "Share Savings"
    assert xpath.xpath == "ancestor::tr[1]/td[3]"
    coords = target.locators[-1]
    assert isinstance(coords, CoordsVerifiedLocator) and coords.verify_text == ""
    assert target.text_hint is None
    assert "2,431" not in target.model_dump_json()


def test_table_cell_clicked_keeps_exact_cell_text() -> None:
    target = build_target(_balance_cell(), PARAMS)
    assert target.kinds() == ["table_cell", "text_exact", "xpath_anchored", "coords_verified"]
    text = target.locators[1]
    assert isinstance(text, TextExactLocator) and text.text == "$2,431.17" and text.tag == "td"
    assert target.text_hint == "$2,431.17"


def test_sensitive_cell_has_no_text_hint_or_text_locator() -> None:
    target = build_target(_balance_cell(sensitive=True), PARAMS)
    assert "text_exact" not in target.kinds()
    assert target.text_hint is None


def test_cell_without_col_uses_generic_td_xpath() -> None:
    el = _balance_cell()
    assert el.table is not None
    el.table.pop("col")
    target = build_target(el, PARAMS, for_read=True)
    assert target.locators[1].xpath == "ancestor::tr[1]/td"  # type: ignore[union-attr]


def test_top_frame_element_has_no_target_frame() -> None:
    el = _nav_link().model_copy(update={"frame": ""})
    assert build_target(el, PARAMS).frame is None


# ---- emit_capability ---------------------------------------------------------------------


def _obs(main: str, *, url: str = f"{BASE}/console", dialog: DialogInfo | None = None) -> Observation:
    visible = {"": "Ledgerline Console", "nav": "Home Members Accounts", "main": main}
    return Observation(
        url=url, title="Ledgerline Member Servicing Console", frames=["", "nav", "main"],
        http_status=200, elements=[], dialog=dialog, visible_text=visible,
        text_digest=hashlib.sha256("\n".join(visible.values()).encode()).hexdigest(),
    )


def _act(action: str, *, url_before: str = f"{BASE}/console", url_after: str | None = None, navigated: bool = False, text: str | None = None) -> ActResult:
    return ActResult(ok=True, action=action, url_before=url_before, url_after=url_after or url_before, navigated=navigated, text=text)  # type: ignore[arg-type]


def _happy_path_recorder() -> Recorder:
    rec = Recorder(params=dict(PARAMS))
    home = _obs("Ledgerline Console\nWelcome, operator")
    search = _obs("Member Search\nMember #  Go", url=f"{BASE}/console/members/search")
    results = _obs("Search Results\n10001 Dana Whitfield Active", url=f"{BASE}/console/members/search?q=10001")
    detail = _obs("Member Detail\n10001 Dana Whitfield\nAccounts\nShare Savings $2,431.17", url=f"{BASE}/console/members/10001")
    nav = Element(mark_id=1, role="link", name="Members", text="Members", tag="a", frame="nav", bbox=(20, 140, 84, 16))
    box = Element(mark_id=2, role="textbox", tag="input", frame="main", bbox=(200, 100, 120, 20), input_type="text", label="Member #", stable_attr=("name", "txtF1"))
    go = Element(mark_id=3, role="button", name="Go", tag="input", frame="main", bbox=(330, 100, 40, 20), input_type="submit", is_submit=True, stable_attr=("name", "cmdGo"))
    row = Element(mark_id=4, role="row", name="10001 Dana Whitfield Active", text="10001 Dana Whitfield Active", tag="tr", frame="main", bbox=(0, 200, 600, 20))
    cell = Element(mark_id=5, role="cell", name="$2,431.17", text="$2,431.17", tag="td", frame="main", bbox=(400, 300, 100, 20), table={"anchor": "Accounts", "header": "Current Balance", "row_key_header": "Product", "row_key": "Share Savings", "col": 3})
    rec.record(RecordedStep("click", "Open the Members area", nav, build_target(nav, PARAMS), act=_act("click", url_after=f"{BASE}/console/members/search", navigated=True), before=home, after=search, turn=1))
    rec.record(RecordedStep("type", "Enter the member number", box, build_target(box, PARAMS), value="{member_id}", act=_act("type", url_before=search.url), before=search, after=search, turn=2))
    rec.record(RecordedStep("click", "Submit the search", go, build_target(go, PARAMS), act=_act("click", url_before=search.url, url_after=results.url, navigated=True), before=search, after=results, turn=3))
    rec.record(RecordedStep("click", "Open the matching member", row, build_target(row, PARAMS), act=_act("click", url_before=results.url, url_after=detail.url, navigated=True), before=results, after=detail, turn=4))
    rec.record(RecordedStep("read", "Read the Share Savings balance", cell, build_target(cell, PARAMS, for_read=True), act=_act("read", url_before=detail.url, text="$2,431.17"), before=detail, after=detail, output_name="savings_balance", extracted="$2,431.17", turn=5))
    rec.add_checkpoint("Member Detail", "main")
    return rec


def _emit(rec: Recorder, **overrides: Any) -> Capability:
    kwargs: dict[str, Any] = dict(
        goal="Read the member's Share Savings balance",
        params=dict(PARAMS),
        param_decls={"member_id": ParamDecl(type="string", classification="pii_low", pattern="^[0-9]{5}$", description="Five-digit member number")},
        output_decls={"savings_balance": OutputDecl(type="decimal", classification="pii_low", description="Current balance")},
        capability_id="ledgerline.member.read_savings_balance",
        title="Read a member's savings balance",
        description=None,
        profile="ledgerline-msc",
        version_range=">=4.1 <5",
        surface="web_legacy",
        entry_url="{base_url}/console",
        discovered_by="test-model",
        run_id="run_test",
        transcript_sha256="ab" * 32,
    )
    kwargs.update(overrides)
    return emit_capability(rec, **kwargs)


def test_recorder_collects_outputs_only_from_reads() -> None:
    rec = _happy_path_recorder()
    assert rec.outputs == {"savings_balance": "$2,431.17"}
    assert len(rec.steps) == 5
    assert rec.checkpoints == [{"text_contains": "Member Detail", "frame": "main"}]


def test_emit_happy_path_metadata() -> None:
    cap = _emit(_happy_path_recorder())
    meta = cap.capability
    assert meta.status == "draft"
    assert meta.risk_class == "read"
    assert meta.id == "ledgerline.member.read_savings_balance"
    assert meta.version == "1.0.0"
    assert meta.description == "Read the member's Share Savings balance"  # falls back to goal
    assert meta.app.profile == "ledgerline-msc"
    assert meta.app.version_range == ">=4.1 <5"
    assert meta.provenance.discovered_by == "test-model"
    assert meta.provenance.discovery_run_id == "run_test"
    assert meta.provenance.transcript_sha256 == "ab" * 32
    assert meta.provenance.recorded_at
    assert cap.restart_anchor == "s1"
    assert [s.id for s in cap.steps] == ["s1", "s2", "s3", "s4", "s5"]
    assert cap.business_outcomes == {} and cap.recoverable_conditions == []


def test_emit_e1_e2_wait_for_and_expect_from_new_heading() -> None:
    cap = _emit(_happy_path_recorder())
    s1 = cap.step("s1")
    assert s1.wait_for is not None
    assert s1.wait_for.state == "text"
    assert s1.wait_for.text == "Member Search"
    assert s1.wait_for.frame == "main"
    assert s1.expect is not None
    assert s1.expect.text_contains == "Member Search"
    assert s1.expect.frame == "main"
    assert cap.step("s3").expect.text_contains == "Search Results"  # type: ignore[union-attr]
    assert cap.step("s4").wait_for.text == "Member Detail"  # type: ignore[union-attr]


def test_emit_e3_type_step_expects_typed_param() -> None:
    s2 = _emit(_happy_path_recorder()).step("s2")
    assert s2.action == "type"
    assert s2.value is not None and s2.value.param == "member_id"
    assert s2.expect is not None
    assert s2.expect.value_equals is not None
    assert s2.expect.value_equals.param == "member_id"
    assert s2.clear_first is True
    assert s2.wait_for is None


def test_emit_e4_read_step_extracts_with_declared_parser() -> None:
    cap = _emit(_happy_path_recorder())
    s5 = cap.step("s5")
    assert s5.action == "read"
    assert s5.extract is not None
    assert s5.extract.output == "savings_balance"
    assert s5.extract.parse == "currency_usd"
    assert s5.mask_in_evidence is False
    out = cap.outputs["savings_balance"]
    assert out.type == "decimal"
    assert out.parse == "currency_usd"
    assert out.classification == "pii_low"
    assert out.source_step == "s5"
    assert out.description == "Current balance"


def test_emit_e4_undeclared_output_infers_parser_from_text() -> None:
    rec = _happy_path_recorder()
    cap = _emit(rec, output_decls={})
    assert cap.outputs["savings_balance"].parse == "currency_usd"
    assert cap.outputs["savings_balance"].type == "decimal"
    assert cap.outputs["savings_balance"].classification == "none"


def test_emit_e6_idempotence_rules() -> None:
    cap = _emit(_happy_path_recorder())
    assert cap.step("s1").idempotent is True  # nav click
    assert cap.step("s2").idempotent is True  # type
    assert cap.step("s3").idempotent is False  # submitting click
    assert cap.step("s4").idempotent is True  # row click, not a submit
    assert cap.step("s5").idempotent is True  # read


def test_emit_e7_e8_checkpoint_and_params() -> None:
    rec = _happy_path_recorder()
    rec.checkpoints.clear()
    rec.add_checkpoint("Member Detail 10001", "main")
    cap = _emit(rec)
    cp = cap.checkpoint
    assert cp.all is not None
    assert [p.text_contains for p in cp.all if p.text_contains] == ["Member Detail {member_id}"]
    assert [p.output_present for p in cp.all if p.output_present] == ["savings_balance"]
    assert cp.all[0].frame == "main"
    param = cap.params["member_id"]
    assert param.example == "10001"  # kept for pii_low
    assert param.classification == "pii_low"
    assert param.pattern == "^[0-9]{5}$"
    assert cap.param_refs() == {"member_id"}


def test_emit_checkpoint_falls_back_to_last_heading() -> None:
    rec = _happy_path_recorder()
    rec.checkpoints.clear()
    cp = _emit(rec).checkpoint
    assert cp.all is not None
    assert cp.all[0].text_contains == "Member Detail"
    assert cp.all[1].output_present == "savings_balance"


def test_emit_locators_are_canonicalised_and_leak_nothing() -> None:
    cap = _emit(_happy_path_recorder())
    s4 = cap.step("s4")
    assert s4.target is not None
    assert s4.target.text_hint == "{member_id}"
    dumped = cap.model_dump_json()
    assert "Dana" not in dumped
    assert "2,431" not in dumped and "2431" not in dumped


def test_emit_pii_high_output_masks_step_and_leaks_no_value() -> None:
    rec = _happy_path_recorder()
    detail = rec.steps[-1].after
    acct = Element(mark_id=6, role="cell", name="1234567890", text="1234567890", tag="td", frame="main", bbox=(300, 300, 100, 20), sensitive=True, table={"anchor": "Accounts", "header": "Account #", "row_key_header": "Product", "row_key": "Share Savings", "col": 2})
    rec.record(RecordedStep("read", "Read the account number", acct, build_target(acct, PARAMS, for_read=True), act=_act("read", text="1234567890"), before=detail, after=detail, output_name="savings_account_number", extracted="1234567890", turn=6))
    cap = _emit(
        rec,
        output_decls={
            "savings_balance": OutputDecl(type="decimal", classification="pii_low"),
            "savings_account_number": OutputDecl(type="string", classification="pii_high"),
        },
        param_decls={"member_id": ParamDecl(classification="pii_high")},
    )
    s6 = cap.step("s6")
    assert s6.mask_in_evidence is True
    assert s6.extract is not None and s6.extract.output == "savings_account_number"
    assert cap.outputs["savings_account_number"].classification == "pii_high"
    assert cap.params["member_id"].example is None
    dumped = cap.model_dump_json()
    assert "1234567890" not in dumped
    assert "10001" not in dumped
    assert cap.checkpoint.all is not None
    assert {p.output_present for p in cap.checkpoint.all if p.output_present} == {"savings_balance", "savings_account_number"}


def test_emit_e2_url_expect_when_no_heading_appeared() -> None:
    rec = Recorder(params=dict(PARAMS))
    same = _obs("Ledgerline Console\nMenu")
    after = _obs("Ledgerline Console\nMenu", url=f"{BASE}/console/members/10001")
    link = Element(mark_id=1, role="link", name="Open", text="Open", tag="a", frame="main", bbox=(0, 0, 10, 10))
    rec.record(RecordedStep("click", "Open the member", link, build_target(link, PARAMS), act=_act("click", url_after=after.url, navigated=True), before=same, after=after))
    cap = _emit(rec, output_decls={})
    s1 = cap.step("s1")
    assert s1.wait_for is not None
    assert s1.wait_for.state == "url"
    assert s1.wait_for.url_matches == "/console/members/{member_id}"
    assert s1.expect is not None
    assert s1.expect.url_matches == "/console/members/{member_id}"


def test_emit_click_without_change_has_no_wait_or_expect() -> None:
    rec = Recorder(params=dict(PARAMS))
    obs = _obs("Member Search")
    link = Element(mark_id=1, role="button", name="Help", text="Help", tag="button", frame="main", bbox=(0, 0, 10, 10))
    rec.record(RecordedStep("click", "Toggle help", link, build_target(link, PARAMS), act=_act("click"), before=obs, after=obs))
    s1 = _emit(rec, output_decls={}).step("s1")
    assert s1.wait_for is None and s1.expect is None
    assert s1.idempotent is True


def test_emit_e5_dismiss_dialog_kept_verbatim_with_review_note() -> None:
    rec = Recorder(params=dict(PARAMS))
    blocked = _obs("Member Search", dialog=DialogInfo(type="alert", message="Your session will expire soon"))
    clear = _obs("Member Search")
    rec.record(RecordedStep("dismiss_dialog", "Acknowledge the expiry warning", None, None, accept=True, act=_act("dismiss_dialog"), before=blocked, after=clear))
    cap = _emit(rec, output_decls={}, notes="hand-checked")
    s1 = cap.step("s1")
    assert s1.action == "dismiss_dialog"
    assert s1.accept is True
    assert s1.idempotent is False  # accepting a dialog is not repeatable
    notes = cap.capability.review.notes or ""
    assert notes.startswith("hand-checked")
    assert "answered dialog 'Your session will expire soon' with accept=True" in notes
    assert cap.checkpoint.url_matches == ".*"  # nothing better to assert on


def test_emit_risk_class_is_max_over_steps() -> None:
    rec = _happy_path_recorder()
    rec.steps[2].risk_class = "reversible_write"
    cap = _emit(rec)
    assert cap.capability.risk_class == "reversible_write"
    assert cap.step("s3").risk_class == "reversible_write"


def test_emit_human_authored_steps_are_recorded() -> None:
    rec = _happy_path_recorder()
    rec.human_steps.append("s3")
    cap = _emit(rec)
    assert cap.capability.provenance.human_authored_steps == ["s3"]
