"""Capability artifact schema.

A *capability* is the reusable, reviewable, agent-invocable artifact that a discovery run
emits and that replay executes without a model in the loop. Three layers compose one
runnable capability at load time (see ``store.py``):

    app profile  (vendor level: login routine, generic detectors, sensitive selectors)
      -> capability (this file: flow, typed params/outputs, declared outcomes, steps)
        -> tenant    (base_url, credential refs, policy, sparse overrides)

Design rules encoded here rather than in prose:

* Every acted-on control is a ``Target`` holding an ordered ``LocatorBundle``; replay tries
  locators top to bottom and never guesses between ambiguous matches.
* Values are references (``{param: member_id}`` / ``{secret: LEDGERLINE_PASS}``), never
  literals for anything classified above ``none`` — the emitter canonicalises literals and a
  validator rejects ``pii_high``/``secret`` literals.
* The same detector is a *business outcome* only at the steps that declare it (``at_steps``);
  anywhere else it is a failure. Classification is a property of the artifact, not the engine.
* ``risk_class`` on the capability is derived (max over steps); ``status: approved`` is pinned to
  a content hash so any edit reverts it to ``draft``.

The JSON Schema for this file is exported to ``/schema/capability.schema.json`` by
``teller schema export`` and checked for drift by a test.
"""

from __future__ import annotations

import hashlib
import json
import re
from typing import Annotated, ClassVar, Literal

from pydantic import BaseModel, BeforeValidator, ConfigDict, Field, field_validator, model_validator


def _check_regex(pattern: str, where: str) -> None:
    try:
        re.compile(pattern)
    except re.error as e:
        raise ValueError(f"{where}: invalid regex {pattern!r}: {e}") from e

SCHEMA_VERSION = 1

Classification = Literal["none", "pii_low", "pii_high", "secret"]
RiskClass = Literal["read", "reversible_write", "irreversible_write"]
SurfaceKind = Literal["web", "web_legacy", "desktop"]
ActionKind = Literal[
    "click", "type", "select", "press", "navigate", "read", "scroll", "dismiss_dialog", "run_subflow"
]
Status = Literal["draft", "approved", "deprecated"]

RISK_ORDER: dict[str, int] = {"read": 0, "reversible_write": 1, "irreversible_write": 2}


def _iso(v: object) -> object:
    """YAML parses bare timestamps into datetime objects; store them as ISO strings."""
    if hasattr(v, "isoformat"):
        return v.isoformat()  # type: ignore[union-attr]
    return v


IsoTimestamp = Annotated[str, BeforeValidator(_iso)]
PARAM_REF_RE = re.compile(r"\{(?P<name>[a-zA-Z_][a-zA-Z0-9_]*)\}")


class Strict(BaseModel):
    """Base with unknown-field rejection so typos in YAML fail loudly."""

    model_config = ConfigDict(extra="forbid", populate_by_name=True)


# --------------------------------------------------------------------------------------
# Values
# --------------------------------------------------------------------------------------


class ValueRef(Strict):
    """Exactly one of ``param`` / ``secret`` / ``literal``.

    ``param``   -> substituted from the caller's typed params at replay.
    ``secret``  -> resolved from the environment by the harness (login routine only); the model
                   never sees it and it is never written anywhere.
    ``literal`` -> allowed only for values classified ``none``/``pii_low`` (the emitter
                   canonicalises anything equal to a param value into a ``param`` ref).
    """

    param: str | None = None
    secret: str | None = None
    literal: str | None = None

    @model_validator(mode="after")
    def _exactly_one(self) -> ValueRef:
        set_ = [k for k in ("param", "secret", "literal") if getattr(self, k) is not None]
        if len(set_) != 1:
            raise ValueError(f"ValueRef needs exactly one of param/secret/literal, got {set_}")
        return self

    def render(self, params: dict[str, str], secrets: dict[str, str] | None = None) -> str:
        if self.param is not None:
            if self.param not in params:
                raise KeyError(f"missing param {self.param!r}")
            return str(params[self.param])
        if self.secret is not None:
            if not secrets or self.secret not in secrets:
                raise KeyError(f"missing secret {self.secret!r}")
            return secrets[self.secret]
        return self.literal or ""


# --------------------------------------------------------------------------------------
# Locators — an ordered bundle of independent strategies per target
# --------------------------------------------------------------------------------------


