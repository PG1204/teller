"""Risk classification of a proposed action.

Rule-based from the policy's patterns plus the step's own declaration; the stricter of the
two wins. Patterns are deliberately conservative: a *submitting* click on a route listed as
irreversible, a control whose text matches the irreversible list, or accepting a dialog whose
text matches, is ``irreversible_write``. Navigation links to those routes stay ``read`` —
looking at the "Post Transaction" form is fine, pressing Post is not.

Limits (stated in REPORT §6): pattern matching can miss an innocuously labelled destructive
control; ``explicit_elements`` exists for that, and ``status: approved`` is a human review.
"""

from __future__ import annotations

import re
from urllib.parse import urlsplit

from teller.artifact.model import RISK_ORDER, RiskClass
from teller.policy.model import Policy, RiskPatterns
from teller.surface.base import Action, Element


def stricter(a: RiskClass, b: RiskClass) -> RiskClass:
    return a if RISK_ORDER[a] >= RISK_ORDER[b] else b


class RiskClassifier:
    def __init__(self, policy: Policy):
        self.policy = policy
        self._irr = self._compile(policy.risk.irreversible)
        self._rev = self._compile(policy.risk.reversible)

    @staticmethod
    def _compile(p: RiskPatterns) -> dict[str, list[re.Pattern[str]]]:
        return {
            "controls": [re.compile(r) for r in p.controls],
            "dialog_text": [re.compile(r) for r in p.dialog_text],
        }

    @staticmethod
    def _route_match(path: str, pattern: str) -> bool:
        rx = re.escape(pattern).replace(r"\*\*", ".*").replace(r"\*", "[^/]*")
        return re.fullmatch(rx, path) is not None

    def _control_text(self, el: Element) -> str:
        return " | ".join(
            x for x in (el.name, el.text, el.attrs.get("value", ""), el.attrs.get("alt", "")) if x
        )

    def classify(
        self,
        action: Action,
        element: Element | None,
        current_url: str,
        dialog_text: str | None = None,
        declared: RiskClass = "read",
    ) -> tuple[RiskClass, str]:
        """Return (risk_class, reason)."""
        computed: RiskClass = "read"
        reason = "read-only action"
        path = urlsplit(current_url).path or "/"

        if action.kind in ("read", "scroll", "navigate", "press", "select", "type"):
            computed, reason = "read", f"{action.kind} is non-committing"
        elif action.kind == "dismiss_dialog":
            if action.accept and dialog_text:
                if any(rx.search(dialog_text) for rx in self._irr["dialog_text"]):
                    computed, reason = "irreversible_write", "accepting a dialog that matches irreversible text"
                else:
                    computed, reason = "reversible_write", "accepting a confirmation dialog"
            else:
                computed, reason = "read", "dismissing a dialog"
        elif action.kind == "click" and element is not None:
            text = self._control_text(element)
            explicit = self._explicit(element, path)
            if explicit is not None:
                computed, reason = explicit, "tenant-listed explicit element"
            elif any(rx.search(text) for rx in self._irr["controls"]):
                computed, reason = "irreversible_write", f"control text {text!r} matches irreversible pattern"
            elif element.is_submit and any(
                self._route_match(path, r) for r in self.policy.risk.irreversible.submit_routes
            ):
                computed, reason = "irreversible_write", f"submit on irreversible route {path}"
            elif any(rx.search(text) for rx in self._rev["controls"]) and element.is_submit:
                computed, reason = "reversible_write", f"submit control {text!r} (reversible pattern)"
            elif element.is_submit and any(
                self._route_match(path, r) for r in self.policy.risk.reversible.submit_routes
            ):
                computed, reason = "reversible_write", f"submit on reversible route {path}"
            elif element.is_submit:
                computed, reason = "reversible_write", "form submission with no matching pattern"
            else:
                computed, reason = "read", "click on a non-submitting control"

        final = stricter(computed, declared)
        if final != computed:
            reason = f"step declares {declared} (stricter than computed {computed}: {reason})"
        return final, reason

    def _explicit(self, el: Element, path: str) -> RiskClass | None:
        for e in self.policy.risk.explicit_elements:
            if e.text.lower() in (el.name.lower(), el.text.lower()) and (
                e.route is None or self._route_match(path, e.route)
            ):
                return e.risk_class
        return None
