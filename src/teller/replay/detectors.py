"""Predicate evaluation against an Observation — waits, expectations, detectors, checkpoints.

Pure functions: no Playwright, no side effects. ``{param}`` placeholders are substituted from the
run's params before matching. A ``detector`` leaf refers to an app-profile detector by name and
is resolved through ``ctx.detectors``.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from teller.artifact.model import Predicate
from teller.surface.base import Observation


@dataclass
class EvalContext:
    params: dict[str, str] = field(default_factory=dict)
    outputs: dict[str, str] = field(default_factory=dict)
    detectors: dict[str, Predicate] = field(default_factory=dict)
    field_values: dict[str, str] = field(default_factory=dict)  # frame -> last typed value ("*" = latest); secrets never stored


def subst(s: str, params: dict[str, str]) -> str:
    out = s
    for k, v in params.items():
        out = out.replace("{" + k + "}", str(v))
    return out


def _text_for(obs: Observation, frame: str | None) -> str:
    if frame is None:
        return obs.text_of()
    # tolerate a declared frame that is absent (e.g. a 500 page replaced the frameset)
    return obs.visible_text.get(frame) or ("" if obs.visible_text else obs.text_of())


def evaluate(pred: Predicate, obs: Observation, ctx: EvalContext) -> bool:
    p = ctx.params
    if pred.all is not None:
        return all(evaluate(q, obs, ctx) for q in pred.all)
    if pred.any_of is not None:
        return any(evaluate(q, obs, ctx) for q in pred.any_of)
    if pred.none_of is not None:
        return not any(evaluate(q, obs, ctx) for q in pred.none_of)
    if pred.detector is not None:
        det = ctx.detectors.get(pred.detector)
        if det is None:
            return False
        return evaluate(det, obs, ctx)
    text = _text_for(obs, pred.frame)
    if pred.text_contains is not None:
        return subst(pred.text_contains, p).lower() in text.lower()
    if pred.text_matches is not None:
        return re.search(subst(pred.text_matches, p), text, re.I) is not None
    if pred.url_matches is not None:
        return re.search(subst(pred.url_matches, p), obs.url) is not None
    if pred.title_matches is not None:
        return re.search(subst(pred.title_matches, p), obs.title or "") is not None
    if pred.http_status is not None:
        return obs.http_status == pred.http_status
    if pred.http_status_gte is not None:
        return obs.http_status is not None and obs.http_status >= pred.http_status_gte
    if pred.css_exists is not None:
        if pred.frame is not None:
            return bool(obs.selector_hits.get(f"{pred.frame}|{pred.css_exists}", False))
        return any(hit for k, hit in obs.selector_hits.items() if k.endswith(f"|{pred.css_exists}"))
    if pred.value_equals is not None:
        want = pred.value_equals.render(p) if pred.value_equals.secret is None else None
        if want is None:
            return False
        got = ctx.field_values.get(pred.frame) if pred.frame else ctx.field_values.get("*")
        return got is not None and got == want
    if pred.output_present is not None:
        return bool(ctx.outputs.get(pred.output_present))
    if pred.dialog_text_matches is not None:
        return obs.dialog is not None and re.search(
            subst(pred.dialog_text_matches, p), obs.dialog.message, re.I
        ) is not None
    return False


def describe(pred: Predicate, params: dict[str, str]) -> str:
    return subst(pred.describe(), params)


def observed_summary(obs: Observation, limit: int = 200) -> dict[str, object]:
    text = obs.text_of().strip().replace("\n", " ")
    return {
        "url": obs.url,
        "title": obs.title,
        "http_status": obs.http_status,
        "dialog": obs.dialog.model_dump() if obs.dialog else None,
        "visible_text_head": text[:limit],
        "detector_hits": obs.detector_hits,
        "element_count": len(obs.elements),
    }


def css_selectors(preds: list[Predicate]) -> list[str]:
    """All css_exists selectors used by these predicates (for the surface's probe list)."""
    out: list[str] = []

    def walk(p: Predicate) -> None:
        if p.css_exists:
            out.append(p.css_exists)
        for q in (p.all or []) + (p.any_of or []) + (p.none_of or []):
            walk(q)

    for p in preds:
        walk(p)
    return sorted(set(out))
