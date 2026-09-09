"""Unit tests for discovery tool validation, prompt formatting and the scripted decider."""

from __future__ import annotations

import hashlib
from typing import Any

import pytest

from teller.discovery.llm import (
    CassetteMismatch,
    DeciderError,
    ScriptedDecider,
    cassette_from_transcript,
    obs_hash,
)
from teller.discovery.prompts import (
    SYSTEM_PROMPT,
    format_observation,
    goal_message,
    tool_result_text,
)
from teller.discovery.tools import TERMINAL_TOOLS, TOOLS, TOOLS_BY_NAME, validate_call
from teller.surface.base import DialogInfo, Element, Observation

# ---- tools ---------------------------------------------------------------------------------


def test_every_tool_requires_intent_and_forbids_extras() -> None:
    assert TERMINAL_TOOLS <= set(TOOLS_BY_NAME)
    for spec in TOOLS:
        assert "intent" in spec.required
        schema = spec.json_schema()
        assert schema["additionalProperties"] is False
        assert set(schema["required"]) <= set(schema["properties"])


def test_validate_call_unknown_tool() -> None:
    err = validate_call("teleport", {"intent": "x"})
    assert err is not None
    assert err.startswith("unknown tool 'teleport'")
    assert "click" in err


def test_validate_call_missing_required() -> None:
    err = validate_call("click", {"mark_id": 3})
    assert err == "click: missing required argument(s) ['intent']"
    err = validate_call("type_text", {"intent": "x"})
    assert err is not None and "'mark_id'" in err and "'text'" in err


def test_validate_call_extra_argument() -> None:
    err = validate_call("click", {"mark_id": 3, "intent": "x", "force": True})
    assert err == "click: unexpected argument(s) ['force']"


def test_validate_call_coerces_integer_from_string_in_place() -> None:
    args: dict[str, Any] = {"mark_id": "12", "intent": "open"}
    assert validate_call("click", args) is None
    assert args["mark_id"] == 12
    assert isinstance(args["mark_id"], int)


def test_validate_call_rejects_non_integer_mark_id() -> None:
    assert validate_call("click", {"mark_id": "abc", "intent": "x"}) == "click: mark_id must be an integer mark id"
    assert validate_call("click", {"mark_id": None, "intent": "x"}) == "click: mark_id must be an integer mark id"


def test_validate_call_boolean_type_error() -> None:
    assert validate_call("dismiss_dialog", {"accept": "yes", "intent": "x"}) == "dismiss_dialog: accept must be true or false"
    assert validate_call("dismiss_dialog", {"accept": 1, "intent": "x"}) == "dismiss_dialog: accept must be true or false"
    assert validate_call("dismiss_dialog", {"accept": False, "intent": "x"}) is None


def test_validate_call_stringifies_non_string_values() -> None:
    args: dict[str, Any] = {"mark_id": 1, "text": 10001, "intent": "type the id"}
    assert validate_call("type_text", args) is None
    assert args["text"] == "10001"


def test_validate_call_accepts_optional_arguments() -> None:
    assert validate_call("scroll", {"intent": "see more"}) is None
    assert validate_call("scroll", {"direction": "down", "intent": "see more"}) is None
    assert validate_call("press_key", {"key": "Enter", "mark_id": 2, "intent": "submit"}) is None
    assert validate_call("done", {"summary": "ok", "intent": "finished"}) is None


# ---- prompts -------------------------------------------------------------------------------


def _obs(*, main: str = "Member Search\nMember #", elements: list[Element] | None = None, dialog: DialogInfo | None = None, http_status: int | None = 200) -> Observation:
    visible = {"": "Ledgerline Console", "nav": "Home Members", "main": main}
    return Observation(
        url="http://127.0.0.1:8600/console", title="Ledgerline Member Servicing Console",
        frames=["", "nav", "main"], http_status=http_status, elements=elements or [], dialog=dialog,
        visible_text=visible, text_digest=hashlib.sha256(main.encode()).hexdigest(),
    )


