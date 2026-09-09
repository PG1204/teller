"""WebPlaywrightSurface — the only module in ``teller`` that imports Playwright.

Perception: every frame runs ``marks.js`` in its own document, so badges land where the browser
rendered the controls (no offset math) and the element table covers framesets and iframes.
Action: every action passes ``ControlToken.require_automation()`` and ``PolicyGate.check`` in
``act()`` — there is no other path to the browser.
Resolution: an ordered ``LocatorBundle`` is tried top to bottom with the exactly-one-visible rule;
kinds Playwright has no primitive for (``label_anchor``, ``table_cell``, ``xpath_anchored``,
``coords_verified``) resolve inside the frame via ``resolvers.js`` and are wrapped in a normal
Playwright locator by a stamped attribute.

Determinism knobs: fixed viewport, locale, timezone, reduced motion, no ``networkidle``, no sleeps.
"""

from __future__ import annotations

import hashlib
import logging
import re
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

from playwright.sync_api import (
    Browser,
    BrowserContext,
    Dialog,
    Frame,
    Locator,
    Page,
    Playwright,
    sync_playwright,
)
from playwright.sync_api import (
    Error as PlaywrightError,
)

from teller.artifact.model import RiskClass, Target
from teller.hitl.state import ControlToken
from teller.policy.gate import PolicyGate
from teller.policy.redact import Redactor
from teller.surface.base import (
    Action,
    ActResult,
    ConfirmationRequired,
    DialogInfo,
    Element,
    Observation,
    PolicyDenied,
    Resolution,
    SurfaceError,
)

log = logging.getLogger("teller.surface")

_HERE = Path(__file__).parent


def _load_js(name: str) -> str:
    """Load a JS function expression, dropping leading comment lines."""
    lines = (_HERE / name).read_text(encoding="utf-8").splitlines()
    while lines and (not lines[0].strip() or lines[0].lstrip().startswith("//")):
        lines.pop(0)
    return "\n".join(lines)


MARKS_JS = _load_js("marks.js")
RESOLVERS_JS = _load_js("resolvers.js")

ELEMENT_INFO_JS = """
(el) => {
  const norm = (s) => (s || "").replace(/\\s+/g, " ").trim();
  const tag = el.tagName.toLowerCase();
  const type = tag === "input" ? (el.getAttribute("type") || "text").toLowerCase() : null;
  const attrs = {};
  for (const a of ["name", "id", "href", "alt", "title", "placeholder"]) { const v = el.getAttribute(a); if (v) attrs[a] = v.slice(0, 120); }
  if (type) attrs.type = type;
  const sensitive = type === "password";
  if (!sensitive && (tag === "input" || tag === "textarea") && el.value) attrs.value = String(el.value).slice(0, 60);
  let name = norm(el.getAttribute("aria-label") || "");
  if (!name && tag === "input") name = norm(type === "image" ? (el.getAttribute("alt") || "") : (["submit","button","reset"].includes(type) ? el.value : (el.getAttribute("placeholder") || "")));
  if (!name && (tag === "img" || tag === "area")) name = norm(el.getAttribute("alt") || "");
  if (!name) name = norm(el.innerText || el.textContent || "").slice(0, 80);
  const isSubmit = (tag === "input" && ["submit", "image"].includes(type) && !!el.closest("form")) || (tag === "button" && (el.getAttribute("type") || "submit") === "submit" && !!el.closest("form"));
  const r = el.getBoundingClientRect();
  const role = el.getAttribute("role") || (tag === "a" ? "link" : tag === "button" || (tag === "input" && ["submit","button","image","reset"].includes(type)) ? "button" : tag === "select" ? "combobox" : tag === "input" || tag === "textarea" ? "textbox" : tag === "tr" ? "row" : tag === "td" ? "cell" : tag);
  return { tag, role, name, text: sensitive ? "" : norm(el.innerText || el.value || "").slice(0, 120), attrs, input_type: type,
           bbox: [Math.round(r.left), Math.round(r.top), Math.round(r.width), Math.round(r.height)], disabled: !!el.disabled, is_submit: isSubmit, sensitive };
}
"""

