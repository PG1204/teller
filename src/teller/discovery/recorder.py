"""Recorder — captures each executed action as an artifact-shaped step.

For every action the model took and the surface performed, the recorder keeps: the action, the
model's intent, the acted element (as the surface saw it), a ``LocatorBundle`` built from that
element with a robustness note per strategy, the risk class the gate assigned, and the
observation *after* the action. ``emit.py`` turns this into a draft ``Capability``.

Locator bundle order (most to least robust on legacy surfaces):
  role_name -> label_anchor -> text_exact -> attr_stable -> table_cell -> xpath_anchored
  -> coords_verified (recorded viewport only; never trusted unattended)
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

from teller.artifact.model import (
    AttrStableLocator,
    CoordsVerifiedLocator,
    LabelAnchorLocator,
    RiskClass,
    RoleNameLocator,
    RowMatch,
    TableCellLocator,
    Target,
    TextExactLocator,
    XPathAnchoredLocator,
)
from teller.surface.base import ActResult, Element, Observation

ARIA_ROLES_FOR_LOCATOR = {
    "link", "button", "textbox", "checkbox", "radio", "combobox", "row", "cell", "columnheader",
    "heading", "img", "menuitem", "tab", "option", "listitem",
}
CONTROL_KIND = {
    "text": "text_input", "search": "text_input", "email": "text_input", "tel": "text_input",
    "number": "text_input", "url": "text_input", "password": "password",
    "checkbox": "checkbox", "radio": "radio", "submit": "button", "button": "button", "image": "button",
}


def canonicalize(text: str | None, params: dict[str, str]) -> str | None:
    """Replace any occurrence of a param *value* with its ``{name}`` placeholder (longest first)."""
    if text is None:
        return None
    out = text
    for name, value in sorted(params.items(), key=lambda kv: -len(str(kv[1]))):
        v = str(value)
        if v and v in out:
            out = out.replace(v, "{" + name + "}")
    return out


def _row_key_token(text: str) -> str:
    """The token of a row's text that identifies it: the one carrying a param placeholder, else
    the first token (typically the record number in the first cell)."""
    tokens = text.split()
    for t in tokens:
        if "{" in t and "}" in t:
            return t
    return tokens[0] if tokens else text


def build_target(el: Element, params: dict[str, str], *, for_read: bool = False) -> Target:
    """The ordered locator bundle for an element, with the recorder's robustness notes.

    ``for_read`` marks a cell whose text is the value being extracted: that value is data, never
    a locator, so text-based strategies are skipped for it.
    """
    locs: list[Any] = []
    name = canonicalize(el.name, params) or ""
    text = canonicalize(el.text, params) or ""
    tag = el.tag
    is_control = tag in ("input", "select", "textarea")
    is_row = el.role == "row"
    if is_row and text:
        # a row's full text carries other people's data; identify it by its key cell only
        key = _row_key_token(text)
        name = ""
        text = key

    # A name derived from a sibling <td> or preceding text is OUR heuristic, not an ARIA accessible
    # name: Playwright's role engine will not see it, so role_name would never resolve for such controls.
    geometric_label = is_control and el.label is not None and el.label_relation not in (None, "label_for")
    if el.role in ARIA_ROLES_FOR_LOCATOR and name and not (el.role in ("cell", "row") and el.table) and not geometric_label:
        locs.append(
            RoleNameLocator(
                role=el.role, name=name,
                robustness="accessible name is operator-visible text; survives branding and CSS",
            )
        )
    if is_control and el.label:
        control = CONTROL_KIND.get(el.input_type or "", "select" if tag == "select" else "any")
        locs.append(
            LabelAnchorLocator(
                label=canonicalize(el.label, params) or el.label,
                relation=el.label_relation or "same_row_right",  # type: ignore[arg-type]
                control=control,  # type: ignore[arg-type]
                robustness="legacy tables carry no <label for>; the visible label in the adjacent "
                "cell is what vendors keep stable across tenants",
            )
        )
    if el.role == "cell" and el.table and el.table.get("header") and el.table.get("row_key"):
        locs.append(
            TableCellLocator(
                table_anchor=el.table.get("anchor") or el.table.get("row_key_header") or "",
                row_match=RowMatch(
                    column=el.table.get("row_key_header") or "",
                    equals=canonicalize(el.table["row_key"], params) or el.table["row_key"],
                ),
                column=el.table["header"],
                robustness="header-name addressed; survives row and column reordering",
            )
        )
    if text and not is_control and el.role not in ("cell",):
        locs.append(
            TextExactLocator(
                text=text, tag=tag if tag in ("tr", "td", "a", "button", "li") else None,
                robustness="row containing the identifying cell text" if is_row
                else "same visible text, no role dependency",
            )
        )
    elif text and el.role == "cell" and not el.sensitive and not for_read:
        locs.append(TextExactLocator(text=text, tag="td", robustness="exact cell text"))
    if el.stable_attr:
        attr, value = el.stable_attr
        locs.append(
            AttrStableLocator(
                attr=attr, value=value, tag=tag,
                robustness="cryptic but stable within this vendor build; fails across re-skins",
            )
        )
    if is_row and text:
        locs.append(
            XPathAnchoredLocator(
                anchor_text=text, xpath="ancestor::tr[1]",
                robustness="structural: the row containing the identifying cell; flags drift",
            )
        )
    elif el.role == "cell" and el.table and el.table.get("row_key"):
        col = el.table.get("col")
        locs.append(
            XPathAnchoredLocator(
                anchor_text=canonicalize(el.table["row_key"], params) or el.table["row_key"],
                xpath=f"ancestor::tr[1]/td[{col}]" if col else "ancestor::tr[1]/td",
                robustness="positional within the identified row; flags drift when used",
            )
        )
    x, y, w, h = el.bbox
    verify = "" if for_read else (name or text)[:40]
    locs.append(
        CoordsVerifiedLocator(
            x=int(x + w / 2), y=int(y + h / 2), verify_text=verify,
            robustness="recorded viewport only; verified by text under the point; never trusted "
            "for unattended replay",
        )
    )
    return Target(
        frame=el.frame or None,
        text_hint=(name or text or None) if not (el.sensitive or for_read) else None,
        tag_hint=tag,
        locators=locs,
    )


@dataclass
class RecordedStep:
    kind: str  # click | type | select | press | navigate | read | scroll | dismiss_dialog
    intent: str
    element: Element | None
    target: Target | None
    value: str | None = None  # canonicalised (may contain {param})
    value_is_secret: bool = False
    option: str | None = None
    key: str | None = None
    url: str | None = None
    accept: bool | None = None
    clear_first: bool = True
    risk_class: RiskClass = "read"
    act: ActResult | None = None
    before: Observation | None = None
    after: Observation | None = None
    output_name: str | None = None
    extracted: str | None = None
    turn: int = 0
    screenshot: str | None = None


@dataclass
class Recorder:
    params: dict[str, str]
    steps: list[RecordedStep] = field(default_factory=list)
    outputs: dict[str, str] = field(default_factory=dict)
    checkpoints: list[dict[str, Any]] = field(default_factory=list)
    human_steps: list[str] = field(default_factory=list)

    def record(self, step: RecordedStep) -> None:
        self.steps.append(step)
        if step.kind == "read" and step.output_name and step.extracted is not None:
            self.outputs[step.output_name] = step.extracted

    def add_checkpoint(self, text_contains: str, frame: str | None) -> None:
        self.checkpoints.append({"text_contains": text_contains, "frame": frame})


def infer_parser(value: str) -> str:
    v = value.strip()
    if re.fullmatch(r"-?\$\s?[\d,]+(\.\d{2})?|-?[\d,]+\.\d{2}", v):
        return "currency_usd"
    if re.fullmatch(r"-?\d+", v):
        return "int"
    return "string"