def test_format_observation_dialog_only_form() -> None:
    obs = _obs(dialog=DialogInfo(type="confirm", message="Post this transaction?"), elements=[
        Element(mark_id=1, role="link", name="Members", tag="a", frame="nav", bbox=(0, 0, 1, 1)),
    ])
    text = format_observation(obs, turn=3)
    assert text.startswith("TURN 3\nURL: http://127.0.0.1:8600/console\nTitle: Ledgerline Member Servicing Console")
    assert "Frames: top, nav, main" in text
    assert "HTTP status (main): 200" in text
    assert "OPEN DIALOG (confirm): 'Post this transaction?'" in text
    assert "dismiss_dialog" in text
    assert "ELEMENTS" not in text
    assert "VISIBLE TEXT" not in text


def test_format_observation_element_table_form() -> None:
    els = [
        Element(mark_id=1, role="link", name="Members", tag="a", frame="nav", bbox=(0, 0, 1, 1)),
        Element(mark_id=2, role="textbox", tag="input", frame="main", bbox=(0, 0, 1, 1), label="Member #", input_type="text"),
    ]
    text = format_observation(_obs(elements=els), turn=1)
    assert "ELEMENTS (act by mark id):" in text
    assert "[1] link 'Members' frame=nav" in text
    assert "[2] textbox label='Member #' type=text frame=main" in text
    assert text.endswith("VISIBLE TEXT (main): Member Search | Member #")


def test_format_observation_without_elements_or_status() -> None:
    text = format_observation(_obs(http_status=None), turn=2)
    assert "ELEMENTS: none visible" in text
    assert "HTTP status" not in text


def test_format_observation_truncates_visible_text() -> None:
    text = format_observation(_obs(main="x" * 50), turn=1, text_limit=10)
    assert text.endswith("VISIBLE TEXT (main): " + "x" * 10 + " …")


def test_format_observation_falls_back_to_all_frames_without_main() -> None:
    obs = _obs()
    obs.visible_text = {"": "Top only\ntext"}
    text = format_observation(obs, turn=1)
    assert text.endswith("VISIBLE TEXT (main): Top only | text")


def test_goal_message_lists_params_with_placeholders_and_outputs() -> None:
    msg = goal_message(
        "Read the balance",
        {"member_id": "10001"},
        {"savings_balance": "Current Share Savings balance"},
    )
    assert msg.startswith("GOAL: Read the balance\n")
    assert "PARAMETERS (typed inputs supplied for this run):" in msg
    assert "  - member_id = 10001   (placeholder: {member_id})" in msg
    assert "OUTPUTS to record with read_value (use exactly these names):" in msg
    assert "  - savings_balance: Current Share Savings balance" in msg
    assert msg.endswith("Begin. Observe the screen and take the first action.")


def test_goal_message_without_params_or_outputs() -> None:
    msg = goal_message("Just look", {}, None)
    assert "PARAMETERS" not in msg
    assert "OUTPUTS" not in msg
    assert msg.splitlines()[0] == "GOAL: Just look"


def test_tool_result_text_and_system_prompt_mention_the_rules() -> None:
    assert tool_result_text(True, "clicked") == "OK. clicked"
    assert tool_result_text(False, "no such mark") == "FAILED. no such mark"
    assert "ask_human" in SYSTEM_PROMPT
    assert "assert_checkpoint" in SYSTEM_PROMPT
    assert "Never try to log in" in SYSTEM_PROMPT


# ---- scripted decider / cassette -----------------------------------------------------------

OBS1 = "TURN 1\nURL: /console\nELEMENTS: [1] link 'Members'"
OBS2 = "TURN 2\nURL: /console/members/search"


