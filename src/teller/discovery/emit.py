"""Emitter — turns a Recorder's captured steps into a draft ``Capability``.

Inference rules (written down so a reviewer can predict the output; tested on fixtures):

  E1  A click/navigate/press that changed the URL or the visible text gets
      ``wait_for: {state: text, text: <heading>, frame}`` where <heading> is the first short line
      of visible text that appeared after the action and was not present before.
  E2  The same heading becomes the step's ``expect: {text_contains}``; if none appeared, the
      step gets ``expect: {url_matches}`` on the new path (params canonicalised) if the URL
      changed, else no expect.
  E3  ``type`` steps expect the typed value (``value_equals``) when the value is a param ref.
  E4  ``read`` steps carry ``extract: {output, parse}`` with the parser inferred from the
      captured text (currency_usd / int / string) unless the caller declared one.
  E5  A ``dismiss_dialog`` step is kept verbatim (accept flag) — the dialog it answers is
      recorded in ``review.notes`` for the reviewer to promote into a declared condition.
  E6  ``idempotent`` is true for reads, navigation and non-submitting clicks; false for any
      submitting click and for reversible/irreversible writes.
  E7  Literals equal to a param value anywhere (typed text, locator text, URL segments,
      text hints) are canonicalised to ``{param}`` so the artifact is data-independent.
  E8  The final ``checkpoint`` is the conjunction of the model's ``assert_checkpoint`` texts
      (or the last heading) plus ``output_present`` for every output.

Business outcomes are NOT inferred: a happy-path run never sees them. They are authored in
review (the reviewed copy sits next to the draft in evidence).
"""

from __future__ import annotations

import datetime as dt
import re
from dataclasses import dataclass
from urllib.parse import urlsplit

from teller.artifact.model import (
    AppRef,
    Capability,
    CapabilityMeta,
    Classification,
    EscalationPolicy,
    Extract,
    Output,
    Param,
    Predicate,
    Provenance,
    Review,
    Step,
    ValueRef,
    WaitFor,
)
from teller.discovery.recorder import RecordedStep, Recorder, canonicalize, infer_parser
from teller.surface.base import Observation

BANNER_WORDS = {"ledgerline", "console", "internal use", "build", "menu", "signed in"}


@dataclass
class OutputDecl:
    type: str = "string"
    classification: Classification = "none"
    parse: str | None = None
    description: str = ""


@dataclass
class ParamDecl:
    type: str = "string"
    classification: Classification = "none"
    pattern: str | None = None
    description: str = ""


def _lines(obs: Observation | None, frame: str | None) -> list[str]:
    if obs is None:
        return []
    text = obs.visible_text.get(frame or "main") or obs.text_of()
    out: list[str] = []
    for chunk in re.split(r"\s{2,}|\n", text):
        c = chunk.strip()
        if c:
            out.append(c)
    return out


def _heading_that_appeared(before: Observation | None, after: Observation | None, frame: str | None) -> str | None:
    """First short, distinctive line present after the action and absent before (E1)."""
    prev = set(_lines(before, frame))
    for line in _lines(after, frame):
        low = line.lower()
        if line in prev or len(line) < 4 or len(line) > 40:
            continue
        if any(w in low for w in BANNER_WORDS) or re.fullmatch(r"[\d\W]+", line):
            continue
        return line
    return None


def _main_frame(obs: Observation | None) -> str | None:
    if obs is None:
        return None
    if "main" in obs.frames:
        return "main"
    return None


def _idempotent(rs: RecordedStep) -> bool:
    if rs.kind in ("read", "navigate", "scroll", "press", "select", "type", "dismiss_dialog"):
        return rs.kind != "dismiss_dialog" or not bool(rs.accept)
    if rs.kind == "click":
        submit = bool(rs.element and rs.element.is_submit)
        return not submit and rs.risk_class == "read"
    return False