class _LocatorBase(Strict):
    frame: str | None = Field(
        default=None,
        description="Frame path (e.g. 'main'); None inherits the Target's frame.",
    )
    robustness: str | None = Field(
        default=None, description="One-line note on why this strategy should survive change."
    )
    surfaces: list[SurfaceKind] = Field(
        default_factory=lambda: ["web", "web_legacy"],
        description="Surface implementations able to evaluate this locator kind.",
    )


class RoleNameLocator(_LocatorBase):
    """Accessible role + computed accessible name (Playwright get_by_role)."""

    kind: Literal["role_name"] = "role_name"
    role: str
    name: str
    exact: bool = True
    surfaces: list[SurfaceKind] = Field(default_factory=lambda: ["web", "web_legacy", "desktop"])


class LabelAnchorLocator(_LocatorBase):
    """Visible label text + geometric relation to the control — the legacy-table case where
    there is no <label for>."""

    kind: Literal["label_anchor"] = "label_anchor"
    label: str
    relation: Literal["same_row_right", "below", "label_for", "preceding"] = "same_row_right"
    control: Literal["text_input", "password", "select", "checkbox", "radio", "button", "any"] = (
        "any"
    )
    surfaces: list[SurfaceKind] = Field(default_factory=lambda: ["web", "web_legacy", "desktop"])


class TextExactLocator(_LocatorBase):
    """Exact visible text (or alt) optionally constrained to a tag."""

    kind: Literal["text_exact"] = "text_exact"
    text: str
    tag: str | None = None
    surfaces: list[SurfaceKind] = Field(default_factory=lambda: ["web", "web_legacy", "desktop"])


class AttrStableLocator(_LocatorBase):
    """A name/id attribute that passed the recorder's stability heuristic."""

    kind: Literal["attr_stable"] = "attr_stable"
    attr: str
    value: str
    tag: str | None = None


class RowMatch(Strict):
    column: str
    equals: str


class TableCellLocator(_LocatorBase):
    """Header-name addressed cell: table near ``table_anchor``, row where ``row_match.column``
    equals a value, cell under ``column``. Survives row and column reordering."""

    kind: Literal["table_cell"] = "table_cell"
    table_anchor: str
    row_match: RowMatch
    column: str
    surfaces: list[SurfaceKind] = Field(default_factory=lambda: ["web", "web_legacy", "desktop"])


class XPathAnchoredLocator(_LocatorBase):
    """XPath relative to an anchor text node. Structural; flags drift when used."""

    kind: Literal["xpath_anchored"] = "xpath_anchored"
    anchor_text: str
    xpath: str


class CoordsVerifiedLocator(_LocatorBase):
    """Frame-relative coordinates, accepted only if the element under the point carries
    ``verify_text``. Never trusted for unattended replay."""

    kind: Literal["coords_verified"] = "coords_verified"
    x: int
    y: int
    viewport: tuple[int, int] = (1280, 800)
    verify_text: str = ""
    surfaces: list[SurfaceKind] = Field(default_factory=lambda: ["web", "web_legacy", "desktop"])


class AxPathLocator(_LocatorBase):
    """Accessibility-tree path for desktop surfaces (designed, not built)."""

    kind: Literal["ax_path"] = "ax_path"
    ax_path: str
    surfaces: list[SurfaceKind] = Field(default_factory=lambda: ["desktop"])


Locator = Annotated[
    RoleNameLocator
    | LabelAnchorLocator
    | TextExactLocator
    | AttrStableLocator
    | TableCellLocator
    | XPathAnchoredLocator
    | CoordsVerifiedLocator
    | AxPathLocator,
    Field(discriminator="kind"),
]

LOCATOR_KINDS: tuple[str, ...] = (
    "role_name",
    "label_anchor",
    "text_exact",
    "attr_stable",
    "table_cell",
    "xpath_anchored",
    "coords_verified",
    "ax_path",
)


class Target(Strict):
    """Where a step acts. ``locators`` is ordered; replay records which index resolved."""

    frame: str | None = Field(default=None, description="Frame path; None = top document.")
    text_hint: str | None = Field(default=None, description="Fingerprint to break ambiguity.")
    tag_hint: str | None = None
    locators: list[Locator] = Field(min_length=1)

    def kinds(self) -> list[str]:
        return [loc.kind for loc in self.locators]


# --------------------------------------------------------------------------------------
# Predicates — used for waits, expectations, detectors, checkpoints
# --------------------------------------------------------------------------------------