def _fake_transcript() -> list[dict[str, Any]]:
    return [
        {"role": "user", "text": "GOAL: read the balance"},
        {"role": "user", "observation": OBS1, "observation_hash": obs_hash(OBS1), "screenshot": True},
        {"role": "model", "tool": "click", "args": {"mark_id": 1, "intent": "open members"}, "text": None},
        {"role": "user", "observation": OBS2, "observation_hash": obs_hash(OBS2), "screenshot": True},
        {"role": "model", "text": "thinking aloud, no tool", "finish_reason": "STOP"},
        {"role": "model", "tool": "done", "args": {"summary": "ok", "intent": "finished"}},
    ]


def test_obs_hash_is_short_and_deterministic() -> None:
    assert obs_hash(OBS1) == obs_hash(OBS1)
    assert obs_hash(OBS1) != obs_hash(OBS2)
    assert len(obs_hash(OBS1)) == 16


def test_cassette_from_transcript_pairs_calls_with_observation_hashes() -> None:
    cassette = cassette_from_transcript("gemini", "gemini-test", _fake_transcript())
    assert cassette["provider"] == "gemini"
    assert cassette["model"] == "gemini-test"
    assert cassette["turns"] == [
        {"observation_hash": obs_hash(OBS1), "tool": "click", "args": {"mark_id": 1, "intent": "open members"}},
        {"observation_hash": obs_hash(OBS2), "tool": "done", "args": {"summary": "ok", "intent": "finished"}},
    ]


def test_scripted_decider_replays_in_order_then_exhausts() -> None:
    dec = ScriptedDecider(cassette_from_transcript("gemini", "gemini-test", _fake_transcript()))
    dec.start("system", TOOLS, "GOAL")
    assert dec.name == "scripted"
    assert dec.model == "gemini-test"
    first = dec.decide("anything", None, None)
    assert (first.tool, first.args, first.stop_reason) == ("click", {"mark_id": 1, "intent": "open members"}, "cassette")
    second = dec.decide("anything else", b"jpeg", None)
    assert second.tool == "done"
    with pytest.raises(DeciderError, match="cassette exhausted"):
        dec.decide("more", None, None)
    assert dec.usage()["calls"] == 2
    assert dec.usage()["provider"] == "scripted"
    transcript = dec.transcript()
    assert transcript[0] == {"role": "user", "text": "GOAL"}
    assert [t.get("tool") for t in transcript if t["role"] == "model"] == ["click", "done"]
    assert all(t["replayed"] for t in transcript if t["role"] == "model")
    assert transcript[1]["observation_hash"] == obs_hash("anything")
    assert transcript[3]["screenshot"] is True


def test_scripted_decider_returns_a_copy_of_args() -> None:
    cassette = cassette_from_transcript("gemini", "g", _fake_transcript())
    dec = ScriptedDecider(cassette)
    decision = dec.decide(OBS1, None, None)
    decision.args["mark_id"] = 99
    assert cassette["turns"][0]["args"]["mark_id"] == 1


def test_strict_cassette_raises_on_different_observation() -> None:
    dec = ScriptedDecider(cassette_from_transcript("gemini", "g", _fake_transcript()), strict=True)
    with pytest.raises(CassetteMismatch, match="turn 1: observation differs"):
        dec.decide("a different screen", None, None)
    assert issubclass(CassetteMismatch, DeciderError)


def test_strict_cassette_passes_on_recorded_hash() -> None:
    dec = ScriptedDecider(cassette_from_transcript("gemini", "g", _fake_transcript()), strict=True)
    assert dec.decide(OBS1, None, None).tool == "click"
    assert dec.decide(OBS2, None, None).tool == "done"


def test_non_strict_cassette_ignores_observation_drift() -> None:
    dec = ScriptedDecider(cassette_from_transcript("gemini", "g", _fake_transcript()))
    assert dec.decide("drifted", None, None).tool == "click"


def test_strict_cassette_tolerates_turns_without_a_recorded_hash() -> None:
    dec = ScriptedDecider({"turns": [{"tool": "done", "args": {}}]}, strict=True)
    assert dec.model == "cassette"
    assert dec.decide("whatever", None, None).tool == "done"
