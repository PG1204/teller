"""Unit tests for the JSONL command channel (``teller.hitl.commands``)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from teller.hitl.commands import (
    COMMANDS,
    RESUME_MODES,
    append_command,
    commands_path,
    read_commands,
    read_intervention,
    read_state,
)


def test_append_command_writes_exactly_one_json_line(tmp_path: Path) -> None:
    cmd = append_command(tmp_path, intervention_id="int_1", command="claim", operator="alex")
    raw = commands_path(tmp_path).read_bytes()
    assert raw.endswith(b"\n")
    lines = raw.decode().splitlines()
    assert len(lines) == 1
    row = json.loads(lines[0])
    assert row["intervention_id"] == "int_1"
    assert row["command"] == "claim"
    assert row["operator"] == "alex"
    assert row["ts"]
    # None-valued fields are dropped from the file but present on the returned dict
    assert "mode" not in row and "note" not in row
    assert cmd["mode"] is None and cmd["note"] is None


def test_append_command_creates_run_dir(tmp_path: Path) -> None:
    run_dir = tmp_path / "nested" / "run"
    append_command(run_dir, intervention_id="int_1", command="abort", operator="alex")
    assert commands_path(run_dir).exists()


def test_append_resume_keeps_mode_and_note(tmp_path: Path) -> None:
    append_command(
        tmp_path,
        intervention_id="int_1",
        command="resume",
        operator="alex",
        mode="skip_step",
        note="typed it by hand",
    )
    row = json.loads(commands_path(tmp_path).read_text().splitlines()[0])
    assert row["mode"] == "skip_step"
    assert row["note"] == "typed it by hand"


def test_unknown_command_raises_and_writes_nothing(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="unknown command 'reboot'"):
        append_command(tmp_path, intervention_id="int_1", command="reboot", operator="alex")
    assert not commands_path(tmp_path).exists()


@pytest.mark.parametrize("mode", [None, "bogus", "retry", ""])
def test_resume_requires_a_valid_mode(tmp_path: Path, mode: str | None) -> None:
    with pytest.raises(ValueError, match="resume needs mode"):
        append_command(
            tmp_path, intervention_id="int_1", command="resume", operator="alex", mode=mode
        )
    assert not commands_path(tmp_path).exists()


@pytest.mark.parametrize("mode", RESUME_MODES)
def test_every_resume_mode_is_accepted(tmp_path: Path, mode: str) -> None:
    cmd = append_command(
        tmp_path, intervention_id="int_1", command="resume", operator="alex", mode=mode
    )
    assert cmd["mode"] == mode


@pytest.mark.parametrize("command", [c for c in COMMANDS if c != "resume"])
def test_every_non_resume_command_is_accepted_without_mode(tmp_path: Path, command: str) -> None:
    cmd = append_command(tmp_path, intervention_id="int_1", command=command, operator="alex")
    assert cmd["command"] == command


def test_read_commands_on_missing_file_returns_empty_and_same_offset(tmp_path: Path) -> None:
    assert read_commands(tmp_path) == ([], 0)
    assert read_commands(tmp_path, offset=17) == ([], 17)


def test_read_commands_returns_only_new_lines_across_polls(tmp_path: Path) -> None:
    offset = 0
    seen: list[str] = []
    for command in ("claim", "confirm", "abort"):
        append_command(tmp_path, intervention_id="int_1", command=command, operator="alex")
        cmds, offset = read_commands(tmp_path, offset)
        assert [c["command"] for c in cmds] == [command]
        seen.append(command)
        assert offset == commands_path(tmp_path).stat().st_size
    # nothing new: empty list, offset unchanged
    again, offset2 = read_commands(tmp_path, offset)
    assert again == []
    assert offset2 == offset
    # from zero, everything comes back in order
    all_cmds, _ = read_commands(tmp_path, 0)
    assert [c["command"] for c in all_cmds] == seen


def test_partial_last_line_is_held_back_until_completed(tmp_path: Path) -> None:
    append_command(tmp_path, intervention_id="int_1", command="claim", operator="alex")
    cmds, offset = read_commands(tmp_path)
    assert [c["command"] for c in cmds] == ["claim"]

    path = commands_path(tmp_path)
    with path.open("ab") as fh:
        fh.write(b'{"intervention_id": "int_1", "command": "abo')
    cmds, offset2 = read_commands(tmp_path, offset)
    assert cmds == []
    assert offset2 == offset  # the partial bytes are not consumed

    with path.open("ab") as fh:
        fh.write(b'rt", "operator": "alex"}\n')
    cmds, offset3 = read_commands(tmp_path, offset2)
    assert [c["command"] for c in cmds] == ["abort"]
    assert offset3 == path.stat().st_size


def test_read_commands_skips_blank_lines(tmp_path: Path) -> None:
    path = commands_path(tmp_path)
    path.write_bytes(b'{"command": "claim"}\n\n{"command": "abort"}\n')
    cmds, offset = read_commands(tmp_path)
    assert [c["command"] for c in cmds] == ["claim", "abort"]
    assert offset == path.stat().st_size


def test_read_intervention_and_state_return_none_when_absent(tmp_path: Path) -> None:
    assert read_intervention(tmp_path) is None
    assert read_state(tmp_path) is None


def test_read_intervention_and_state_parse_json(tmp_path: Path) -> None:
    (tmp_path / "intervention.json").write_text(json.dumps({"intervention_id": "int_9"}))
    (tmp_path / "state.json").write_text(json.dumps({"state": "AWAITING_HUMAN"}))
    assert read_intervention(tmp_path) == {"intervention_id": "int_9"}
    assert read_state(tmp_path) == {"state": "AWAITING_HUMAN"}
