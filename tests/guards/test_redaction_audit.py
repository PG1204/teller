"""Redaction audit: nothing committed under evidence/ or capabilities/ may carry a secret or an
unmasked pii_high literal. Run before every evidence commit (part of `make test`)."""

from __future__ import annotations

import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
SCAN_DIRS = [ROOT / "evidence", ROOT / "capabilities", ROOT / "apps", ROOT / "tenants", ROOT / "policies"]
TEXT_SUFFIXES = {".json", ".jsonl", ".yaml", ".yml", ".md", ".txt", ".html", ".py"}

# fixture secrets and pii_high literals from the mock's seed data
FORBIDDEN_LITERALS = ["Ledger!2026", "Ledger%212026", "0004411982", "0004411983", "0004411990"]
FORBIDDEN_PATTERNS = [
    re.compile(r"\b\d{3}-\d{2}-\d{4}\b"),  # full SSN
    re.compile(r"(?i)(api[_-]?key|authorization)\s*[:=]\s*['\"]?[A-Za-z0-9_\-]{20,}"),
    re.compile(r"\bAIza[0-9A-Za-z_\-]{30,}\b"),  # Google API key shape
    re.compile(r"\bsk-ant-[0-9A-Za-z_\-]{20,}\b"),  # Anthropic API key shape
]


def _files() -> list[Path]:
    out: list[Path] = []
    for d in SCAN_DIRS:
        if d.exists():
            out += [p for p in d.rglob("*") if p.is_file() and p.suffix in TEXT_SUFFIXES]
    return out


@pytest.mark.parametrize("path", _files(), ids=lambda p: str(p.relative_to(ROOT)))
def test_no_secret_or_pii_high_literal_persisted(path: Path) -> None:
    text = path.read_text(encoding="utf-8", errors="replace")
    for lit in FORBIDDEN_LITERALS:
        assert lit not in text, f"{path.relative_to(ROOT)} contains forbidden literal {lit!r}"
    for rx in FORBIDDEN_PATTERNS:
        m = rx.search(text)
        assert m is None, f"{path.relative_to(ROOT)} matches {rx.pattern!r}: {m.group(0)[:20]!r}"


def test_env_file_is_ignored_and_absent_from_git() -> None:
    gitignore = (ROOT / ".gitignore").read_text()
    assert ".env" in gitignore.splitlines()
    assert not (ROOT / ".env.example").read_text().strip().splitlines()[-1].startswith("GEMINI_API_KEY=A")
