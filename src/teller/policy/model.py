"""Policy file schema (``policies/<name>.yaml``).

The policy is the explicit, configurable allowlist the brief asks for, plus the rules that
classify actions into risk classes and the redaction configuration. It is loaded once per run,
its sha256 is written into every result, and it is enforced by ``PolicyGate`` at the single
call site inside ``Surface.act`` — for discovery and replay alike.
"""

from __future__ import annotations

import hashlib
import re
from pathlib import Path
from typing import Literal
from urllib.parse import urlsplit

import yaml
from pydantic import BaseModel, ConfigDict, Field, model_validator

from teller.artifact.model import ActionKind, RiskClass


class Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


def _check_regex(pattern: str, where: str) -> None:
    try:
        re.compile(pattern)
    except re.error as e:
        raise ValueError(f"{where}: invalid regex {pattern!r}: {e}") from e


class RiskPatterns(Strict):
    """Regexes on control text (accessible name / visible text / value) and route globs that
    apply only to *submitting* clicks (form submit buttons), never to navigation links."""

    controls: list[str] = Field(default_factory=list)
    submit_routes: list[str] = Field(default_factory=list)
    dialog_text: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def _compile(self) -> RiskPatterns:
        for r in self.controls + self.dialog_text:
            _check_regex(r, "risk pattern")
        return self


class ExplicitElement(Strict):
    """Tenant-listed control that overrides the patterns."""

    text: str
    risk_class: RiskClass
    route: str | None = None


class RiskConfig(Strict):
    reversible: RiskPatterns = Field(default_factory=RiskPatterns)
    irreversible: RiskPatterns = Field(default_factory=RiskPatterns)
    explicit_elements: list[ExplicitElement] = Field(default_factory=list)


class RedactionConfig(Strict):
    regexes: dict[str, str] = Field(default_factory=dict)
    known_secret_env: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def _compile(self) -> RedactionConfig:
        for r in self.regexes.values():
            _check_regex(r, "redaction regex")
        return self


class Policy(Strict):
    schema_version: Literal[1] = 1
    origins: list[str] = Field(min_length=1, description="Allowed origins, scheme://host[:port].")
    routes_allow: list[str] = Field(default_factory=lambda: ["/**"])
    routes_deny: list[str] = Field(default_factory=list, description="Deny wins over allow.")
    actions_allow: list[ActionKind] = Field(min_length=1)
    navigate_allow_new_origins: bool = False
    downloads: bool = False
    popups: Literal["close_and_log", "block"] = "close_and_log"
    max_steps: int = Field(default=20, ge=1, le=200)
    max_run_seconds: int = Field(default=360, ge=10)
    risk: RiskConfig = Field(default_factory=RiskConfig)
    redaction: RedactionConfig = Field(default_factory=RedactionConfig)

    # ---- helpers --------------------------------------------------------------------------

    @staticmethod
    def _norm_origin(o: str) -> str:
        parts = urlsplit(o if "://" in o else f"http://{o}")
        return f"{parts.scheme}://{parts.netloc}".lower()

    def origin_allowed(self, url: str) -> bool:
        if url.startswith(("about:", "data:", "javascript:")):
            return url == "about:blank"
        try:
            origin = self._norm_origin(url)
        except ValueError:
            return False
        return origin in {self._norm_origin(o) for o in self.origins}

    @staticmethod
    def _route_match(path: str, pattern: str) -> bool:
        # '**' matches across '/', '*' matches within a segment.
        rx = re.escape(pattern).replace(r"\*\*", ".*").replace(r"\*", "[^/]*")
        return re.fullmatch(rx, path) is not None

    def route_allowed(self, url: str) -> bool:
        path = urlsplit(url).path or "/"
        if any(self._route_match(path, p) for p in self.routes_deny):
            return False
        return any(self._route_match(path, p) for p in self.routes_allow)

    def url_allowed(self, url: str) -> bool:
        return self.origin_allowed(url) and (url == "about:blank" or self.route_allowed(url))

    def action_allowed(self, kind: str) -> bool:
        return kind in self.actions_allow

    def sha256(self) -> str:
        blob = self.model_dump_json(exclude_none=True)
        return hashlib.sha256(blob.encode()).hexdigest()


def load_policy(path: str | Path) -> Policy:
    with Path(path).open("r", encoding="utf-8") as fh:
        return Policy.model_validate(yaml.safe_load(fh))
