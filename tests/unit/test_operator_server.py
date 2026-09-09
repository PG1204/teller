"""Unit tests for the file-backed operator console (``teller.hitl.operator_server``)."""

from __future__ import annotations

import json
from collections.abc import Iterator
from pathlib import Path

import httpx
import pytest

from teller.hitl.commands import commands_path, read_commands
from teller.hitl.operator_server import OperatorServer
from teller.hitl.state import ControlState, InterventionRequest

INTERVENTION_ID = "int_feed"


def _write_pending(run_dir: Path, intervention_id: str = INTERVENTION_ID) -> None:
    """Write intervention.json + state.json the way the automation thread would."""
    cs = ControlState(run_dir=run_dir)
    cs.raise_intervention(
        InterventionRequest(
            intervention_id=intervention_id,
            run_id="run_test",
            mode="replay",
            trigger="resolution_failed",
            reason="Go button not found",
            screenshot="screenshots/handoff.jpg",
        )
    )


@pytest.fixture
def server(tmp_path: Path) -> Iterator[tuple[str, Path]]:
    run_dir = tmp_path / "run"
    run_dir.mkdir()
    srv = OperatorServer(run_dir)
    url = srv.start()
    try:
        yield url, run_dir
    finally:
        srv.stop()


def _lines(run_dir: Path) -> list[str]:
    p = commands_path(run_dir)
    return p.read_text().splitlines() if p.exists() else []


def test_start_returns_a_bound_url(server: tuple[str, Path]) -> None:
    url, _ = server
    assert url.startswith("http://127.0.0.1:")
    assert int(url.rsplit(":", 1)[1]) >= 8787


def test_second_server_picks_another_port(server: tuple[str, Path], tmp_path: Path) -> None:
    url, _ = server
    other = OperatorServer(tmp_path / "other")
    try:
        other_url = other.start()
    finally:
        other.stop()
    assert other_url != url
    assert httpx.get(url + "/").status_code == 200


def test_get_root_serves_console_html(server: tuple[str, Path]) -> None:
    url, _ = server
    r = httpx.get(url + "/")
    assert r.status_code == 200
    assert r.headers["content-type"].startswith("text/html")
    assert "operator console" in r.text.lower()
    assert httpx.get(url + "/interventions/" + INTERVENTION_ID).status_code == 200


def test_get_current_json_without_files_is_empty(server: tuple[str, Path]) -> None:
    url, _ = server
    r = httpx.get(url + "/interventions/current.json")
    assert r.status_code == 200
    body = r.json()
    assert body["intervention"] == {}
    assert body["state"] == {"state": None, "controller": None, "handoffs": None}
    assert body["commands"] == []


def test_get_current_json_reflects_pending_intervention(server: tuple[str, Path]) -> None:
    url, run_dir = server
    _write_pending(run_dir)
    r = httpx.get(url + "/interventions/current.json")
    assert r.status_code == 200
    body = r.json()
    assert body["intervention"]["intervention_id"] == INTERVENTION_ID
    assert body["intervention"]["reason"] == "Go button not found"
    assert body["state"] == {"state": "AWAITING_HUMAN", "controller": "none", "handoffs": 0}


def test_screenshot_404_then_200_after_live_frame(server: tuple[str, Path]) -> None:
    url, run_dir = server
    assert httpx.get(url + "/screenshot").status_code == 404
    (run_dir / "screenshots").mkdir()
    (run_dir / "screenshots" / "handoff_live.jpg").write_bytes(b"\xff\xd8fake-jpeg")
    r = httpx.get(url + "/screenshot")
    assert r.status_code == 200
    assert r.headers["content-type"] == "image/jpeg"
    assert r.content == b"\xff\xd8fake-jpeg"


def test_screenshot_falls_back_to_the_intervention_screenshot(server: tuple[str, Path]) -> None:
    url, run_dir = server
    _write_pending(run_dir)
    assert httpx.get(url + "/screenshot").status_code == 404
    (run_dir / "screenshots").mkdir()
    (run_dir / "screenshots" / "handoff.jpg").write_bytes(b"still")
    r = httpx.get(url + "/screenshot")
    assert r.status_code == 200
    assert r.content == b"still"


def test_events_endpoint_returns_last_eight(server: tuple[str, Path]) -> None:
    url, run_dir = server
    assert httpx.get(url + "/events").json() == []
    (run_dir / "events.jsonl").write_text(
        "".join(json.dumps({"seq": i}) + "\n" for i in range(12)), encoding="utf-8"
    )
    body = httpx.get(url + "/events").json()
    assert [e["seq"] for e in body] == list(range(4, 12))


def test_get_unknown_path_is_404(server: tuple[str, Path]) -> None:
    url, _ = server
    assert httpx.get(url + "/nope").status_code == 404