class Predicate(Strict):
    """A single observable condition. Exactly one leaf field, or ``any_of``/``all``.

    ``{param}`` placeholders inside string leaves are substituted at evaluation time.
    """

    frame: str | None = None
    text_contains: str | None = None
    text_matches: str | None = None
    url_matches: str | None = None
    title_matches: str | None = None
    http_status: int | None = None
    http_status_gte: int | None = None
    css_exists: str | None = None
    value_equals: ValueRef | None = None
    output_present: str | None = None
    dialog_text_matches: str | None = None
    detector: str | None = Field(default=None, description="Name of an app-profile detector.")
    any_of: list[Predicate] | None = None
    all: list[Predicate] | None = None

    LEAVES: ClassVar[tuple[str, ...]] = (
        "text_contains",
        "text_matches",
        "url_matches",
        "title_matches",
        "http_status",
        "http_status_gte",
        "css_exists",
        "value_equals",
        "output_present",
        "dialog_text_matches",
        "detector",
        "any_of",
        "all",
    )

    @model_validator(mode="after")
    def _one_leaf(self) -> Predicate:
        set_ = [k for k in self.LEAVES if getattr(self, k) is not None]
        if len(set_) != 1:
            raise ValueError(f"Predicate needs exactly one condition, got {set_ or 'none'}")
        for k in ("text_matches", "url_matches", "title_matches", "dialog_text_matches"):
            v = getattr(self, k)
            if v is not None:
                _check_regex(v, k)
        return self

    def describe(self) -> str:
        for k in self.LEAVES:
            v = getattr(self, k)
            if v is None:
                continue
            if k in ("any_of", "all"):
                inner = ", ".join(p.describe() for p in v)
                return f"{k}({inner})"
            if isinstance(v, ValueRef):
                v = v.model_dump(exclude_none=True)
            where = f" in frame {self.frame!r}" if self.frame else ""
            return f"{k}={v!r}{where}"
        return "<empty>"


class WaitFor(Strict):
    state: Literal["navigation", "text", "selector", "url", "none"] = "text"
    text: str | None = None
    selector: str | None = None
    url_matches: str | None = None
    frame: str | None = None
    timeout_ms: int = Field(default=5000, ge=100, le=120_000)

    @model_validator(mode="after")
    def _shape(self) -> WaitFor:
        need = {"text": "text", "selector": "selector", "url": "url_matches"}
        if self.state in need and getattr(self, need[self.state]) is None:
            raise ValueError(f"wait_for.state={self.state!r} requires {need[self.state]!r}")
        return self


# --------------------------------------------------------------------------------------
# Params / outputs
# --------------------------------------------------------------------------------------


class Param(Strict):
    type: Literal["string", "integer", "decimal", "boolean"] = "string"
    description: str = ""
    required: bool = True
    pattern: str | None = None
    enum: list[str] | None = None
    classification: Classification = "none"
    example: str | None = None

    @model_validator(mode="after")
    def _no_sensitive_examples(self) -> Param:
        if self.classification in ("pii_high", "secret") and self.example is not None:
            raise ValueError("example is only stored for none/pii_low params")
        if self.pattern:
            _check_regex(self.pattern, "pattern")
        return self


Parser = Literal["string", "currency_usd", "int", "decimal", "regex"]


class Output(Strict):
    type: Literal["string", "integer", "decimal", "boolean"] = "string"
    description: str = ""
    parse: Parser = "string"
    regex: str | None = Field(default=None, description="Required when parse == 'regex'.")
    pattern: str | None = Field(default=None, description="Validated against the parsed value.")
    classification: Classification = "none"
    source_step: str | None = None

    @model_validator(mode="after")
    def _regex_shape(self) -> Output:
        if self.parse == "regex" and not self.regex:
            raise ValueError("parse=regex requires 'regex'")
        for r in (self.regex, self.pattern):
            if r:
                _check_regex(r, "regex/pattern")
        return self


# --------------------------------------------------------------------------------------
# Outcomes, recoveries, steps
# --------------------------------------------------------------------------------------


class BusinessOutcome(Strict):
    """The app said *no to the data*. Legitimate answer the caller must branch on."""

    description: str
    detect: Predicate
    at_steps: list[str] = Field(min_length=1)
    returns: dict[str, str] = Field(default_factory=dict)


