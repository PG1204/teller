"""System prompt and observation formatting for the discovery loop.

The model sees: a redacted screenshot with numbered badges, a compact element table, page facts
(URL, title, frames, HTTP status, open dialog) and a bounded slice of visible text. It acts by
mark id through the tools in ``tools.py``. It never sees credentials (the harness logs in) and
never sees values classified sensitive (masked at capture).
"""

from __future__ import annotations

from teller.surface.base import Observation

SYSTEM_PROMPT = """You are operating a legacy bank back-office web application on behalf of an \
automation system. Your job is to accomplish ONE goal by observing the screen and taking one \
action at a time, so that the system can record your successful path as a reusable, replayable \
procedure.

How it works
- Each turn you receive a screenshot with yellow numbered badges and a matching element table. \
Act ONLY through the tools, and ONLY by the badge numbers (mark ids) you can see this turn. \
Never guess a mark id that is not in the table.
- Exactly one tool call per turn. After each action you get a fresh observation.
- You are already signed in. Never try to log in, sign out, or enter credentials.
- Stay inside the application. Do not open other sites. Use the app's own navigation (left menu, \
links, buttons, search forms).
- Prefer the most direct path: menu -> search -> open the record -> read the value.

Reading data
- When the goal asks for a value, use read_value on the cell that holds it and give it a clear \
snake_case output_name (e.g. savings_balance, savings_account_number). Read every requested value.
- Cells shown as <account>, <ssn> or SENSITIVE are masked from you on purpose; you may still \
read_value them by mark id, and the system will capture the real value securely.
- Before calling done, call assert_checkpoint with a piece of text that is visible on the final \
screen and proves you reached the right place (for example the screen heading).

Safety
- Never perform an action that posts a transaction, moves money, closes or deletes anything, or \
confirms such a dialog. If the goal requires it, call ask_human instead.
- If a browser dialog appears that you did not expect, or the screen shows an error, an unexpected \
page, or nothing you can act on, call ask_human with the reason.
- If you have tried the same thing twice without progress, stop and call ask_human.

Parameters
- The task parameters are given with their values. When typing a parameter value you may type \
the value itself or its placeholder in braces (e.g. {member_id}); both are recorded as the \
parameter so the procedure can be replayed with other values.
"""


def goal_message(goal: str, params: dict[str, str], outputs: dict[str, str] | None) -> str:
    lines = [f"GOAL: {goal}", ""]
    if params:
        lines.append("PARAMETERS (typed inputs supplied for this run):")
        for k, v in params.items():
            lines.append(f"  - {k} = {v}   (placeholder: {{{k}}})")
        lines.append("")
    if outputs:
        lines.append("OUTPUTS to record with read_value (use exactly these names):")
        for k, desc in outputs.items():
            lines.append(f"  - {k}: {desc}")
        lines.append("")
    lines.append("Begin. Observe the screen and take the first action.")
    return "\n".join(lines)


def format_observation(obs: Observation, *, turn: int, text_limit: int = 1200) -> str:
    parts = [f"TURN {turn}", f"URL: {obs.url}", f"Title: {obs.title}"]
    parts.append(f"Frames: {', '.join(f or 'top' for f in obs.frames)}")
    if obs.http_status is not None:
        parts.append(f"HTTP status (main): {obs.http_status}")
    if obs.dialog is not None:
        parts.append(
            f"OPEN DIALOG ({obs.dialog.type}): {obs.dialog.message!r} — the page is blocked until "
            "you call dismiss_dialog (accept=false cancels)."
        )
        return "\n".join(parts)
    parts.append("")
    if obs.elements:
        parts.append("ELEMENTS (act by mark id):")
        parts.append(obs.element_table())
    else:
        parts.append("ELEMENTS: none visible (the page may be blank or still loading).")
    parts.append("")
    main = (obs.visible_text.get("main") or obs.text_of()).replace("\n", " | ")
    if len(main) > text_limit:
        main = main[:text_limit] + " …"
    parts.append(f"VISIBLE TEXT (main): {main}")
    return "\n".join(parts)


def tool_result_text(ok: bool, note: str) -> str:
    return ("OK. " if ok else "FAILED. ") + note
