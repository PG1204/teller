"""Redactor — the only writer path for anything that lands on disk or leaves the process.

Three sources of sensitivity, applied in this order to any string (recursively through dicts,
lists and pydantic models):

1. **Known secret values** (resolved from the environment names listed in the policy, plus
   anything registered at runtime) -> ``<secret>``. Plain and URL-encoded forms.
2. **Classified values** (params/outputs marked ``pii_high``) -> ``****<last4>``.
3. **Policy regexes** (SSN, card, account, email…) -> ``<ssn>``, ``<card>``, …

The same object also produces the list of CSS selectors to mask in screenshots and the
regexes ``marks.js`` uses to wrap matching text nodes so they can be masked at capture time.
"""

from __future__ import annotations

import os
import re
from typing import Any
from urllib.parse import quote

from pydantic import BaseModel

from teller.artifact.model import Capability, Classification
from teller.policy.model import Policy


def mask_last4(value: str) -> str:
    v = str(value)
    return "****" + v[-4:] if len(v) > 4 else "****"


class Redactor:
    def __init__(
        self,
        policy: Policy,
        *,
        sensitive_selectors: list[str] | None = None,
        env: dict[str, str] | None = None,
    ):
        self.policy = policy
        env = env if env is not None else dict(os.environ)
        self._secrets: dict[str, str] = {}
        for name in policy.redaction.known_secret_env:
            val = env.get(name)
            if val:
                self.register_secret(val)
        self._classified: dict[str, str] = {}  # value -> replacement
        self._regexes: list[tuple[str, re.Pattern[str]]] = [
            (name, re.compile(rx)) for name, rx in policy.redaction.regexes.items()
        ]
        self._selectors = list(sensitive_selectors or [])
        self._masked_words: set[str] = set()

    # ---- registration --------------------------------------------------------------------

    def register_secret(self, value: str) -> None:
        if not value or len(value) < 3:
            return
        self._secrets[value] = "<secret>"
        enc = quote(value, safe="")
        if enc != value:
            self._secrets[enc] = "<secret>"

    def register_value(self, value: str, classification: Classification) -> None:
        """Register a concrete param/output value so its literal never persists."""
        if not value:
            return
        if classification == "secret":
            self.register_secret(value)
        elif classification == "pii_high":
            self._classified[str(value)] = mask_last4(value)

    def register_params(self, cap: Capability, params: dict[str, str]) -> None:
        for name, spec in cap.params.items():
            if name in params:
                self.register_value(str(params[name]), spec.classification)

    def register_output(self, cap: Capability, name: str, value: str) -> None:
        spec = cap.outputs.get(name)
        if spec:
            self.register_value(value, spec.classification)

    # ---- scrubbing ------------------------------------------------------------------------

    def scrub_text(self, text: str) -> str:
        if not text:
            return text
        out = text
        for val, rep in sorted(self._secrets.items(), key=lambda kv: -len(kv[0])):
            out = out.replace(val, rep)
        for val, rep in sorted(self._classified.items(), key=lambda kv: -len(kv[0])):
            out = out.replace(val, rep)
        for name, rx in self._regexes:
            out = rx.sub(f"<{name}>", out)
        return out

    def scrub(self, obj: Any) -> Any:
        if obj is None or isinstance(obj, (bool, int, float)):
            return obj
        if isinstance(obj, str):
            return self.scrub_text(obj)
        if isinstance(obj, bytes):
            return obj  # binary (screenshots) is redacted at capture, not here
        if isinstance(obj, BaseModel):
            return self.scrub(obj.model_dump(mode="json"))
        if isinstance(obj, dict):
            return {k: self.scrub(v) for k, v in obj.items()}
        if isinstance(obj, (list, tuple)):
            return [self.scrub(v) for v in obj]
        return self.scrub_text(str(obj))

    def masked_params(self, cap: Capability, params: dict[str, str]) -> dict[str, str]:
        out: dict[str, str] = {}
        for k, v in params.items():
            spec = cap.params.get(k)
            cls = spec.classification if spec else "none"
            if cls == "secret":
                out[k] = "<secret>"
            elif cls == "pii_high":
                out[k] = mask_last4(v)
            else:
                out[k] = v
        return out

    def masked_params_raw(self, params: dict[str, str], decls: dict[str, Any]) -> dict[str, str]:
        """Mask by classification when only declarations (not a Capability) are available."""
        out: dict[str, str] = {}
        for k, v in params.items():
            cls = getattr(decls.get(k), "classification", "none") if decls else "none"
            out[k] = "<secret>" if cls == "secret" else mask_last4(v) if cls == "pii_high" else v
        return out

    def masked_outputs(self, cap: Capability, outputs: dict[str, str]) -> dict[str, str]:
        out: dict[str, str] = {}
        for k, v in outputs.items():
            spec = cap.outputs.get(k)
            cls = spec.classification if spec else "none"
            out[k] = mask_last4(v) if cls == "pii_high" else ("<secret>" if cls == "secret" else v)
        return out

    # ---- for screenshots / DOM --------------------------------------------------------------

    def mask_selectors(self) -> list[str]:
        return ["input[type=password]", "[data-teller-mask]", *self._selectors]

    def text_regexes(self) -> list[str]:
        """Regexes handed to marks.js to wrap matching text nodes for masking."""
        return [rx.pattern for _, rx in self._regexes]

    def sensitive_literals(self) -> list[str]:
        """Concrete values marks.js should also wrap (classified values seen this run)."""
        return list(self._classified) + list(self._secrets)