DOM_SNAPSHOT_JS = """
() => {
  const root = document.documentElement.cloneNode(true);
  root.querySelectorAll("input,textarea").forEach((i) => { i.removeAttribute("value"); i.textContent = ""; });
  root.querySelectorAll("[data-teller-mask]").forEach((s) => { s.textContent = "****"; });
  root.querySelectorAll("[data-teller-badge]").forEach((b) => b.remove());
  root.querySelectorAll("script").forEach((s) => s.remove());
  return root.outerHTML;
}
"""

CONTAINER_TAGS = {"tr", "li", "ul", "ol", "div", "table", "tbody", "form", "section", "p", "span"}
ARIA_ROLES = {
    "link", "button", "textbox", "checkbox", "radio", "combobox", "listbox", "option", "row",
    "cell", "gridcell", "columnheader", "rowheader", "heading", "img", "table", "menuitem", "tab",
    "dialog", "alert", "region", "navigation", "form", "list", "listitem", "switch", "slider",
}

WEB_LOCATOR_KINDS = {
    "role_name", "label_anchor", "text_exact", "attr_stable", "table_cell", "xpath_anchored",
    "coords_verified",
}


def subst(s: str | None, params: dict[str, str]) -> str | None:
    if s is None:
        return None
    out = s
    for k, v in params.items():
        out = out.replace("{" + k + "}", str(v))
    return out


