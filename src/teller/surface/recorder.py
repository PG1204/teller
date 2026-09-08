"""recorder.js — captures what a human does in the live session during a handoff.

Injected into every frame (and as an init script for later navigations) while a human holds
control. Each event is posted through the ``__teller_hitl`` binding to the Python sink, which
redacts it and appends it to ``human_actions.jsonl``. Values are classified before they leave
the page: password fields never send a value; everything else is scrubbed by the Redactor.
"""

RECORDER_JS = r"""
(() => {
  if (window.__tellerRecorderOn) return;
  window.__tellerRecorderOn = true;
  window.__tellerRecorderOff = false;
  const norm = (s) => (s || "").replace(/\s+/g, " ").trim();
  const describe = (el) => {
    if (!el || el.nodeType !== 1) return null;
    const tag = el.tagName.toLowerCase();
    const type = tag === "input" ? (el.getAttribute("type") || "text").toLowerCase() : null;
    const attrs = {};
    for (const a of ["name", "id", "href", "alt", "title"]) { const v = el.getAttribute(a); if (v) attrs[a] = v.slice(0, 120); }
    if (type) attrs.type = type;
    let label = null;
    const td = el.closest("td,th");
    if (td) { let n = td; while ((n = n.previousElementSibling)) { const t = norm(n.innerText); if (t) { label = t.replace(/[:：]\s*$/, ""); break; } } }
    return {
      tag, type, attrs, label,
      text: type === "password" ? "" : norm(el.innerText || el.value || el.getAttribute("alt") || "").slice(0, 80),
      role: el.getAttribute("role") || (tag === "a" ? "link" : (tag === "button" || (type && ["submit","image","button"].includes(type))) ? "button" : tag),
    };
  };
  const send = (payload) => {
    if (window.__tellerRecorderOff) return;
    payload.ts = new Date().toISOString();
    payload.url = location.href;
    try { window.__teller_hitl(payload); } catch (e) { /* binding absent in tests */ }
  };
  document.addEventListener("click", (e) => {
    const el = e.target.closest("a,button,input,select,tr,td,[onclick]") || e.target;
    send({ kind: "click", target: describe(el) });
  }, true);
  document.addEventListener("change", (e) => {
    const el = e.target;
    const type = el.tagName === "INPUT" ? (el.getAttribute("type") || "text").toLowerCase() : el.tagName.toLowerCase();
    let value = null;
    if (type === "password") value = "<secret>";
    else if (el.tagName === "SELECT") value = el.options[el.selectedIndex] ? el.options[el.selectedIndex].text : null;
    else value = String(el.value || "").slice(0, 80);
    send({ kind: "change", target: describe(el), value });
  }, true);
  document.addEventListener("keydown", (e) => {
    if (["Enter", "Escape", "Tab"].includes(e.key)) send({ kind: "key", key: e.key, target: describe(e.target) });
  }, true);
  window.addEventListener("beforeunload", () => send({ kind: "navigate_away" }));
  send({ kind: "recorder_attached" });
})()
"""