def emit_capability(
    rec: Recorder,
    *,
    goal: str,
    params: dict[str, str],
    param_decls: dict[str, ParamDecl],
    output_decls: dict[str, OutputDecl],
    capability_id: str,
    title: str,
    description: str | None,
    profile: str,
    version_range: str,
    surface: str,
    entry_url: str,
    discovered_by: str,
    run_id: str,
    transcript_sha256: str | None,
    version: str = "1.0.0",
    notes: str | None = None,
) -> Capability:
    steps: list[Step] = []
    review_notes: list[str] = []
    if notes:
        review_notes.append(notes)
    for i, rs in enumerate(rec.steps, start=1):
        sid = f"s{i}"
        wait: WaitFor | None = None
        expect: Predicate | None = None
        heading = None
        if rs.kind in ("click", "navigate", "press", "dismiss_dialog") and rs.act and (
            rs.act.navigated or (rs.before and rs.after and rs.before.text_digest != rs.after.text_digest)
        ):
            heading = _heading_that_appeared(rs.before, rs.after, "main")
            wframe = _main_frame(rs.after)
            if heading:
                wait = WaitFor(state="text", text=heading, frame=wframe, timeout_ms=8000)
                expect = Predicate(text_contains=heading, frame=wframe)
            elif rs.act.url_before != rs.act.url_after:
                path = urlsplit(rs.act.url_after).path
                wait = WaitFor(state="url", url_matches=re.escape(canonicalize(path, params) or path).replace(r"\{", "{").replace(r"\}", "}"), timeout_ms=8000)
                expect = Predicate(url_matches=wait.url_matches)
        if rs.kind == "type" and rs.value and rs.value.startswith("{") and rs.value.endswith("}"):
            expect = Predicate(value_equals=ValueRef(param=rs.value[1:-1]))
        value: ValueRef | None = None
        if rs.kind in ("type", "select") and rs.value is not None:
            m = re.fullmatch(r"\{([a-zA-Z_][a-zA-Z0-9_]*)\}", rs.value)
            value = ValueRef(param=m.group(1)) if m else ValueRef(literal=rs.value)
        extract: Extract | None = None
        if rs.kind == "read" and rs.output_name:
            decl = output_decls.get(rs.output_name)
            if decl and decl.parse:
                parse = decl.parse
            elif decl:
                parse = {"decimal": "currency_usd", "integer": "int"}.get(decl.type, "string")
            else:
                parse = infer_parser(rs.extracted or "")
            extract = Extract(output=rs.output_name, parse=parse)  # type: ignore[arg-type]
        if rs.kind == "dismiss_dialog" and rs.before and rs.before.dialog:
            review_notes.append(
                f"{sid}: answered dialog {rs.before.dialog.message!r} with accept={rs.accept}; "
                "consider declaring it as a recoverable condition or business outcome."
            )
        steps.append(
            Step(
                id=sid,
                action=rs.kind,  # type: ignore[arg-type]
                intent=rs.intent,
                idempotent=_idempotent(rs),
                risk_class=rs.risk_class,
                target=rs.target,
                value=value if rs.kind == "type" else None,
                option=rs.option if rs.kind == "select" else None,
                key=rs.key,
                url=canonicalize(rs.url, params) if rs.url else None,
                accept=rs.accept if rs.kind == "dismiss_dialog" else None,
                clear_first=rs.clear_first if rs.kind == "type" else False,
                wait_for=wait,
                expect=expect,
                extract=extract,
                mask_in_evidence=bool(
                    rs.output_name and output_decls.get(rs.output_name, OutputDecl()).classification in ("pii_high", "secret")
                ),
            )
        )

    outputs: dict[str, Output] = {}
    for name, val in rec.outputs.items():
        declared = name in output_decls
        decl = output_decls.get(name, OutputDecl())
        src = next((s.id for s in steps if s.extract and s.extract.output == name), None)
        if decl.parse:
            parse = decl.parse
        elif declared:
            parse = {"decimal": "currency_usd", "integer": "int"}.get(decl.type, "string")
        else:
            parse = infer_parser(val)
        otype = decl.type if declared else (
            "decimal" if parse == "currency_usd" else "integer" if parse == "int" else "string"
        )
        outputs[name] = Output(
            type=otype,  # type: ignore[arg-type]
            parse=parse,  # type: ignore[arg-type]
            classification=decl.classification,
            source_step=src,
            description=decl.description,
        )
    params_model: dict[str, Param] = {}
    for name, val in params.items():
        d = param_decls.get(name, ParamDecl())
        params_model[name] = Param(
            type=d.type,  # type: ignore[arg-type]
            classification=d.classification,
            pattern=d.pattern,
            description=d.description,
            example=str(val) if d.classification in ("none", "pii_low") else None,
        )

    main = _main_frame(rec.steps[-1].after) if rec.steps else None
    cp: list[Predicate] = []
    for c in rec.checkpoints:
        cp.append(Predicate(text_contains=canonicalize(c["text_contains"], params) or c["text_contains"], frame=c.get("frame") or main))
    if not cp:
        last_heading = next(
            (s.expect.text_contains for s in reversed(steps) if s.expect and s.expect.text_contains), None
        )
        if last_heading:
            cp.append(Predicate(text_contains=last_heading, frame=main))
    for name in outputs:
        cp.append(Predicate(output_present=name))
    checkpoint = cp[0] if len(cp) == 1 else Predicate(all=cp) if cp else Predicate(url_matches=".*")

    risk = "read"
    for s in steps:
        if s.risk_class == "irreversible_write":
            risk = "irreversible_write"
            break
        if s.risk_class == "reversible_write":
            risk = "reversible_write"

    return Capability(
        capability=CapabilityMeta(
            id=capability_id,
            version=version,
            status="draft",
            title=title,
            description=description or goal,
            risk_class=risk,  # type: ignore[arg-type]
            app=AppRef(profile=profile, version_range=version_range, surface=surface, entry_url=entry_url),  # type: ignore[arg-type]
            provenance=Provenance(
                discovered_by=discovered_by,
                discovery_run_id=run_id,
                transcript_sha256=transcript_sha256,
                recorded_at=dt.datetime.now(dt.UTC).replace(microsecond=0).isoformat(),
                human_authored_steps=list(rec.human_steps),
            ),
            review=Review(notes="\n".join(review_notes) or None),
        ),
        params=params_model,
        outputs=outputs,
        business_outcomes={},
        recoverable_conditions=[],
        restart_anchor=steps[0].id if steps else None,
        steps=steps,
        checkpoint=checkpoint,
        escalation_policy=EscalationPolicy(),
    )
