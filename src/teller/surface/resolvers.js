// resolvers.js — in-frame resolution for locator kinds Playwright has no primitive for.
//
// Called as (opts) => count. Matching elements are stamped with data-teller-resolve="<token>"
// so the Python side can wrap them in a normal Playwright locator and apply the
// exactly-one-visible rule uniformly. opts.kind:
//   "label_anchor" {label, relation, control}
//   "table_cell"   {table_anchor, row_column, row_equals, column}
//   "xpath_anchored" {anchor_text, xpath}
//   "point"        {x, y, verify_text}
//   "clear"        removes every stamp

(opts) => {
  const doc = document;
  const body = doc.body;
  const token = opts.token || "r";
  const norm = (s) => (s || "").replace(/\s+/g, " ").trim();
  const key = (s) => norm(s).toLowerCase().replace(/[:：]\s*$/, "");
  const stamp = (els) => { let n = 0; for (const el of els) { if (el && el.nodeType === 1) { el.setAttribute("data-teller-resolve", token); n++; } } return n; };

  if (opts.kind === "clear") {
    doc.querySelectorAll("[data-teller-resolve]").forEach((e) => e.removeAttribute("data-teller-resolve"));
    return 0;
  }
  if (!body) return 0;

  const CONTROL_SEL = "input:not([type=hidden]),select,textarea,button";
  const controlMatches = (el, kind) => {
    const tag = el.tagName;
    const t = tag === "INPUT" ? (el.getAttribute("type") || "text").toLowerCase() : null;
    switch (kind || "any") {
      case "text_input": return tag === "TEXTAREA" || (tag === "INPUT" && ["text", "search", "email", "tel", "number", "url", "date"].includes(t));
      case "password": return tag === "INPUT" && t === "password";
      case "select": return tag === "SELECT";
      case "checkbox": return tag === "INPUT" && t === "checkbox";
      case "radio": return tag === "INPUT" && t === "radio";
      case "button": return tag === "BUTTON" || (tag === "INPUT" && ["submit", "button", "image", "reset"].includes(t));
      default: return true;
    }
  };
  const cellIndex = (td) => { let i = 0, n = td; while ((n = n.previousElementSibling)) i += (n.colSpan || 1); return i; };
  const cellAt = (tr, idx) => { let acc = 0; for (const c of Array.from(tr.cells)) { if (acc === idx) return c; acc += c.colSpan || 1; } return null; };

  // elements whose own (direct) text equals the wanted text; deepest wins
  const textElements = (wanted) => {
    const w = key(wanted);
    const out = [];
    const walker = doc.createTreeWalker(body, NodeFilter.SHOW_ELEMENT, {
      acceptNode: (n) => /^(SCRIPT|STYLE|NOSCRIPT)$/.test(n.tagName) ? NodeFilter.FILTER_REJECT : NodeFilter.FILTER_ACCEPT,
    });
    let n;
    while ((n = walker.nextNode())) {
      const own = norm(Array.from(n.childNodes).filter((c) => c.nodeType === 3).map((c) => c.nodeValue).join(" "));
      if (own && key(own) === w) out.push(n);
    }
    if (!out.length) {
      // fall back to elements whose full text equals the label (e.g. <td><b>Label</b></td>)
      for (const el of Array.from(body.querySelectorAll("td,th,label,span,b,strong,font,div,p,legend,dt"))) {
        if (key(el.innerText) === w && !out.some((o) => o.contains(el) || el.contains(o))) out.push(el);
      }
    }
    return out;
  };

  if (opts.kind === "label_anchor") {
    const found = [];
    for (const lab of textElements(opts.label)) {
      const rel = opts.relation || "same_row_right";
      if (rel === "label_for") {
        const l = lab.closest("label");
        const forId = l && l.getAttribute("for");
        const el = forId ? doc.getElementById(forId) : (l ? l.querySelector(CONTROL_SEL) : null);
        if (el && controlMatches(el, opts.control)) found.push(el);
        continue;
      }
      if (rel === "same_row_right") {
        const td = lab.closest("td,th");
        if (!td) continue;
        let n = td;
        while ((n = n.nextElementSibling)) {
          const c = Array.from(n.querySelectorAll(CONTROL_SEL)).find((e) => controlMatches(e, opts.control));
          if (c) { found.push(c); break; }
          if (norm(n.innerText)) break; // hit another label
        }
        continue;
      }
      if (rel === "below") {
        const td = lab.closest("td,th"); if (!td) continue;
        const tr = td.parentElement, next = tr && tr.nextElementSibling;
        if (!next) continue;
        const cell = cellAt(next, cellIndex(td));
        const c = cell && Array.from(cell.querySelectorAll(CONTROL_SEL)).find((e) => controlMatches(e, opts.control));
        if (c) found.push(c);
        continue;
      }
      if (rel === "preceding") {
        // first control after the label in document order
        const walker = doc.createTreeWalker(body, NodeFilter.SHOW_ELEMENT);
        walker.currentNode = lab;
        let n;
        while ((n = walker.nextNode())) { if (n.matches(CONTROL_SEL) && controlMatches(n, opts.control)) { found.push(n); break; } }
      }
    }
    return stamp([...new Set(found)]);
  }

  if (opts.kind === "table_cell") {
    const anchorWanted = key(opts.table_anchor || "");
    const found = [];
    const tables = Array.from(body.querySelectorAll("table")).filter((t) => Array.from(t.querySelectorAll("th")).some((th) => th.closest("table") === t));
    // anchor: caption or nearest preceding short text
    const anchorOf = (table) => {
      const cap = table.querySelector("caption"); if (cap) return norm(cap.innerText);
      const walker = doc.createTreeWalker(body, NodeFilter.SHOW_ELEMENT);
      let n, last = "";
      while ((n = walker.nextNode())) {
        if (n === table) break;
        if (n.tagName === "TABLE" || n.tagName === "SCRIPT" || n.tagName === "STYLE") continue;
        const own = norm(Array.from(n.childNodes).filter((c) => c.nodeType === 3).map((c) => c.nodeValue).join(" "));
        if (own && own.length <= 40 && !n.querySelector("input,select,textarea")) last = own;
      }
      return last;
    };
    for (const table of tables) {
      if (anchorWanted && !key(anchorOf(table)).includes(anchorWanted) && !Array.from(table.querySelectorAll("th")).some((th) => key(th.innerText) === anchorWanted)) continue;
      const rows = Array.from(table.rows);
      const hr = rows.find((r) => r.parentElement && r.parentElement.closest("table") === table && r.querySelector("th")); if (!hr) continue;
      const names = [];
      Array.from(hr.cells).forEach((c) => { for (let k = 0; k < (c.colSpan || 1); k++) names.push(key(c.innerText)); });
      const rowIdx = names.indexOf(key(opts.row_column));
      const colIdx = names.indexOf(key(opts.column));
      if (rowIdx < 0 || colIdx < 0) continue;
      for (const tr of rows) {
        if (tr === hr) continue;
        const rc = cellAt(tr, rowIdx);
        if (rc && key(rc.innerText) === key(opts.row_equals)) { const c = cellAt(tr, colIdx); if (c) found.push(c); }
      }
    }
    return stamp(found);
  }

  if (opts.kind === "xpath_anchored") {
    const found = [];
    const anchors = textElements(opts.anchor_text);
    for (const a of anchors) {
      try {
        const snap = doc.evaluate(opts.xpath, a, null, XPathResult.ORDERED_NODE_SNAPSHOT_TYPE, null);
        for (let i = 0; i < snap.snapshotLength; i++) found.push(snap.snapshotItem(i));
      } catch (e) { /* invalid xpath -> zero matches; diagnostics carry the kind */ }
    }
    return stamp([...new Set(found)]);
  }

  if (opts.kind === "point") {
    const el = doc.elementFromPoint(opts.x, opts.y);
    if (!el) return 0;
    const want = key(opts.verify_text || "");
    if (want && !key(el.innerText || el.value || el.getAttribute("alt") || "").includes(want)) return 0;
    return stamp([el]);
  }
  return 0;
}
