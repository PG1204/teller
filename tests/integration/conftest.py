"""Integration fixtures: a live mock console on a free port and a tenant pointing at it."""

from __future__ import annotations

import os
import socket
import subprocess
import sys
import time
from collections.abc import Iterator
from pathlib import Path

import httpx
import pytest
import yaml

ROOT = Path(__file__).resolve().parents[2]


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@pytest.fixture(scope="session")
def mock_url() -> Iterator[str]:
    port = _free_port()
    env = {**os.environ, "LEDGERLINE_USER": "teller1", "LEDGERLINE_PASS": "Ledger!2026"}
    proc = subprocess.Popen(
        [sys.executable, "-m", "uvicorn", "mockapp.app:app", "--port", str(port), "--log-level", "warning"],
        cwd=ROOT, env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )
    url = f"http://127.0.0.1:{port}"
    for _ in range(100):
        try:
            if httpx.get(url + "/login", timeout=1).status_code == 200:
                break
        except httpx.HTTPError:
            time.sleep(0.1)
    else:
        proc.kill()
        raise RuntimeError("mock console did not start")
    os.environ.setdefault("LEDGERLINE_USER", "teller1")
    os.environ.setdefault("LEDGERLINE_PASS", "Ledger!2026")
    yield url
    proc.terminate()
    proc.wait(timeout=5)


@pytest.fixture()
def workdir(tmp_path: Path, mock_url: str) -> Path:
    """A repo-shaped working directory whose local tenant points at the test mock."""
    for d in ("apps", "policies", "tenants", "tests/fixtures"):
        src = ROOT / d
        dst = tmp_path / d
        dst.mkdir(parents=True, exist_ok=True)
        for f in src.rglob("*"):
            if f.is_file():
                rel = f.relative_to(src)
                (dst / rel).parent.mkdir(parents=True, exist_ok=True)
                (dst / rel).write_bytes(f.read_bytes())
    tp = tmp_path / "tenants" / "local.yaml"
    data = yaml.safe_load(tp.read_text())
    data["base_url"] = mock_url
    tp.write_text(yaml.safe_dump(data))
    pol = tmp_path / "policies" / "ledgerline.yaml"
    pdata = yaml.safe_load(pol.read_text())
    pdata["origins"] = [mock_url]
    pol.write_text(yaml.safe_dump(pdata))
    (tmp_path / "capabilities").mkdir()
    (tmp_path / "runs").mkdir()
    return tmp_path


def chaos(mock_url: str, mode: str, times: int = 1) -> None:
    httpx.post(mock_url + "/__chaos", json={"mode": mode, "times": times}, timeout=5).raise_for_status()


def chaos_reset(mock_url: str) -> None:
    httpx.post(mock_url + "/__chaos/reset", timeout=5).raise_for_status()
