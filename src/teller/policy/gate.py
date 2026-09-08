"""PolicyGate — the single checkpoint every action passes before it reaches the browser.

Called from exactly one place (``Surface.act``) for discovery and replay alike. It answers:

1. Is the action *kind* allowed by the policy?
2. Are we currently on an allowed origin/route? (If the page drifted off the allowlist, refuse
   to act at all — the caller escalates.)
3. For ``navigate`` and for clicks on links with resolvable hrefs: is the destination allowed?
4. What is the risk class, and does it require a human decision?

It never performs the action and never talks to Playwright.
"""

from __future__ import annotations

from urllib.parse import urljoin

from teller.artifact.model import RiskClass
from teller.policy.model import Policy
from teller.policy.risk import RiskClassifier
from teller.surface.base import Action, Element, GateDecision


class PolicyGate:
    def __init__(self, policy: Policy):
        self.policy = policy
        self.risk = RiskClassifier(policy)

    def check(
        self,
        action: Action,
        element: Element | None,
        current_url: str,
        *,
        dialog_text: str | None = None,
        declared_risk: RiskClass = "read",
    ) -> GateDecision:
        p = self.policy
        if not p.action_allowed(action.kind):
            return GateDecision(
                allowed=False, risk_class="read", reason=f"action kind {action.kind!r} not in allowlist"
            )
        if not p.url_allowed(current_url):
            return GateDecision(
                allowed=False,
                risk_class="read",
                reason=f"current page {current_url!r} is outside the allowlist; refusing to act",
            )
        if action.kind == "navigate":
            dest = urljoin(current_url, action.url or "")
            if not p.url_allowed(dest):
                return GateDecision(
                    allowed=False, risk_class="read", reason=f"navigation to {dest!r} denied by policy"
                )
        if action.kind == "click" and element is not None:
            href = element.attrs.get("href", "")
            if href and not href.lower().startswith(("javascript:", "#")):
                dest = urljoin(current_url, href)
                if not p.url_allowed(dest):
                    return GateDecision(
                        allowed=False,
                        risk_class="read",
                        reason=f"link target {dest!r} denied by policy",
                    )
            if element.disabled:
                return GateDecision(
                    allowed=False, risk_class="read", reason="control is disabled"
                )

        risk, why = self.risk.classify(
            action, element, current_url, dialog_text=dialog_text, declared=declared_risk
        )
        if risk == "irreversible_write":
            return GateDecision(
                allowed=True,
                risk_class=risk,
                confirm_required=True,
                reason=f"irreversible: {why}",
            )
        return GateDecision(allowed=True, risk_class=risk, reason=why)
