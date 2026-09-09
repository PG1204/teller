"""The handoff command channel: an append-only ``commands.jsonl`` in the run directory.

Why files: sync Playwright dispatches page events only while the main thread is inside a
Playwright call, so the automation thread must *poll* (inside ``page.wait_for_timeout``) rather
than block on a threading primitive. A JSONL file is trivially inspectable, works across
processes (the operator CLI, the web console, a test), and is the same seam a queue or a DB row
would occupy later.
"""

from __future__ import annotations

import datetime as dt
import json
import threading
from pathlib import Path
from typing import Any

COMMANDS = ("claim", "resume", "abort", "confirm", "decline", "dialog_accept", "dialog_dismiss")
RESUME_MODES = ("retry_step", "skip_step", "complete")

_lock = threading.Lock()


def commands_path(run_dir: Path) -> Path:
    return Path(run_dir) / "commands.jsonl"


def append_command(
    run_dir: Path,
    *,
    intervention_id: str,
    command: str,
    operator: str,
    mode: str | None = None,
    note: str | None = None,
) -> dict[str, Any]:
    if command not in COMMANDS:
        raise ValueError(f"unknown command {command!r}; expected one of {COMMANDS}")
    if command == "resume" and mode not in RESUME_MODES:
        raise ValueError(f"resume needs mode in {RESUME_MODES}, got {mode!r}")
    cmd = {
        "ts": dt.datetime.now(dt.UTC).replace(microsecond=0).isoformat(),
        "intervention_id": intervention_id,
        "command": command,
        "mode": mode,
        "note": note,
        "operator": operator,
    }
    path = commands_path(run_dir)
    with _lock:
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps({k: v for k, v in cmd.items() if v is not None}) + "\n")
    return cmd


def read_commands(run_dir: Path, offset: int = 0) -> tuple[list[dict[str, Any]], int]:
    """Return commands appended after byte ``offset`` and the new offset."""
    path = commands_path(run_dir)
    if not path.exists():
        return [], offset
    with path.open("rb") as fh:
        fh.seek(offset)
        chunk = fh.read()
    out: list[dict[str, Any]] = []
    consumed = 0
    for line in chunk.split(b"\n"):
        if not line.strip():
            consumed += len(line) + 1 if line or consumed < len(chunk) else 0
            continue
        try:
            out.append(json.loads(line))
        except json.JSONDecodeError:
            break  # partial line still being written; re-read next poll
        consumed += len(line) + 1
    return out, offset + min(consumed, len(chunk))


def read_intervention(run_dir: Path) -> dict[str, Any] | None:
    p = Path(run_dir) / "intervention.json"
    if not p.exists():
        return None
    return json.loads(p.read_text(encoding="utf-8"))


def read_state(run_dir: Path) -> dict[str, Any] | None:
    p = Path(run_dir) / "state.json"
    if not p.exists():
        return None
    return json.loads(p.read_text(encoding="utf-8"))
