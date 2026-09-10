"""The seam between "how we perceive and act on a surface" and "the recorded flow".

Everything above this module (discovery loop, artifact, replay interpreter, policy, handoff)
speaks only in these types. ``WebPlaywrightSurface`` is the one implementation built here;
``DesktopSurface`` is a typed stub carrying the mapping table (REPORT §4).

Rules enforced at this seam:

* ``act()`` requires a ``ControlToken`` and refuses to run unless automation holds control.
* ``act()`` calls the ``PolicyGate`` exactly once per action; nothing reaches the browser
  without passing it, in discovery and replay alike.
* Irreversible actions are never performed without an explicit ``confirmed=True`` from a
  caller that obtained a human decision (or a version-pinned pre-authorisation).
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, Literal, Protocol

from pydantic import BaseModel, ConfigDict, Field

from teller.artifact.model import ActionKind, RiskClass, Target

if TYPE_CHECKING:  # pragma: no cover
    from teller.hitl.state import ControlToken


class Element(BaseModel):
    """One row of the element table the model sees; also what the recorder captures."""

    model_config = ConfigDict(extra="forbid")

    mark_id: int
    role: str
    name: str = ""
    text: str = ""
    tag: str
    frame: str = Field(description="Frame path, '' for the top document.")
    bbox: tuple[int, int, int, int] = Field(description="Frame-relative x, y, w, h.")
    input_type: str | None = None
    attrs: dict[str, str] = Field(default_factory=dict, description="name/id/href/alt/title/value")
    label: str | None = Field(default=None, description="Nearest visible label text.")
    label_relation: str | None = None
    table: dict[str, Any] | None = Field(
        default=None, description="{anchor, header, row_key_header, row_key} when inside a data table"
    )
    stable_attr: tuple[str, str] | None = Field(
        default=None, description="(attr, value) that passed the stability heuristic."
    )
    sensitive: bool = False
    disabled: bool = False
    is_submit: bool = False

    def short(self, max_text: int = 40) -> str:
        """The compact line the model reads."""
        bits = [f"[{self.mark_id}]", self.role]
        if self.name:
            bits.append(repr(self.name[:max_text]))
        elif self.sensitive:
            bits.append("'<masked>'")
        if self.text and self.text != self.name:
            bits.append(f"text={self.text[:max_text]!r}")
        if self.label and self.label != self.name:
            bits.append(f"label={self.label[:max_text]!r}")
        if self.table:
            hdr = self.table.get("header")
            rk = self.table.get("row_key")
            if hdr or rk:
                bits.append(f"cell({self.table.get('anchor', '')!s}: {rk!s} / {hdr!s})")
        if self.input_type:
            bits.append(f"type={self.input_type}")
        if self.disabled:
            bits.append("disabled")
        if self.sensitive:
            bits.append("SENSITIVE")
        bits.append(f"frame={self.frame or 'top'}")
        return " ".join(bits)


class DialogInfo(BaseModel):
    model_config = ConfigDict(extra="forbid")

    type: str
    message: str
    default_value: str | None = None


class Observation(BaseModel):
    """What the surface currently shows. Screenshot bytes are already redacted and badged."""

    model_config = ConfigDict(extra="forbid", arbitrary_types_allowed=True)

    url: str
    title: str
    frames: list[str]
    http_status: int | None = Field(default=None, description="Last navigation status, main frame.")
    elements: list[Element]
    dialog: DialogInfo | None = None
    visible_text: dict[str, str] = Field(
        default_factory=dict, description="frame path -> visible text (redacted)."
    )
    text_digest: str = Field(description="sha256 of visible text; feeds the no-progress detector.")
    screenshot_jpeg: bytes | None = Field(default=None, exclude=True, repr=False)
    screenshot_path: str | None = None
    detector_hits: list[str] = Field(default_factory=list)
    selector_hits: dict[str, bool] = Field(
        default_factory=dict, description='"frame|selector" -> exists, for css_exists predicates.'
    )

    def element(self, mark_id: int) -> Element:
        for e in self.elements:
            if e.mark_id == mark_id:
                return e
        raise KeyError(f"no element with mark id {mark_id}")

    def element_table(self, max_rows: int = 120) -> str:
        rows = [e.short() for e in self.elements[:max_rows]]
        if len(self.elements) > max_rows:
            rows.append(f"... {len(self.elements) - max_rows} more elements not shown")
        return "\n".join(rows)

    def text_of(self, frame: str | None = None) -> str:
        if frame is None:
            return "\n".join(self.visible_text.values())
        return self.visible_text.get(frame, "")


class Action(BaseModel):
    """Closed vocabulary shared by discovery tools and artifact steps. Values are already
    param-substituted by the caller."""

    model_config = ConfigDict(extra="forbid")

    kind: ActionKind
    value: str | None = None
    option: str | None = None
    key: str | None = None
    url: str | None = None
    accept: bool | None = None
    clear_first: bool = True
    frame: str | None = None


class Resolution(BaseModel):
    """Outcome of resolving a ``Target``'s locator bundle."""

    model_config = ConfigDict(extra="forbid", arbitrary_types_allowed=True)

    handle: Any | None = Field(default=None, exclude=True, repr=False)
    frame: str | None = None
    index_used: int | None = None
    kind: str | None = None
    ambiguous_resolved: bool = Field(
        default=False, description="More than one match; fingerprint filter picked exactly one."
    )
    diagnostics: list[dict[str, Any]] = Field(default_factory=list)
    element: Element | None = None
    failure: Literal["not_found", "ambiguous", "frame_missing"] | None = None

    @property
    def ok(self) -> bool:
        return self.handle is not None


class ActResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    ok: bool
    action: ActionKind
    text: str | None = Field(default=None, description="For read.")
    url_before: str
    url_after: str
    navigated: bool = False
    dialog_opened: bool = False
    duration_ms: int = 0
    risk_class: RiskClass = "read"
    note: str | None = None


class GateDecision(BaseModel):
    model_config = ConfigDict(extra="forbid")

    allowed: bool
    risk_class: RiskClass
    reason: str = ""
    confirm_required: bool = False


class SurfaceError(Exception):
    pass


class PolicyDenied(SurfaceError):
    def __init__(self, decision: GateDecision):
        super().__init__(decision.reason)
        self.decision = decision


class ConfirmationRequired(SurfaceError):
    def __init__(self, decision: GateDecision):
        super().__init__(decision.reason or "irreversible action requires a human decision")
        self.decision = decision


SurfaceKind = Literal["web", "web_legacy", "desktop"]


class Surface(Protocol):
    """What every surface implementation provides. See module docstring for the rules."""

    kind: SurfaceKind

    def start(self, entry_url: str) -> None: ...
    def stop(self) -> None: ...
    def observe(self, *, badges: bool = True) -> Observation: ...
    def resolve(self, target: Target, params: dict[str, str]) -> Resolution: ...
    def act(
        self,
        token: ControlToken,
        action: Action,
        resolution: Resolution | None,
        *,
        declared_risk: RiskClass = "read",
        confirmed: bool = False,
    ) -> ActResult: ...
    def pending_dialog(self) -> DialogInfo | None: ...
    def handle_dialog(self, accept: bool) -> DialogInfo | None: ...
    def screenshot(self, path: str, *, redact: bool = True) -> str: ...
    def dom_snapshot(self, path: str) -> str: ...
    def current_url(self) -> str: ...
    def inject_recorder(self, sink: Any) -> None: ...
    def detach_recorder(self) -> None: ...
    def capabilities(self) -> set[str]: ...
    def session_endpoint(self) -> str | None: ...