class Remedy(Strict):
    action: Literal["click", "dismiss_dialog", "wait_and_retry", "run_subflow"]
    target: Target | None = None
    accept: bool | None = None
    backoff_ms: list[int] | None = None
    subflow: str | None = None
    then: Literal["restart_from_anchor", "continue"] | None = None

    @model_validator(mode="after")
    def _shape(self) -> Remedy:
        if self.action == "click" and self.target is None:
            raise ValueError("remedy click requires target")
        if self.action == "dismiss_dialog" and self.accept is None:
            raise ValueError("remedy dismiss_dialog requires accept")
        if self.action == "wait_and_retry" and not self.backoff_ms:
            raise ValueError("remedy wait_and_retry requires backoff_ms")
        if self.action == "run_subflow" and not self.subflow:
            raise ValueError("remedy run_subflow requires subflow")
        return self


class RecoverableCondition(Strict):
    """A known interstitial/dialog/slowness with a declared, bounded remedy."""

    id: str
    detect: Predicate
    remedy: Remedy
    max_times: int = Field(default=1, ge=1, le=10)


class Extract(Strict):
    output: str
    parse: Parser | None = Field(default=None, description="Overrides the output's parser.")


class Step(Strict):
    id: str = Field(pattern=r"^[a-z][a-z0-9_]*$")
    action: ActionKind
    intent: str = Field(description="The model's stated reason; kept for reviewers.")
    idempotent: bool = Field(
        default=False,
        description="May be re-acted after a timeout. False => re-observe only, then escalate.",
    )
    risk_class: RiskClass = "read"
    target: Target | None = None
    value: ValueRef | None = None
    option: str | None = Field(default=None, description="For select: visible option label.")
    key: str | None = Field(default=None, description="For press: key name, e.g. Enter.")
    url: str | None = Field(default=None, description="For navigate: may contain {param}.")
    accept: bool | None = Field(default=None, description="For dismiss_dialog.")
    subflow: str | None = Field(default=None, description="For run_subflow: profile subflow.")
    clear_first: bool = False
    wait_for: WaitFor | None = None
    expect: Predicate | None = Field(default=None, description="Postcondition = checkpoint.")
    on_outcome: list[str] = Field(
        default_factory=list, description="Business outcomes that may legitimately end here."
    )
    extract: Extract | None = None
    mask_in_evidence: bool = False

    @model_validator(mode="after")
    def _shape(self) -> Step:
        needs_target = {"click", "type", "select", "read", "scroll"}
        if self.action in needs_target and self.target is None:
            raise ValueError(f"step {self.id}: action {self.action!r} requires target")
        if self.action == "type" and self.value is None:
            raise ValueError(f"step {self.id}: type requires value")
        if self.action == "select" and self.option is None and self.value is None:
            raise ValueError(f"step {self.id}: select requires option or value")
        if self.action == "press" and not self.key:
            raise ValueError(f"step {self.id}: press requires key")
        if self.action == "navigate" and not self.url:
            raise ValueError(f"step {self.id}: navigate requires url")
        if self.action == "dismiss_dialog" and self.accept is None:
            raise ValueError(f"step {self.id}: dismiss_dialog requires accept")
        if self.action == "run_subflow" and not self.subflow:
            raise ValueError(f"step {self.id}: run_subflow requires subflow")
        if self.action == "read" and self.extract is None:
            raise ValueError(f"step {self.id}: read requires extract")
        if self.risk_class == "irreversible_write" and self.idempotent:
            raise ValueError(f"step {self.id}: irreversible steps cannot be idempotent")
        return self


# --------------------------------------------------------------------------------------
# Capability metadata
# --------------------------------------------------------------------------------------


class AppRef(Strict):
    profile: str = Field(description="-> apps/<profile>/profile.yaml")
    version_range: str = Field(default="*", description="UI generation recorded against.")
    surface: SurfaceKind = "web_legacy"
    entry_url: str = Field(description="May use {base_url} from the tenant.")


class Provenance(Strict):
    discovered_by: str = Field(description="Model id, or 'human' for hand-authored artifacts.")
    discovery_run_id: str | None = None
    transcript_sha256: str | None = None
    recorded_at: IsoTimestamp | None = None
    human_authored_steps: list[str] = Field(default_factory=list)


