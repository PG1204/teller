"""Shared fixtures for the schema/config unit tests."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
import yaml

REPO_ROOT = Path(__file__).resolve().parents[2]
FIXTURE_PATH = REPO_ROOT / "tests" / "fixtures" / "sample_capability.yaml"


@pytest.fixture
def repo_root() -> Path:
    return REPO_ROOT


@pytest.fixture
def fixture_path() -> Path:
    return FIXTURE_PATH


@pytest.fixture
def sample_raw() -> dict[str, Any]:
    """The sample capability as a fresh raw mapping (safe to mutate per test)."""
    return yaml.safe_load(FIXTURE_PATH.read_text(encoding="utf-8"))
