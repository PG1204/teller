"""Drift guard: the committed JSON Schemas must match what the CLI generates."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from typer.testing import CliRunner

from teller.cli import app

EXPECTED_FILES = {
    "capability.schema.json",
    "app_profile.schema.json",
    "tenant.schema.json",
    "policy.schema.json",
    "result.schema.json",
}


@pytest.fixture(scope="module")
def regenerated(tmp_path_factory: pytest.TempPathFactory) -> Path:
    out = tmp_path_factory.mktemp("schema")
    result = CliRunner().invoke(app, ["schema", "export", "--to", str(out)])
    assert result.exit_code == 0, result.output
    return out


def test_export_writes_expected_files(regenerated: Path) -> None:
    assert {p.name for p in regenerated.glob("*.schema.json")} == EXPECTED_FILES


def test_committed_schema_dir_has_expected_files(repo_root: Path) -> None:
    assert {p.name for p in (repo_root / "schema").glob("*.schema.json")} == EXPECTED_FILES


@pytest.mark.parametrize("name", sorted(EXPECTED_FILES))
def test_committed_schema_matches_regenerated(
    repo_root: Path, regenerated: Path, name: str
) -> None:
    committed = (repo_root / "schema" / name).read_bytes()
    fresh = (regenerated / name).read_bytes()
    assert committed == fresh, (
        f"schema/{name} is stale: run `teller schema export --to schema/` and commit"
    )


def test_capability_schema_shape(repo_root: Path) -> None:
    schema = json.loads((repo_root / "schema" / "capability.schema.json").read_text("utf-8"))
    assert schema["$schema"] == "https://json-schema.org/draft/2020-12/schema"
    assert schema["$id"].endswith("/capability.schema.json")
    assert "steps" in schema["properties"]
    assert schema["additionalProperties"] is False


def test_every_schema_has_id_matching_filename(repo_root: Path) -> None:
    for name in EXPECTED_FILES:
        schema = json.loads((repo_root / "schema" / name).read_text("utf-8"))
        assert schema["$id"] == f"https://github.com/teller/schema/{name}"
        assert "$schema" in schema