class Review(Strict):
    reviewed_by: str | None = None
    reviewed_at: IsoTimestamp | None = None
    notes: str | None = None
    approved_by: str | None = None
    approved_at: IsoTimestamp | None = None
    artifact_sha256: str | None = Field(
        default=None, description="Hash of the content at approval; any edit reverts to draft."
    )


class CapabilityMeta(Strict):
    id: str = Field(pattern=r"^[a-z][a-z0-9_]*(\.[a-z][a-z0-9_]*)+$")
    version: str = Field(pattern=r"^\d+\.\d+\.\d+$")
    status: Status = "draft"
    title: str
    description: str
    risk_class: RiskClass = "read"
    app: AppRef
    provenance: Provenance
    review: Review = Field(default_factory=Review)


class DialogPolicy(Strict):
    policy: Literal["fail_on_unknown", "dismiss_and_continue"] = "fail_on_unknown"


class EscalationPolicy(Strict):
    on_hard_failure: Literal["pause", "fail"] = "pause"
    on_irreversible_step: Literal["require_confirmation", "refuse"] = "require_confirmation"
    handoff_timeout_s: int = Field(default=900, ge=10)
    max_handoffs: int = Field(default=2, ge=0, le=10)


class Capability(Strict):
    """Root of ``capabilities/<id>@<version>.yaml``."""

    schema_version: Literal[1] = SCHEMA_VERSION
    capability: CapabilityMeta
    params: dict[str, Param] = Field(default_factory=dict)
    outputs: dict[str, Output] = Field(default_factory=dict)
    business_outcomes: dict[str, BusinessOutcome] = Field(default_factory=dict)
    recoverable_conditions: list[RecoverableCondition] = Field(default_factory=list)
    restart_anchor: str | None = None
    steps: list[Step] = Field(min_length=1)
    checkpoint: Predicate
    dialogs: DialogPolicy = Field(default_factory=DialogPolicy)
    escalation_policy: EscalationPolicy = Field(default_factory=EscalationPolicy)
    overrides: dict = Field(
        default_factory=dict, description="Merged tenant patch (recorded here after load)."
    )

    # ---- cross-field validation ---------------------------------------------------------

    @field_validator("business_outcomes")
    @classmethod
    def _outcome_codes(cls, v: dict[str, BusinessOutcome]) -> dict[str, BusinessOutcome]:
        for code in v:
            if not re.fullmatch(r"[A-Z][A-Z0-9_]*", code):
                raise ValueError(f"business outcome code {code!r} must be UPPER_SNAKE")
        return v

    @model_validator(mode="after")
    def _cross_refs(self) -> Capability:
        ids = [s.id for s in self.steps]
        if len(ids) != len(set(ids)):
            raise ValueError("duplicate step ids")
        idset = set(ids)
        if self.restart_anchor and self.restart_anchor not in idset:
            raise ValueError(f"restart_anchor {self.restart_anchor!r} is not a step")
        for code, bo in self.business_outcomes.items():
            for sid in bo.at_steps:
                if sid not in idset:
                    raise ValueError(f"business outcome {code}: at_steps {sid!r} is not a step")
        for s in self.steps:
            for code in s.on_outcome:
                if code not in self.business_outcomes:
                    raise ValueError(f"step {s.id}: on_outcome {code!r} not declared")
                if s.id not in self.business_outcomes[code].at_steps:
                    raise ValueError(
                        f"step {s.id}: on_outcome {code!r} but outcome not declared at this step"
                    )
            if s.extract and s.extract.output not in self.outputs:
                raise ValueError(f"step {s.id}: extract.output {s.extract.output!r} unknown")
            if s.value and s.value.param and s.value.param not in self.params:
                raise ValueError(f"step {s.id}: value references unknown param {s.value.param!r}")
            if s.value and s.value.literal is not None:
                # literal values are fine only when nothing sensitive could be meant
                for p in self.params.values():
                    if p.classification in ("pii_high", "secret") and p.example == s.value.literal:
                        raise ValueError(f"step {s.id}: sensitive literal value")
        for name, out in self.outputs.items():
            if out.source_step and out.source_step not in idset:
                raise ValueError(f"output {name}: source_step {out.source_step!r} is not a step")
        for ref in self.param_refs():
            if ref not in self.params and ref != "base_url":
                raise ValueError(f"placeholder {{{ref}}} does not name a declared param")
        derived = max((RISK_ORDER[s.risk_class] for s in self.steps), default=0)
        declared = RISK_ORDER[self.capability.risk_class]
        if declared != derived:
            raise ValueError(
                "capability.risk_class must equal the max over steps "
                f"({self.capability.risk_class!r} vs derived "
                f"{list(RISK_ORDER)[derived]!r})"
            )
        if self.capability.status == "approved":
            r = self.capability.review
            if not (r.approved_by and r.approved_at and r.artifact_sha256):
                raise ValueError("status=approved requires review.approved_by/approved_at/sha")
        return self

    # ---- helpers ------------------------------------------------------------------------

    def step(self, step_id: str) -> Step:
        for s in self.steps:
            if s.id == step_id:
                return s
        raise KeyError(step_id)

    def step_index(self, step_id: str) -> int:
        for i, s in enumerate(self.steps):
            if s.id == step_id:
                return i
        raise KeyError(step_id)

    def param_refs(self) -> set[str]:
        """All ``{name}`` placeholders used anywhere in steps/checkpoint/outcomes."""
        blob = json.dumps(
            self.model_dump(mode="json", include={"steps", "checkpoint", "business_outcomes"})
        )
        return {m.group("name") for m in PARAM_REF_RE.finditer(blob)}

    def content_hash(self) -> str:
        """sha256 of everything except ``review``, ``status`` and ``overrides`` — what approval pins."""
        data = self.model_dump(mode="json", exclude={"overrides"})
        data["capability"].pop("review", None)
        data["capability"].pop("status", None)
        return hashlib.sha256(json.dumps(data, sort_keys=True).encode()).hexdigest()

    def file_stem(self) -> str:
        return f"{self.capability.id}@{self.capability.version}"