def test_post_claim_appends_a_command_line(server: tuple[str, Path]) -> None:
    url, run_dir = server
    _write_pending(run_dir)
    r = httpx.post(url + f"/interventions/{INTERVENTION_ID}/claim", json={"operator": "alex"})
    assert r.status_code == 200
    assert r.json()["command"] == "claim"
    lines = _lines(run_dir)
    assert len(lines) == 1
    row = json.loads(lines[0])
    assert row["command"] == "claim"
    assert row["operator"] == "alex"
    assert row["intervention_id"] == INTERVENTION_ID
    cmds, _ = read_commands(run_dir)
    assert cmds == [row]


def test_post_form_encoded_body_is_accepted(server: tuple[str, Path]) -> None:
    url, run_dir = server
    _write_pending(run_dir)
    r = httpx.post(
        url + f"/interventions/{INTERVENTION_ID}/resume",
        data={"mode": "complete", "operator": "sam", "note": "done"},
    )
    assert r.status_code == 200
    row = json.loads(_lines(run_dir)[0])
    assert row["mode"] == "complete"
    assert row["operator"] == "sam"
    assert row["note"] == "done"


def test_post_with_wrong_id_is_409_and_writes_nothing(server: tuple[str, Path]) -> None:
    url, run_dir = server
    _write_pending(run_dir)
    r = httpx.post(url + "/interventions/int_stale/claim", json={"operator": "alex"})
    assert r.status_code == 409
    assert "stale" in r.json()["error"]
    assert _lines(run_dir) == []


def test_post_without_any_intervention_is_409(server: tuple[str, Path]) -> None:
    url, run_dir = server
    r = httpx.post(url + f"/interventions/{INTERVENTION_ID}/claim", json={"operator": "alex"})
    assert r.status_code == 409
    assert _lines(run_dir) == []


def test_post_to_released_intervention_is_409(server: tuple[str, Path]) -> None:
    url, run_dir = server
    _write_pending(run_dir)
    p = run_dir / "intervention.json"
    data = json.loads(p.read_text())
    data["released_at"] = "2026-09-08T00:00:00+00:00"
    p.write_text(json.dumps(data))
    r = httpx.post(url + f"/interventions/{INTERVENTION_ID}/claim", json={"operator": "alex"})
    assert r.status_code == 409
    assert _lines(run_dir) == []


def test_post_resume_with_bogus_mode_is_400(server: tuple[str, Path]) -> None:
    url, run_dir = server
    _write_pending(run_dir)
    r = httpx.post(
        url + f"/interventions/{INTERVENTION_ID}/resume",
        json={"operator": "alex", "mode": "bogus"},
    )
    assert r.status_code == 400
    assert "mode must be one of" in r.json()["error"]
    assert _lines(run_dir) == []


def test_post_resume_without_mode_is_400(server: tuple[str, Path]) -> None:
    url, run_dir = server
    _write_pending(run_dir)
    r = httpx.post(url + f"/interventions/{INTERVENTION_ID}/resume", json={"operator": "alex"})
    assert r.status_code == 400
    assert _lines(run_dir) == []


def test_post_resume_retry_step_is_200(server: tuple[str, Path]) -> None:
    url, run_dir = server
    _write_pending(run_dir)
    r = httpx.post(
        url + f"/interventions/{INTERVENTION_ID}/resume",
        json={"operator": "alex", "mode": "retry_step"},
    )
    assert r.status_code == 200
    assert r.json()["mode"] == "retry_step"
    row = json.loads(_lines(run_dir)[0])
    assert row["command"] == "resume"
    assert row["mode"] == "retry_step"


def test_post_unknown_command_is_400(server: tuple[str, Path]) -> None:
    url, run_dir = server
    _write_pending(run_dir)
    r = httpx.post(url + f"/interventions/{INTERVENTION_ID}/reboot", json={"operator": "alex"})
    assert r.status_code == 400
    assert "unknown command" in r.json()["error"]
    assert _lines(run_dir) == []


def test_post_to_unknown_path_is_404(server: tuple[str, Path]) -> None:
    url, run_dir = server
    _write_pending(run_dir)
    assert httpx.post(url + "/nope", json={}).status_code == 404
    assert httpx.post(url + f"/interventions/{INTERVENTION_ID}", json={}).status_code == 404
    assert httpx.post(url + "/other/a/b", json={}).status_code == 404
    assert _lines(run_dir) == []


def test_commands_show_up_in_current_json(server: tuple[str, Path]) -> None:
    url, run_dir = server
    _write_pending(run_dir)
    httpx.post(url + f"/interventions/{INTERVENTION_ID}/claim", json={"operator": "alex"})
    body = httpx.get(url + "/interventions/current.json").json()
    assert [c["command"] for c in body["commands"]] == ["claim"]


def test_stop_is_idempotent(tmp_path: Path) -> None:
    srv = OperatorServer(tmp_path)
    url = srv.start()
    assert httpx.get(url + "/").status_code == 200
    srv.stop()
    srv.stop()
    with pytest.raises(httpx.ConnectError):
        httpx.get(url + "/", timeout=1.0)
