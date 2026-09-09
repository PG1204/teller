"""Architectural guards: the boundaries the design promises are enforced by import scans."""

from __future__ import annotations

import ast
from pathlib import Path

SRC = Path(__file__).resolve().parents[2] / "src" / "teller"


def _imports(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names |= {a.name.split(".")[0] for a in node.names}
        elif isinstance(node, ast.ImportFrom) and node.module:
            names.add(node.module.split(".")[0])
    return names


def _package_imports(pkg: str) -> dict[str, set[str]]:
    return {str(p.relative_to(SRC)): _imports(p) for p in (SRC / pkg).rglob("*.py")}


def test_replay_never_imports_an_llm_sdk() -> None:
    for file, names in _package_imports("replay").items():
        assert not ({"anthropic", "google"} & names), f"{file} imports an LLM SDK"


def test_only_the_surface_package_imports_playwright() -> None:
    for p in SRC.rglob("*.py"):
        rel = p.relative_to(SRC)
        if rel.parts[0] == "surface":
            continue
        assert "playwright" not in _imports(p), f"{rel} imports playwright outside surface/"


def test_hitl_never_imports_playwright_or_llm_sdks() -> None:
    for file, names in _package_imports("hitl").items():
        assert not ({"playwright", "anthropic", "google"} & names), f"{file} crosses a boundary"


def test_policy_and_artifact_are_pure() -> None:
    for pkg in ("policy", "artifact"):
        for file, names in _package_imports(pkg).items():
            assert not ({"playwright", "anthropic", "google", "httpx"} & names), f"{file} crosses a boundary"