# --------------------------------------------------------------------------------------
# App profile (vendor layer) and tenant (institution layer)
# --------------------------------------------------------------------------------------


class Subflow(Strict):
    entry_url: str | None = None
    steps: list[Step] = Field(min_length=1)
    checkpoint: Predicate | None = None


class UiFingerprint(Strict):
    frame_names: list[str] = Field(default_factory=list)
    title_pattern: str | None = None


class AppProfile(Strict):
    """``apps/<profile>/profile.yaml`` — shared by every capability and tenant of a vendor product."""

    schema_version: Literal[1] = SCHEMA_VERSION
    profile: str
    product: str
    login: Subflow | None = None
    detectors: dict[str, Predicate] = Field(default_factory=dict)
    recoverable_conditions: list[RecoverableCondition] = Field(default_factory=list)
    sensitive_selectors: list[str] = Field(default_factory=list)
    ui_fingerprint: UiFingerprint = Field(default_factory=UiFingerprint)

    @model_validator(mode="after")
    def _detector_names(self) -> AppProfile:
        for name in self.detectors:
            if not re.fullmatch(r"[a-z][a-z0-9_]*", name):
                raise ValueError(f"detector name {name!r} must be lower_snake")
        return self


class Tenant(Strict):
    """``tenants/<tenant>.yaml`` — one institution's instance of the vendor product."""

    tenant: str
    base_url: str
    credentials: dict[str, Literal["env"]] = Field(
        default_factory=dict, description="Secret names resolved from the environment only."
    )
    policy: str = Field(description="Path to the policy file.")
    app_version: str | None = None
    overrides: dict[str, dict] = Field(
        default_factory=dict,
        description=(
            "Sparse patches keyed by capability id. Mappings deep-merge; 'steps' is keyed by "
            "step id and deep-merges into that step; any list (e.g. locators) is replaced."
        ),
    )


__all__ = [
    "SCHEMA_VERSION",
    "Classification",
    "RiskClass",
    "SurfaceKind",
    "ActionKind",
    "ValueRef",
    "Locator",
    "LOCATOR_KINDS",
    "RoleNameLocator",
    "LabelAnchorLocator",
    "TextExactLocator",
    "AttrStableLocator",
    "TableCellLocator",
    "XPathAnchoredLocator",
    "CoordsVerifiedLocator",
    "AxPathLocator",
    "Target",
    "Predicate",
    "WaitFor",
    "Param",
    "Output",
    "BusinessOutcome",
    "Remedy",
    "RecoverableCondition",
    "Extract",
    "Step",
    "AppRef",
    "Provenance",
    "Review",
    "CapabilityMeta",
    "DialogPolicy",
    "EscalationPolicy",
    "Capability",
    "Subflow",
    "UiFingerprint",
    "AppProfile",
    "Tenant",
]