class WebPlaywrightSurface:
    kind = "web_legacy"

    def __init__(
        self,
        gate: PolicyGate,
        redactor: Redactor,
        *,
        headed: bool = False,
        viewport: tuple[int, int] = (1280, 800),
        cdp_port: int | None = None,
        slow_mo_ms: int = 0,
        default_timeout_ms: int = 5000,
        resolve_timeout_ms: int = 1500,
        on_event: Callable[[str, dict[str, Any]], None] | None = None,
    ):
        self.gate = gate
        self.redactor = redactor
        self.headed = headed
        self.viewport = viewport
        self.cdp_port = cdp_port
        self.slow_mo_ms = slow_mo_ms
        self.default_timeout_ms = default_timeout_ms
        self.resolve_timeout_ms = resolve_timeout_ms
        self._on_event = on_event or (lambda t, p: None)

        self._pw: Playwright | None = None
        self._browser: Browser | None = None
        self._context: BrowserContext | None = None
        self._page: Page | None = None
        self._dialog: Dialog | None = None
        self._dialog_info: DialogInfo | None = None
        self._status: dict[str, int] = {}
        self._nav_counter = 0
        self._resolve_seq = 0
        self._recorder_sink: Callable[[dict[str, Any]], None] | None = None
        self._popups_closed = 0
        self.probe_selectors: list[str] = []  # css_exists predicates evaluated on every observe

    # ------------------------------------------------------------------ lifecycle

    @property
    def page(self) -> Page:
        if self._page is None:
            raise SurfaceError("surface not started")
        return self._page

    def start(self, entry_url: str) -> None:
        self._pw = sync_playwright().start()
        args = ["--disable-blink-features=AutomationControlled"]
        if self.cdp_port:
            args.append(f"--remote-debugging-port={self.cdp_port}")
        self._browser = self._pw.chromium.launch(
            headless=not self.headed, args=args, slow_mo=self.slow_mo_ms
        )
        self._context = self._browser.new_context(
            viewport={"width": self.viewport[0], "height": self.viewport[1]},
            locale="en-US",
            timezone_id="America/Chicago",
            reduced_motion="reduce",
            accept_downloads=False,
            java_script_enabled=True,
        )
        self._context.set_default_timeout(self.default_timeout_ms)
        self._page = self._context.new_page()
        self._page.on("popup", self._on_popup)
        self._page.on("dialog", self._on_dialog)
        self._page.on("response", self._on_response)
        self._page.on("framenavigated", self._on_framenavigated)
        if not self.gate.policy.url_allowed(entry_url):
            raise PolicyDenied(
                self.gate.check(Action(kind="navigate", url=entry_url), None, entry_url)
            )
        self._page.goto(entry_url, wait_until="commit")
        self._on_event("surface.start", {"entry_url": entry_url, "headed": self.headed})

    def stop(self) -> None:
        for closer in (
            lambda: self._context and self._context.close(),
            lambda: self._browser and self._browser.close(),
            lambda: self._pw and self._pw.stop(),
        ):
            try:
                closer()
            except Exception:  # pragma: no cover - best effort teardown
                pass
        self._page = self._context = self._browser = self._pw = None

    # ------------------------------------------------------------------ event handlers

    def _on_dialog(self, dialog: Dialog) -> None:
        # Park it: the page's JS is blocked until we accept/dismiss. observe() reports it.
        self._dialog = dialog
        self._dialog_info = DialogInfo(
            type=dialog.type, message=dialog.message, default_value=dialog.default_value or None
        )
        self._on_event("dialog.opened", {"type": dialog.type, "message": dialog.message})

    def _on_response(self, response: Any) -> None:
        try:
            if response.request.is_navigation_request():
                self._status[self._frame_path(response.frame)] = response.status
        except PlaywrightError:  # pragma: no cover
            pass

    def _on_framenavigated(self, frame: Frame) -> None:
        self._nav_counter += 1

    def _on_popup(self, page: Page) -> None:
        self._popups_closed += 1
        self._on_event("popup.closed", {"url": page.url})
        try:
            page.close()
        except PlaywrightError:  # pragma: no cover
            pass

    # ------------------------------------------------------------------ frames

    def _frame_path(self, frame: Frame) -> str:
        names: list[str] = []
        f: Frame | None = frame
        while f is not None and f.parent_frame is not None:
            names.append(f.name or f"frame{self._frame_index(f)}")
            f = f.parent_frame
        return "/".join(reversed(names))

    def _frame_index(self, frame: Frame) -> int:
        parent = frame.parent_frame
        if parent is None:
            return 0
        return parent.child_frames.index(frame)

    def _frames(self) -> list[tuple[str, Frame]]:
        out: list[tuple[str, Frame]] = []
        for f in self.page.frames:
            if f.is_detached():
                continue
            out.append((self._frame_path(f), f))
        return out

    def _frame(self, path: str | None) -> Frame | None:
        want = path or ""
        for p, f in self._frames():
            if p == want:
                return f
        return None

    def _main_status(self) -> int | None:
        for key in ("main", ""):
            if key in self._status:
                return self._status[key]
        return None

    def current_url(self) -> str:
        main = self._frame("main")
        return (main.url if main else self.page.url) or self.page.url

    # ------------------------------------------------------------------ observe

    def observe(self, *, badges: bool = True) -> Observation:
        if self._dialog_info is not None:
            # JS is blocked by the parked dialog; report it without touching the page.
            return Observation(
                url=self.page.url,
                title="",
                frames=[p for p, _ in self._frames()],
                http_status=self._main_status(),
                elements=[],
                dialog=self._dialog_info,
                visible_text={},
                text_digest=hashlib.sha256(
                    f"dialog:{self._dialog_info.message}".encode()
                ).hexdigest(),
            )

        opts = {
            "phase": "collect",
            "maskRegexes": self.redactor.text_regexes(),
            "maskLiterals": self.redactor.sensitive_literals(),
            "sensitiveSelectors": [s for s in self.redactor.mask_selectors() if s != "[data-teller-mask]"],
        }
        collected: list[tuple[str, Frame, dict[str, Any]]] = []
        for path, frame in self._frames():
            try:
                res = frame.evaluate(MARKS_JS, opts)
            except PlaywrightError as e:
                log.debug("marks.js failed in frame %r: %s", path, e)
                continue
            collected.append((path, frame, res))

        # deterministic global numbering: frame order, then row (10px bands), then x
        rows: list[tuple[tuple[int, int, int], str, dict[str, Any]]] = []
        for fi, (path, _frame, res) in enumerate(collected):
            for el in res.get("elements", []):
                x, y = el["bbox"][0], el["bbox"][1]
                rows.append(((fi, y // 10, x), path, el))
        rows.sort(key=lambda r: r[0])

        elements: list[Element] = []
        id_maps: dict[str, dict[str, int]] = {}
        for mark_id, (_key, path, el) in enumerate(rows, start=1):
            id_maps.setdefault(path, {})[str(el["idx"])] = mark_id
            elements.append(
                Element(
                    mark_id=mark_id,
                    role=el["role"],
                    name=self.redactor.scrub_text(el.get("name") or ""),
                    text=self.redactor.scrub_text(el.get("text") or ""),
                    tag=el["tag"],
                    frame=path,
                    bbox=tuple(el["bbox"]),
                    input_type=el.get("input_type"),
                    attrs={k: self.redactor.scrub_text(str(v)) for k, v in (el.get("attrs") or {}).items()},
                    label=el.get("label"),
                    label_relation=el.get("label_relation"),
                    table=el.get("table"),
                    stable_attr=tuple(el["stable_attr"]) if el.get("stable_attr") else None,
                    sensitive=bool(el.get("sensitive")),
                    disabled=bool(el.get("disabled")),
                    is_submit=bool(el.get("is_submit")),
                )
            )

        visible_text = {
            path: self.redactor.scrub_text(res.get("text", "")) for path, _f, res in collected
        }
        selector_hits: dict[str, bool] = {}
        if self.probe_selectors:
            for path, frame, _res in collected:
                try:
                    hits = frame.evaluate(
                        "sels => sels.map(s => { try { return !!document.querySelector(s); } catch (e) { return false; } })",
                        self.probe_selectors,
                    )
                except PlaywrightError:
                    continue
                for sel, hit in zip(self.probe_selectors, hits, strict=True):
                    selector_hits[f"{path}|{sel}"] = bool(hit)
        digest_src = "\n".join(f"{p}:{t}" for p, t in visible_text.items())
        title = ""
        try:
            title = self.page.title()
        except PlaywrightError:  # pragma: no cover
            pass

        screenshot: bytes | None = None
        if badges:
            for path, frame, _res in collected:
                ids = id_maps.get(path)
                if ids:
                    try:
                        frame.evaluate(MARKS_JS, {"phase": "badge", "ids": ids})
                    except PlaywrightError:  # pragma: no cover
                        pass
        try:
            screenshot = self._screenshot_bytes()
        finally:
            for _path, frame, _res in collected:
                try:
                    frame.evaluate(MARKS_JS, {"phase": "clear", "keepMarks": True})
                except PlaywrightError:  # pragma: no cover
                    pass

        obs = Observation(
            url=self.current_url(),
            title=title,
            frames=[p for p, _f, _r in collected],
            http_status=self._main_status(),
            elements=elements,
            dialog=None,
            visible_text=visible_text,
            text_digest=hashlib.sha256(digest_src.encode()).hexdigest(),
            screenshot_jpeg=screenshot,
            selector_hits=selector_hits,
        )
        self._on_event(
            "observe",
            {"url": obs.url, "title": title, "elements": len(elements), "frames": obs.frames,
             "http_status": obs.http_status},
        )
        return obs

    # ------------------------------------------------------------------ screenshots / dom

    def _mask_locators(self) -> list[Locator]:
        masks: list[Locator] = []
        for path, _frame in self._frames():
            base: Any = self.page
            if path:
                for name in path.split("/"):
                    sel = f'frame[name="{name}"], iframe[name="{name}"]'
                    base = base.frame_locator(sel)
            for sel in self.redactor.mask_selectors():
                try:
                    masks.append(base.locator(sel))
                except PlaywrightError:  # pragma: no cover
                    continue
        return masks

    def _screenshot_bytes(self, *, redact: bool = True, quality: int = 70) -> bytes:
        kwargs: dict[str, Any] = {"type": "jpeg", "quality": quality, "full_page": False}
        if redact:
            kwargs["mask"] = self._mask_locators()
            kwargs["mask_color"] = "#000000"
        try:
            return self.page.screenshot(**kwargs)
        except PlaywrightError as e:
            log.warning("masked screenshot failed (%s); retrying without frame masks", e)
            kwargs["mask"] = [self.page.locator(s) for s in self.redactor.mask_selectors()]
            return self.page.screenshot(**kwargs)

    def screenshot(self, path: str, *, redact: bool = True) -> str:
        data = self._screenshot_bytes(redact=redact)
        p = Path(path)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(data)
        return str(p)

    def dom_snapshot(self, path: str) -> str:
        parts: list[str] = []
        for fp, frame in self._frames():
            try:
                html = frame.evaluate(DOM_SNAPSHOT_JS)
            except PlaywrightError as e:
                html = f"<!-- snapshot failed: {e} -->"
            parts.append(f"<!-- frame: {fp or 'top'} url: {frame.url} -->\n{html}\n")
        p = Path(path)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(self.redactor.scrub_text("\n".join(parts)), encoding="utf-8")
        return str(p)

    # ------------------------------------------------------------------ dialogs

    def pending_dialog(self) -> DialogInfo | None:
        return self._dialog_info

    def handle_dialog(self, accept: bool) -> DialogInfo | None:
        info = self._dialog_info
        if self._dialog is None:
            return None
        try:
            if accept:
                self._dialog.accept()
            else:
                self._dialog.dismiss()
        except PlaywrightError as e:  # already handled by the browser
            log.debug("dialog already closed: %s", e)
        self._dialog = None
        self._dialog_info = None
        self._on_event("dialog.handled", {"accept": accept, "message": info.message if info else ""})
        return info

    # ------------------------------------------------------------------ resolve

    def capabilities(self) -> set[str]:
        return set(WEB_LOCATOR_KINDS)

    def _visible_indices(self, loc: Locator, cap: int = 25) -> list[int]:
        try:
            n = loc.count()
        except PlaywrightError:
            return []
        out: list[int] = []
        for i in range(min(n, cap)):
            try:
                if loc.nth(i).is_visible():
                    out.append(i)
            except PlaywrightError:
                continue
        return out

    def _innermost(self, pl: Locator, visible: list[int]) -> list[int]:
        """Drop container matches that contain another match (nested layout tables)."""
        try:
            contains_other = pl.evaluate_all(
                "els => els.map((e, i) => els.some((o, j) => j !== i && e !== o && e.contains(o)))"
            )
        except PlaywrightError:
            return visible
        return [i for i in visible if i < len(contains_other) and not contains_other[i]]

    def _build_locator(self, kind: str, spec: dict[str, Any], frame: Frame) -> Locator | None:
        if kind == "role_name":
            role = spec["role"]
            name = spec["name"]
            if role not in ARIA_ROLES:
                # e.g. our inferred "clickable": fall back to onclick-bearing elements with the text
                return frame.locator("[onclick]").filter(
                    has_text=re.compile(rf"^\s*{re.escape(name)}\s*$")
                )
            return frame.get_by_role(role, name=name, exact=bool(spec.get("exact", True)))
        if kind == "text_exact":
            text = spec["text"]
            tag = spec.get("tag")
            if tag and tag.lower() in CONTAINER_TAGS:
                return frame.locator(tag).filter(has=frame.get_by_text(text, exact=True))
            if tag:
                return frame.locator(tag).filter(has_text=re.compile(rf"^\s*{re.escape(text)}\s*$"))
            return frame.get_by_text(text, exact=True)
        if kind == "attr_stable":
            tag = spec.get("tag") or ""
            value = str(spec["value"]).replace('"', '\\"')
            return frame.locator(f'{tag}[{spec["attr"]}="{value}"]')
        # in-frame resolvers
        self._resolve_seq += 1
        token = f"t{self._resolve_seq}"
        if kind == "label_anchor":
            js_opts = {
                "kind": "label_anchor", "token": token, "label": spec["label"],
                "relation": spec.get("relation", "same_row_right"), "control": spec.get("control", "any"),
            }
        elif kind == "table_cell":
            rm = spec["row_match"]
            js_opts = {
                "kind": "table_cell", "token": token, "table_anchor": spec["table_anchor"],
                "row_column": rm["column"], "row_equals": rm["equals"], "column": spec["column"],
            }
        elif kind == "xpath_anchored":
            js_opts = {"kind": "xpath_anchored", "token": token, "anchor_text": spec["anchor_text"], "xpath": spec["xpath"]}
        elif kind == "coords_verified":
            js_opts = {"kind": "point", "token": token, "x": spec["x"], "y": spec["y"], "verify_text": spec.get("verify_text", "")}
        else:
            return None
        count = frame.evaluate(RESOLVERS_JS, js_opts)
        if not count:
            return None
        return frame.locator(f'[data-teller-resolve="{token}"]')

    def resolve_mark(self, element: Element) -> Resolution:
        """Resolve an element the model chose by mark id (valid until the next observe)."""
        frame = self._frame(element.frame)
        if frame is None:
            return Resolution(handle=None, frame=element.frame, failure="frame_missing",
                              diagnostics=[{"kind": "mark", "matched": 0, "note": "frame missing"}])
        loc = frame.locator(f'[data-teller-mark="{element.mark_id}"]')
        visible = self._visible_indices(loc)
        if len(visible) != 1:
            return Resolution(handle=None, frame=element.frame, failure="not_found",
                              diagnostics=[{"kind": "mark", "matched": len(visible), "note": "mark stale; re-observe"}])
        return Resolution(handle=loc.nth(visible[0]), frame=element.frame, index_used=0, kind="mark",
                          element=element, diagnostics=[{"kind": "mark", "matched": 1, "note": "resolved by mark id"}])

    def _element_from_handle(self, handle: Locator, frame_path: str) -> Element | None:
        try:
            info = handle.evaluate(ELEMENT_INFO_JS)
        except PlaywrightError:
            return None
        return Element(
            mark_id=0,
            role=info.get("role") or info["tag"],
            name=self.redactor.scrub_text(info.get("name") or ""),
            text=self.redactor.scrub_text(info.get("text") or ""),
            tag=info["tag"],
            frame=frame_path,
            bbox=tuple(info["bbox"]),
            input_type=info.get("input_type"),
            attrs={k: self.redactor.scrub_text(str(v)) for k, v in (info.get("attrs") or {}).items()},
            sensitive=bool(info.get("sensitive")),
            disabled=bool(info.get("disabled")),
            is_submit=bool(info.get("is_submit")),
        )

    def resolve(self, target: Target, params: dict[str, str]) -> Resolution:
        deadline = time.monotonic() + self.resolve_timeout_ms / 1000
        diagnostics: list[dict[str, Any]] = []
        saw_ambiguous = False
        hint = subst(target.text_hint, params)
        for _path, fr in self._frames():
            try:
                fr.evaluate(RESOLVERS_JS, {"kind": "clear"})
            except PlaywrightError:
                continue
        while True:
            diagnostics = []
            saw_ambiguous = False
            for idx, loc in enumerate(target.locators):
                spec = {k: (subst(v, params) if isinstance(v, str) else v) for k, v in loc.model_dump().items()}
                if isinstance(spec.get("row_match"), dict):
                    spec["row_match"] = {k: subst(v, params) for k, v in spec["row_match"].items()}
                kind = loc.kind
                if kind not in WEB_LOCATOR_KINDS:
                    diagnostics.append({"index": idx, "kind": kind, "matched": 0, "note": "unsupported on web"})
                    continue
                frame_path = spec.get("frame") or target.frame or ""
                frame = self._frame(frame_path)
                if frame is None:
                    diagnostics.append({"index": idx, "kind": kind, "matched": 0, "note": f"frame {frame_path!r} missing"})
                    continue
                try:
                    pl = self._build_locator(kind, spec, frame)
                except PlaywrightError as e:
                    diagnostics.append({"index": idx, "kind": kind, "matched": 0, "note": f"error: {e}"[:200]})
                    continue
                if pl is None:
                    diagnostics.append({"index": idx, "kind": kind, "matched": 0, "note": "no match"})
                    continue
                visible = self._visible_indices(pl)
                if len(visible) > 1 and kind == "text_exact" and (spec.get("tag") or "").lower() in CONTAINER_TAGS:
                    visible = self._innermost(pl, visible)
                if len(visible) == 0:
                    diagnostics.append({"index": idx, "kind": kind, "matched": 0, "note": "no visible match"})
                    continue
                if len(visible) == 1:
                    handle = pl.nth(visible[0])
                    diagnostics.append({"index": idx, "kind": kind, "matched": 1, "note": "resolved"})
                    return Resolution(
                        handle=handle, frame=frame_path, index_used=idx, kind=kind,
                        diagnostics=diagnostics, element=self._element_from_handle(handle, frame_path),
                    )
                # >1 visible: fingerprint filter
                survivors: list[int] = []
                for i in visible:
                    cand = pl.nth(i)
                    try:
                        info = cand.evaluate(ELEMENT_INFO_JS)
                    except PlaywrightError:
                        continue
                    ok = True
                    if hint and hint.lower() not in (info.get("text", "") + " " + info.get("name", "")).lower():
                        ok = False
                    if target.tag_hint and info.get("tag") != target.tag_hint.lower():
                        ok = False
                    if ok:
                        survivors.append(i)
                if len(survivors) == 1:
                    handle = pl.nth(survivors[0])
                    diagnostics.append({"index": idx, "kind": kind, "matched": len(visible), "note": "ambiguous; fingerprint picked one"})
                    return Resolution(
                        handle=handle, frame=frame_path, index_used=idx, kind=kind, ambiguous_resolved=True,
                        diagnostics=diagnostics, element=self._element_from_handle(handle, frame_path),
                    )
                saw_ambiguous = True
                diagnostics.append({"index": idx, "kind": kind, "matched": len(visible), "note": f"ambiguous: {len(visible)} visible, {len(survivors)} after fingerprint"})
            if time.monotonic() >= deadline:
                break
            self.page.wait_for_timeout(250)
        return Resolution(
            handle=None, frame=target.frame, diagnostics=diagnostics,
            failure="ambiguous" if saw_ambiguous else "not_found",
        )

    # ------------------------------------------------------------------ act

    def act(
        self,
        token: ControlToken,
        action: Action,
        resolution: Resolution | None,
        *,
        declared_risk: RiskClass = "read",
        confirmed: bool = False,
    ) -> ActResult:
        token.require_automation()  # ControlViolation if a human holds the session
        element = resolution.element if resolution else None
        url_before = self.current_url()
        dialog_text = self._dialog_info.message if self._dialog_info else None
        decision = self.gate.check(
            action, element, url_before, dialog_text=dialog_text, declared_risk=declared_risk
        )
        self._on_event(
            "policy_check",
            {"action": action.kind, "allowed": decision.allowed, "risk_class": decision.risk_class,
             "confirm_required": decision.confirm_required, "reason": decision.reason,
             "target": element.short() if element else None},
        )
        if not decision.allowed:
            raise PolicyDenied(decision)
        if decision.confirm_required and not confirmed:
            raise ConfirmationRequired(decision)

        if self._dialog_info is not None and action.kind != "dismiss_dialog":
            return ActResult(
                ok=False, action=action.kind, url_before=url_before, url_after=url_before,
                risk_class=decision.risk_class, note="a dialog is open; handle it first",
            )

        nav_before = self._nav_counter
        t0 = time.monotonic()
        text: str | None = None
        note: str | None = None
        ok = True
        try:
            handle = resolution.handle if resolution else None
            if action.kind in ("click", "type", "select", "read", "scroll") and handle is None:
                raise SurfaceError(f"{action.kind} requires a resolved target")
            if action.kind == "click":
                handle.click(timeout=self.default_timeout_ms, no_wait_after=True)
            elif action.kind == "type":
                if action.clear_first:
                    handle.fill(action.value or "", timeout=self.default_timeout_ms)
                else:
                    handle.press_sequentially(action.value or "", timeout=self.default_timeout_ms)
            elif action.kind == "select":
                if action.option is not None:
                    handle.select_option(label=action.option, timeout=self.default_timeout_ms)
                else:
                    handle.select_option(value=action.value or "", timeout=self.default_timeout_ms)
            elif action.kind == "press":
                if handle is not None:
                    handle.press(action.key or "Enter")
                else:
                    self.page.keyboard.press(action.key or "Enter")
            elif action.kind == "navigate":
                frame = self._frame(action.frame) if action.frame else None
                target_url = action.url or ""
                if frame is not None and frame.parent_frame is not None:
                    frame.goto(target_url, wait_until="commit")
                else:
                    self.page.goto(target_url, wait_until="commit")
            elif action.kind == "read":
                text = handle.inner_text(timeout=self.default_timeout_ms).strip()
                if not text:
                    text = (handle.input_value(timeout=1000) or "").strip()
            elif action.kind == "scroll":
                handle.scroll_into_view_if_needed(timeout=self.default_timeout_ms)
            elif action.kind == "dismiss_dialog":
                info = self.handle_dialog(bool(action.accept))
                note = f"dialog handled: {info.message if info else 'none'}"
            elif action.kind == "run_subflow":
                raise SurfaceError("run_subflow is executed by the interpreter, not the surface")
            else:  # pragma: no cover
                raise SurfaceError(f"unknown action {action.kind}")
        except (PlaywrightError, SurfaceError) as e:
            ok = False
            note = str(e).splitlines()[0][:300]
        # give the page a beat so a triggered dialog/navigation registers
        try:
            self.page.wait_for_timeout(50)
        except PlaywrightError:  # pragma: no cover
            pass
        duration = int((time.monotonic() - t0) * 1000)
        url_after = self.current_url()
        result = ActResult(
            ok=ok, action=action.kind, text=text, url_before=url_before, url_after=url_after,
            navigated=self._nav_counter != nav_before or url_after != url_before,
            dialog_opened=self._dialog_info is not None and action.kind != "dismiss_dialog",
            duration_ms=duration, risk_class=decision.risk_class, note=note,
        )
        self._on_event(
            "act",
            {"action": action.kind, "ok": ok, "navigated": result.navigated,
             "dialog_opened": result.dialog_opened, "duration_ms": duration, "note": note,
             "risk_class": decision.risk_class, "kind": resolution.kind if resolution else None,
             "index_used": resolution.index_used if resolution else None},
        )
        return result

    # ------------------------------------------------------------------ handoff support

    def inject_recorder(self, sink: Callable[[dict[str, Any]], None]) -> None:
        """Capture the human's actions during a handoff (recorder.js, P4)."""
        from teller.surface.recorder import RECORDER_JS

        self._recorder_sink = sink
        if self._context is None:
            return
        try:
            self._context.expose_binding(
                "__teller_hitl", lambda source, payload: self._recorder_event(source, payload)
            )
        except PlaywrightError:
            pass  # already exposed
        self._context.add_init_script(RECORDER_JS)
        for _path, frame in self._frames():
            try:
                frame.evaluate(RECORDER_JS)
            except PlaywrightError:
                continue

    def _recorder_event(self, source: Any, payload: dict[str, Any]) -> None:
        if self._recorder_sink is None:
            return
        try:
            payload = dict(payload)
            payload["frame"] = self._frame_path(source["frame"])
        except Exception:  # pragma: no cover
            pass
        self._recorder_sink(self.redactor.scrub(payload))

    def detach_recorder(self) -> None:
        self._recorder_sink = None
        for _path, frame in self._frames():
            try:
                frame.evaluate("() => { window.__tellerRecorderOff = true; }")
            except PlaywrightError:
                continue

    def session_endpoint(self) -> str | None:
        return f"http://127.0.0.1:{self.cdp_port}" if self.cdp_port else None
