"""Curate a run directory into ``/evidence/<name>``.

Copies everything except Playwright traces (they embed unredacted DOM and cannot be scrubbed),
re-runs a regex scrub over every text file as a second line of defence, and writes an
``index.md`` a reviewer can read on GitHub without downloading anything.
"""

from __future__ import annotations

import json
import re
import shutil
from pathlib import Path

TEXT_SUFFIXES = {".json", ".jsonl", ".yaml", ".yml", ".md", ".txt", ".html"}
REFUSED = {"trace.zip"}
SCRUB = [
    (re.compile(r"\b\d{3}-\d{2}-\d{4}\b"), "<ssn>"),
    (re.compile(r"\b\d{13,19}\b"), "<card>"),
]


def export_run(src: Path, dst: Path, *, note: str = "") -> str:
    dst.mkdir(parents=True, exist_ok=True)
    for f in src.rglob("*"):
        if f.is_dir():
            continue
        if f.name in REFUSED or f.suffix == ".zip":
            continue
        rel = f.relative_to(src)
        out = dst / rel
        out.parent.mkdir(parents=True, exist_ok=True)
        if f.suffix in TEXT_SUFFIXES:
            text = f.read_text(encoding="utf-8", errors="replace")
            for rx, rep in SCRUB:
                text = rx.sub(rep, text)
            out.write_text(text, encoding="utf-8")
        else:
            shutil.copy2(f, out)
    index = dst / "index.md"
    index.write_text(_render_index(dst, note), encoding="utf-8")
    return str(index)


def _render_index(d: Path, note: str) -> str:
    lines = [f"# Evidence: `{d.name}`", ""]
    if note:
        lines += [note, ""]
    result_path = d / "result.json"
    result = json.loads(result_path.read_text()) if result_path.exists() else {}
    if result:
        lines += ["## Result", ""]
        lines.append(f"- **status**: `{result.get('status')}`")
        lines.append(f"- **mode**: {result.get('mode')}  ·  **run**: `{result.get('run_id')}`  ·  **llm_invoked**: {result.get('llm_invoked')}")
        cap = result.get("capability") or {}
        if cap:
            lines.append(f"- **capability**: `{cap.get('id')}@{cap.get('version')}` ({cap.get('status')})")
        if result.get("goal"):
            lines.append(f"- **goal**: {result['goal']}")
        if result.get("params"):
            lines.append(f"- **params**: `{json.dumps(result['params'])}`")
        if result.get("outputs") is not None:
            lines.append(f"- **outputs**: `{json.dumps(result['outputs'])}`")
        if result.get("outcome"):
            o = result["outcome"]
            lines.append(f"- **business outcome**: `{o.get('code')}` at `{o.get('step_id')}` — {o.get('message', '')}")
        if result.get("failure"):
            f = result["failure"]
            lines.append(f"- **failure**: `{f.get('code')}` at `{f.get('step_id')}` — {f.get('message', '')}")
            if f.get("expected"):
                lines.append(f"  - expected: `{f['expected']}`")
            if f.get("observed"):
                lines.append(f"  - observed: `{json.dumps(f['observed'])[:400]}`")
            if f.get("screenshot"):
                lines.append(f"  - screenshot: ![fail]({f['screenshot']})")
            if f.get("dom_snapshot"):
                lines.append(f"  - dom snapshot: `{f['dom_snapshot']}`")
        if result.get("declined"):
            dd = result["declined"]
            lines.append(f"- **declined**: `{dd.get('code')}` at `{dd.get('step_id')}` by {dd.get('by')} — {dd.get('note', '')}")
        for r in result.get("recoveries", []):
            lines.append(f"- **recovery**: `{r.get('code')}` ({r.get('condition_id')}) at `{r.get('step_id')}`")
        for w in result.get("warnings", []):
            lines.append(f"- **warning**: `{w.get('code')}` {w.get('message', '')}")
        for h in result.get("handoffs", []):
            lines.append(f"- **handoff**: `{h.get('intervention_id')}` trigger `{h.get('trigger')}` claimed by {h.get('claimed_by')} → resume `{h.get('resume_mode')}` ({h.get('action_count')} human actions)")
        steps = result.get("steps") or []
        if steps:
            lines += ["", "## Steps", "", "| step | status | locator | index | ms | screenshot |", "|---|---|---|---|---|---|"]
            for s in steps:
                shot = f"[{Path(s['screenshot']).name}]({s['screenshot']})" if s.get("screenshot") else ""
                lines.append(f"| {s.get('step_id')} | {s.get('status')} | {s.get('locator_kind') or ''} | {s.get('index_used') if s.get('index_used') is not None else ''} | {s.get('duration_ms', '')} | {shot} |")
        lines.append("")
    shots = sorted((d / "screenshots").glob("*.jpg")) if (d / "screenshots").exists() else []
    if shots:
        lines += ["## Screenshots", ""]
        for s in shots:
            lines.append(f"### {s.stem}")
            lines.append(f"![{s.stem}](screenshots/{s.name})")
            lines.append("")
    files = sorted(p.relative_to(d).as_posix() for p in d.rglob("*") if p.is_file() and p.name != "index.md")
    lines += ["## Files", ""] + [f"- `{f}`" for f in files] + [""]
    return "\n".join(lines)
