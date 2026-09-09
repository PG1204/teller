"""Provider-neutral tool definitions for the discovery loop.

Every tool carries a required ``intent`` — the model's stated reason — which the recorder keeps
on the emitted step so a reviewer can read *why* each action was taken. The model acts by
``mark_id`` (the badge number in the screenshot / element table), never by coordinates or
selectors: the surface owns targeting, the model owns decisions.

The same specs are rendered into Gemini ``FunctionDeclaration`` and Anthropic ``tools`` shapes by
the deciders in ``llm.py``; nothing here is provider-specific.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True)
class ToolSpec:
    name: str
    description: str
    properties: dict[str, dict[str, Any]]
    required: list[str] = field(default_factory=list)

    def json_schema(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": self.properties,
            "required": list(self.required),
            "additionalProperties": False,
        }


INTENT = {
    "type": "string",
    "description": "One sentence: why this action moves toward the goal.",
}
MARK = {"type": "integer", "description": "The badge number of the element to act on."}

TOOLS: list[ToolSpec] = [
    ToolSpec(
        "click",
        "Click an interactive element (link, button, row, image button) by its mark id.",
        {"mark_id": MARK, "intent": INTENT},
        ["mark_id", "intent"],
    ),
    ToolSpec(
        "type_text",
        "Type into a text field by mark id. Use the exact value to enter; if the value is one of "
        "the supplied parameters you may write its placeholder, e.g. {member_id}. Replaces the "
        "field's current content unless clear_first is false.",
        {
            "mark_id": MARK,
            "text": {"type": "string", "description": "Text to enter (or a {param} placeholder)."},
            "clear_first": {"type": "boolean", "description": "Default true."},
            "intent": INTENT,
        },
        ["mark_id", "text", "intent"],
    ),
    ToolSpec(
        "select_option",
        "Choose an option in a dropdown (select) by its visible label.",
        {"mark_id": MARK, "option": {"type": "string"}, "intent": INTENT},
        ["mark_id", "option", "intent"],
    ),
    ToolSpec(
        "press_key",
        "Press a keyboard key (Enter, Tab, Escape), optionally focused on an element.",
        {"key": {"type": "string"}, "mark_id": MARK, "intent": INTENT},
        ["key", "intent"],
    ),
    ToolSpec(
        "navigate",
        "Load a URL directly in the main work area. Only allowed within the permitted application; "
        "prefer clicking the app's own navigation.",
        {"url": {"type": "string"}, "intent": INTENT},
        ["url", "intent"],
    ),
    ToolSpec(
        "read_value",
        "Read the visible text of an element (typically a table cell) and record it as a named "
        "output of the task. output_name is snake_case, e.g. savings_balance.",
        {"mark_id": MARK, "output_name": {"type": "string"}, "intent": INTENT},
        ["mark_id", "output_name", "intent"],
    ),
    ToolSpec(
        "scroll",
        "Scroll an element into view (by mark id) or the page in a direction.",
        {
            "mark_id": MARK,
            "direction": {"type": "string", "enum": ["up", "down"]},
            "intent": INTENT,
        },
        ["intent"],
    ),
    ToolSpec(
        "dismiss_dialog",
        "Respond to an open browser dialog (alert/confirm/prompt). accept=true presses OK, "
        "accept=false presses Cancel. Never accept a dialog that would post, transfer or delete.",
        {"accept": {"type": "boolean"}, "intent": INTENT},
        ["accept", "intent"],
    ),
    ToolSpec(
        "assert_checkpoint",
        "Declare a condition that proves the goal state was reached (text that must be visible "
        "on the final screen). Call this once you can see the final screen, before done.",
        {
            "text_contains": {"type": "string", "description": "Exact visible text to require."},
            "intent": INTENT,
        },
        ["text_contains", "intent"],
    ),
    ToolSpec(
        "ask_human",
        "Stop and hand the live session to a human operator. Use when you are stuck, the screen "
        "is unexpected, you would need to read data you should not, or the next step is "
        "irreversible (posting, transferring, deleting).",
        {"reason": {"type": "string"}, "intent": INTENT},
        ["reason", "intent"],
    ),
    ToolSpec(
        "done",
        "The goal is complete and every requested output has been recorded with read_value.",
        {"summary": {"type": "string"}, "intent": INTENT},
        ["summary", "intent"],
    ),
]

TOOLS_BY_NAME: dict[str, ToolSpec] = {t.name: t for t in TOOLS}
TERMINAL_TOOLS = {"done", "ask_human"}


def validate_call(name: str, args: dict[str, Any]) -> str | None:
    """Return an error message if the call does not fit its spec, else None."""
    spec = TOOLS_BY_NAME.get(name)
    if spec is None:
        return f"unknown tool {name!r}; use one of {sorted(TOOLS_BY_NAME)}"
    missing = [k for k in spec.required if k not in args]
    if missing:
        return f"{name}: missing required argument(s) {missing}"
    extra = [k for k in args if k not in spec.properties]
    if extra:
        return f"{name}: unexpected argument(s) {extra}"
    for k, v in args.items():
        want = spec.properties[k].get("type")
        if want == "integer" and not isinstance(v, int):
            try:
                args[k] = int(v)
            except (TypeError, ValueError):
                return f"{name}: {k} must be an integer mark id"
        if want == "boolean" and not isinstance(v, bool):
            return f"{name}: {k} must be true or false"
        if want == "string" and not isinstance(v, str):
            args[k] = str(v)
    return None
