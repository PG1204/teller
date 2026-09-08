"""Unit tests for the JSONL event log."""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from teller.evidence.log import EventLog, new_run_id


def _redact(obj: Any) -> Any:
    if isinstance(obj, str):
        return obj.replace("SECRET", "<secret>")
    if isinstance(obj, dict):
        return {k: _redact(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_redact(v) for v in obj]
    return obj


def _lines(log: EventLog) -> list[dict[str, Any]]:
    text = log.path.read_text(encoding="utf-8")
    assert text.endswith("\n")
    return [json.loads(line) for line in text.splitlines()]


def test_emit_writes_one_json_line_per_event(tmp_path: Path) -> None:
    log = EventLog(tmp_path / "run", "run_1", "replay", echo=False)
    log.emit("run.start", {"a": 1})
    log.emit("act", {"kind": "click"}, step_id="s1")
    log.emit("run.end")
    lines = _lines(log)
    assert [ln["type"] for ln in lines] == ["run.start", "act", "run.end"]
    assert log.path == tmp_path / "run" / "events.jsonl"


def test_event_carries_required_fields(tmp_path: Path) -> None:
    log = EventLog(tmp_path, "run_1", "discovery", echo=False)
    log.emit("observe", {"url": "/console"}, step_id="s2", actor="model")
    (line,) = _lines(log)
    assert line["run_id"] == "run_1"
    assert line["mode"] == "discovery"
    assert line["controller"] == "automation"
    assert line["type"] == "observe"
    assert line["step_id"] == "s2"
    assert line["actor"] == "model"
    assert line["payload"] == {"url": "/console"}
    assert re.fullmatch(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\+00:00", line["ts"])


def test_step_id_omitted_when_none(tmp_path: Path) -> None:
    log = EventLog(tmp_path, "run_1", "replay", echo=False)
    log.emit("run.start")
    (line,) = _lines(log)
    assert "step_id" not in line
    assert line["payload"] == {}


def test_set_controller_reflected_on_later_events(tmp_path: Path) -> None:
    log = EventLog(tmp_path, "run_1", "replay", echo=False)
    log.emit("handoff.requested")
    log.set_controller("human")
    log.emit("human_action")
    log.set_controller("automation")
    log.emit("handoff.released")
    assert [ln["controller"] for ln in _lines(log)] == ["automation", "human", "automation"]


def test_redact_hook_applied_to_payload(tmp_path: Path) -> None:
    log = EventLog(tmp_path, "run_1", "replay", redact=_redact, echo=False)
    ev = log.emit("act", {"value": "my SECRET", "nested": {"list": ["SECRET"]}})
    assert ev.payload == {"value": "my <secret>", "nested": {"list": ["<secret>"]}}
    (line,) = _lines(log)
    assert "SECRET" not in json.dumps(line)


def test_tail_returns_last_n_events(tmp_path: Path) -> None:
    log = EventLog(tmp_path, "run_1", "replay", echo=False)
    for i in range(5):
        log.emit("wait", {"i": i})
    tail = log.tail(2)
    assert [t["payload"]["i"] for t in tail] == [3, 4]
    assert len(log.tail(10)) == 5


def test_emit_returns_event_and_appends_to_memory(tmp_path: Path) -> None:
    log = EventLog(tmp_path, "run_1", "replay", echo=False)
    ev = log.emit("checkpoint", {"ok": True})
    assert log.events == [ev]
    assert ev.type == "checkpoint"


def test_new_run_id_prefix_and_uniqueness() -> None:
    a = new_run_id()
    b = new_run_id()
    pattern = re.compile(r"^run_\d{4}-\d{2}-\d{2}T\d{2}-\d{2}-\d{2}_[0-9a-f]{4}$")
    assert pattern.fullmatch(a), a
    assert pattern.fullmatch(b), b
    assert a != b


def test_new_run_id_custom_prefix() -> None:
    assert new_run_id("disc").startswith("disc_")
