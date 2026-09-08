// marks.js — per-frame element walker with in-DOM "Set-of-Marks" badges.
//
// Evaluated inside EVERY frame by WebPlaywrightSurface.observe(). Because each frame draws its
// own badges into its own document, the screenshot shows them exactly where the browser
// rendered the controls: no frame-offset math anywhere.
//
// Called as: (opts) => result, where opts.phase is one of:
//   "collect" -> wrap sensitive text, compute visible elements, stamp data-teller-idx; returns
//                {elements: [...], text: visibleText, offscreen: n}
//   "badge"   -> opts.ids = {localIdx: globalMarkId}; draws badges and stamps data-teller-mark
//   "clear"   -> removes badges, stamps and mask wrappers
//
// Nothing here is Playwright-specific; it is plain DOM and would run under any driver.

(opts) => {
  const phase = opts.phase || "collect";
  const doc = document;
  const body = doc.body;

  const norm = (s) => (s || "").replace(/\s+/g, " ").trim();
  const short = (s, n) => { s = norm(s); return s.length > n ? s.slice(0, n - 1) + "…" : s; };

  // ------------------------------------------------------------------ clear
  const clear = () => {
    doc.querySelectorAll("[data-teller-badge]").forEach((b) => b.remove());
    doc.querySelectorAll("[data-teller-idx]").forEach((e) => e.removeAttribute("data-teller-idx"));
    doc.querySelectorAll("[data-teller-mark]").forEach((e) => e.removeAttribute("data-teller-mark"));
    doc.querySelectorAll("span[data-teller-mask]").forEach((s) => {
      const parent = s.parentNode;
      if (!parent) return;
      while (s.firstChild) parent.insertBefore(s.firstChild, s);
      parent.removeChild(s);
      parent.normalize && parent.normalize();
    });
  };
  if (phase === "clear") { clear(); return { cleared: true }; }
  if (!body) return { elements: [], text: "", offscreen: 0, frameset: !!doc.querySelector("frameset") };

  // ------------------------------------------------------------------ badge
  if (phase === "badge") {
    const ids = opts.ids || {};
    const sx = window.scrollX || 0, sy = window.scrollY || 0;
    for (const [idx, id] of Object.entries(ids)) {
      const el = doc.querySelector(`[data-teller-idx="${idx}"]`);
      if (!el) continue;
      el.setAttribute("data-teller-mark", String(id));
      const r = el.getBoundingClientRect();
      const b = doc.createElement("span");
      b.setAttribute("data-teller-badge", String(id));
      b.textContent = String(id);
      b.style.cssText = [
        "position:absolute", "z-index:2147483647", "pointer-events:none",
        "background:#ffde21", "color:#000", "font:bold 10px/1 Arial,sans-serif",
        "padding:1px 3px", "border:1px solid #333", "border-radius:3px",
        `left:${Math.max(0, r.left + sx - 2)}px`, `top:${Math.max(0, r.top + sy - 9)}px`,
      ].join(";");
      body.appendChild(b);
    }
    return { badged: Object.keys(ids).length };
  }

  // ------------------------------------------------------------------ collect
  clear();

  // 1) wrap sensitive text nodes so the screenshot can mask them by selector
  const regexes = (opts.maskRegexes || []).map((r) => { try { return new RegExp(r, "g"); } catch (e) { return null; } }).filter(Boolean);
  const literals = (opts.maskLiterals || []).filter((s) => s && s.length >= 3);
  if (regexes.length || literals.length) {
    const walker = doc.createTreeWalker(body, NodeFilter.SHOW_TEXT, {
      acceptNode: (n) => {
        const p = n.parentElement;
        if (!p || /^(SCRIPT|STYLE|NOSCRIPT|TEXTAREA)$/.test(p.tagName)) return NodeFilter.FILTER_REJECT;
        if (p.closest("[data-teller-mask]")) return NodeFilter.FILTER_REJECT;
        return norm(n.nodeValue) ? NodeFilter.FILTER_ACCEPT : NodeFilter.FILTER_REJECT;
      },
    });
    const hits = [];
    let n;
    while ((n = walker.nextNode())) {
      const t = n.nodeValue;
      if (regexes.some((rx) => { rx.lastIndex = 0; return rx.test(t); }) || literals.some((l) => t.includes(l))) hits.push(n);
    }
    for (const node of hits) {
      const span = doc.createElement("span");
      span.setAttribute("data-teller-mask", "1");
      node.parentNode.insertBefore(span, node);
      span.appendChild(node);
    }
  }
  const sensitiveSelectors = (opts.sensitiveSelectors || []).join(",");

  // 2) helpers
  const vw = window.innerWidth, vh = window.innerHeight;
  const isVisible = (el) => {
    const cs = window.getComputedStyle(el);
    if (cs.display === "none" || cs.visibility === "hidden" || parseFloat(cs.opacity || "1") === 0) return false;
    const r = el.getBoundingClientRect();
    return r.width > 0 && r.height > 0;
  };
  const inViewport = (r) => r.bottom > 0 && r.right > 0 && r.top < vh && r.left < vw;
  const ownText = (el) => norm(Array.from(el.childNodes).filter((c) => c.nodeType === 3).map((c) => c.nodeValue).join(" "));
  const textOf = (el) => norm(el.innerText || el.textContent || "");
  const idFor = (id) => (id ? doc.getElementById(id) : null);

  const inputRole = (el) => {
    const t = (el.getAttribute("type") || "text").toLowerCase();
    if (["submit", "button", "image", "reset"].includes(t)) return "button";
    if (t === "checkbox") return "checkbox";
    if (t === "radio") return "radio";
    if (t === "hidden") return null;
    if (t === "file") return "button";
    return "textbox";
  };
  const roleOf = (el) => {
    const explicit = el.getAttribute("role");
    if (explicit) return explicit.toLowerCase();
    const tag = el.tagName;
    if (tag === "A") return el.hasAttribute("href") ? "link" : (el.hasAttribute("onclick") ? "link" : null);
    if (tag === "BUTTON") return "button";
    if (tag === "INPUT") return inputRole(el);
    if (tag === "SELECT") return "combobox";
    if (tag === "TEXTAREA") return "textbox";
    if (tag === "TR") return "row";
    if (tag === "TD") return "cell";
    if (tag === "TH") return "columnheader";
    if (tag === "AREA") return "link";
    if (tag === "IMG") return el.hasAttribute("onclick") || el.closest("a[href]") ? "img" : null;
    if (tag === "LABEL") return null;
    if (/^H[1-6]$/.test(tag)) return "heading";
    if (el.hasAttribute("onclick") || el.getAttribute("tabindex")) return "clickable";
    return null;
  };

  const labelledBy = (el) => {
    const ids = el.getAttribute("aria-labelledby");
    if (!ids) return "";
    return norm(ids.split(/\s+/).map((i) => (idFor(i) ? textOf(idFor(i)) : "")).join(" "));
  };
  const explicitLabel = (el) => {
    if (el.id) { const l = doc.querySelector(`label[for="${CSS.escape(el.id)}"]`); if (l) return textOf(l); }
    const wrap = el.closest("label");
    return wrap ? textOf(wrap) : "";
  };
  const cellIndex = (td) => { let i = 0, n = td; while ((n = n.previousElementSibling)) i += (n.colSpan || 1); return i; };
  const isSpacer = (td) => !norm(td.innerText) && !td.querySelector("input,select,textarea,button,a,img");
  const controlFor = (el) => el.closest("td,th");
  const rowLabel = (el) => {
    const td = controlFor(el); if (!td) return null;
    let n = td;
    while ((n = n.previousElementSibling)) {
      if (n.querySelector && n.querySelector("input:not([type=hidden]),select,textarea,button")) break;
      const t = norm(n.innerText);
      if (t) return { text: t.replace(/[:：]\s*$/, ""), relation: "same_row_right" };
    }
    // label in the row above, same column (stacked forms)
    const tr = td.parentElement, idx = cellIndex(td);
    const prev = tr && tr.previousElementSibling;
    if (prev && prev.tagName === "TR") {
      const cells = Array.from(prev.children);
      let acc = 0;
      for (const c of cells) { if (acc === idx) { const t = norm(c.innerText); if (t && !c.querySelector("input,select,textarea")) return { text: t.replace(/[:：]\s*$/, ""), relation: "below" }; } acc += c.colSpan || 1; }
    }
    return null;
  };
  const precedingText = (el) => {
    let n = el;
    for (let hops = 0; hops < 6 && n; hops++) {
      let p = n.previousSibling;
      while (p) {
        if (p.nodeType === 3 && norm(p.nodeValue)) return norm(p.nodeValue).replace(/[:：]\s*$/, "");
        if (p.nodeType === 1 && !p.matches("input,select,textarea,button,br,script,style")) { const t = norm(p.innerText); if (t) return t.replace(/[:：]\s*$/, ""); }
        p = p.previousSibling;
      }
      n = n.parentElement;
      if (!n || /^(TR|TABLE|FORM|BODY)$/.test(n.tagName)) break;
    }
    return "";
  };
  const findLabel = (el) => {
    const ex = explicitLabel(el); if (ex) return { text: ex, relation: "label_for" };
    const rl = rowLabel(el); if (rl) return rl;
    const pt = precedingText(el); if (pt) return { text: pt, relation: "preceding" };
    return null;
  };

  const accessibleName = (el, role) => {
    const aria = el.getAttribute("aria-label"); if (aria) return norm(aria);
    const lb = labelledBy(el); if (lb) return lb;
    const tag = el.tagName;
    if (tag === "INPUT") {
      const t = (el.getAttribute("type") || "text").toLowerCase();
      if (t === "image") return norm(el.getAttribute("alt") || el.getAttribute("title") || el.value || "");
      if (["submit", "button", "reset"].includes(t)) return norm(el.value || el.getAttribute("title") || "");
      const ex = explicitLabel(el); if (ex) return ex;
      const rl = rowLabel(el); if (rl) return rl.text;
      return norm(el.getAttribute("placeholder") || el.getAttribute("title") || "");
    }
    if (tag === "SELECT" || tag === "TEXTAREA") {
      const ex = explicitLabel(el); if (ex) return ex;
      const rl = rowLabel(el); if (rl) return rl.text;
      return norm(el.getAttribute("title") || "");
    }
    if (tag === "IMG") return norm(el.getAttribute("alt") || el.getAttribute("title") || "");
    if (tag === "AREA") return norm(el.getAttribute("alt") || "");
    if (tag === "A" || tag === "BUTTON") {
      const t = textOf(el); if (t) return short(t, 80);
      const img = el.querySelector("img[alt]"); if (img) return norm(img.getAttribute("alt"));
      return norm(el.getAttribute("title") || "");
    }
    if (role === "row" || role === "cell" || role === "columnheader" || role === "clickable" || role === "heading") return short(textOf(el), 80);
    return short(textOf(el), 80);
  };

  // table context (anchor = nearest preceding short bold/heading text before the table)
  const anchorCache = new Map();
  const tableAnchor = (table) => {
    if (anchorCache.has(table)) return anchorCache.get(table);
    let anchor = "";
    const cap = table.querySelector("caption"); if (cap) anchor = textOf(cap);
    if (!anchor) {
      const walker = doc.createTreeWalker(body, NodeFilter.SHOW_ELEMENT);
      let n, last = "";
      while ((n = walker.nextNode())) {
        if (n === table) break;
        if (table.contains(n) || n.closest("table") === table) continue;
        if (n.tagName === "TABLE" || n.tagName === "SCRIPT" || n.tagName === "STYLE") continue;
        const t = ownText(n);
        if (t && t.length <= 40 && !n.querySelector("input,select,textarea") && n.closest("table") !== table) {
          // ignore text that is itself inside another data table's cells
          const outer = n.closest("table");
          if (outer && outer.querySelector("th") && outer.contains(table)) { /* layout table containing ours: fine */ }
          last = t;
        }
      }
      anchor = last;
    }
    anchorCache.set(table, anchor);
    return anchor;
  };
  const isDataTable = (table) => !!table.querySelector("th");
  const headerCells = (table) => {
    const rows = Array.from(table.rows);
    const hr = rows.find((r) => r.querySelector("th")) || rows[0];
    if (!hr) return { row: null, names: [] };
    const names = [];
    Array.from(hr.cells).forEach((c) => { const t = norm(c.innerText); for (let k = 0; k < (c.colSpan || 1); k++) names.push(t); });
    return { row: hr, names };
  };
  const tableContext = (cell) => {
    const table = cell.closest("table"); if (!table || !isDataTable(table)) return null;
    const { row: hr, names } = headerCells(table);
    const tr = cell.parentElement; if (!tr || tr === hr) return { anchor: tableAnchor(table), header: norm(cell.innerText), row_key_header: null, row_key: null, is_header: true };
    const idx = cellIndex(cell);
    const first = tr.cells[0];
    return { anchor: tableAnchor(table), header: names[idx] || null, row_key_header: names[0] || null, row_key: first ? short(norm(first.innerText), 40) : null, is_header: false };
  };

  const stableAttr = (el) => {
    for (const a of ["name", "id"]) {
      const v = el.getAttribute(a);
      if (!v) continue;
      const digits = (v.match(/\d/g) || []).length;
      if (v.length <= 40 && /^[A-Za-z_][\w\-:.]*$/.test(v) && digits / v.length < 0.5 && !/[0-9a-f]{8}-[0-9a-f]{4}/i.test(v)) return [a, v];
    }
    return null;
  };
  const isSubmit = (el) => {
    const tag = el.tagName;
    if (tag === "INPUT") { const t = (el.getAttribute("type") || "text").toLowerCase(); return ["submit", "image"].includes(t) && !!el.closest("form"); }
    if (tag === "BUTTON") { const t = (el.getAttribute("type") || "submit").toLowerCase(); return t === "submit" && !!el.closest("form"); }
    return false;
  };

  // 3) candidates
  const selector = [
    "a[href]", "a[onclick]", "button", "input", "select", "textarea", "area", "label",
    "[role]", "[onclick]", "[tabindex]", "tr[onclick]", "td[onclick]", "img[onclick]", "h1,h2,h3,h4",
    "table th", "table td",
  ].join(",");
  const seen = new Set();
  const out = [];
  let offscreen = 0;
  let local = 0;
  for (const el of Array.from(body.querySelectorAll(selector))) {
    if (seen.has(el)) continue;
    seen.add(el);
    if (el.closest("[data-teller-badge]")) continue;
    const tag = el.tagName;
    // data-table cells only (layout tables are skipped unless the cell is interactive)
    if ((tag === "TD" || tag === "TH") && !el.hasAttribute("onclick")) {
      const table = el.closest("table");
      if (!table || !isDataTable(table)) continue;
      if (el.querySelector("input,select,textarea,button,a[href]")) continue; // the control itself is listed
      if (!norm(el.innerText)) continue;
    }
    if (tag === "TR" && !el.hasAttribute("onclick")) continue;
    if (tag === "LABEL") continue;
    const role = roleOf(el);
    if (!role) continue;
    if (!isVisible(el)) continue;
    const r = el.getBoundingClientRect();
    if (!inViewport(r)) { offscreen++; continue; }
    const type = tag === "INPUT" ? (el.getAttribute("type") || "text").toLowerCase() : null;
    const sensitive = type === "password" || (sensitiveSelectors && el.matches(sensitiveSelectors)) || !!el.closest("[data-teller-mask]") || !!el.querySelector("[data-teller-mask]");
    const attrs = {};
    for (const a of ["name", "id", "href", "alt", "title", "placeholder"]) { const v = el.getAttribute(a); if (v) attrs[a] = short(v, 120); }
    if (type) attrs.type = type;
    if ((tag === "INPUT" || tag === "TEXTAREA") && type !== "password" && !sensitive && el.value) attrs.value = short(el.value, 60);
    if (tag === "SELECT" && el.selectedIndex >= 0) attrs.value = short(el.options[el.selectedIndex].text, 60);
    let text = "";
    if (tag === "INPUT") text = sensitive ? "" : (type === "image" ? "" : short(el.value || "", 60));
    else if (tag === "SELECT") text = attrs.value || "";
    else text = sensitive ? "" : short(textOf(el), role === "row" ? 120 : 80);
    const label = (tag === "INPUT" || tag === "SELECT" || tag === "TEXTAREA") ? findLabel(el) : null;
    const table = (tag === "TD" || tag === "TH") ? tableContext(el) : null;
    el.setAttribute("data-teller-idx", String(local));
    out.push({
      idx: local++,
      role, tag: tag.toLowerCase(),
      name: sensitive && tag !== "INPUT" ? accessibleName(el, role) : accessibleName(el, role),
      text,
      bbox: [Math.round(r.left), Math.round(r.top), Math.round(r.width), Math.round(r.height)],
      input_type: type,
      attrs,
      label: label ? label.text : null,
      label_relation: label ? label.relation : null,
      table,
      stable_attr: stableAttr(el),
      sensitive,
      disabled: !!(el.disabled),
      is_submit: isSubmit(el),
    });
  }
  return { elements: out, text: norm(body.innerText || ""), offscreen, frameset: false };
}
