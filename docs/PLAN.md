# FINAL BUILD PLAN — `teller`: discovery-to-replay computer use for a legacy bank console

**Revision 2 (post-critique).** Base: Proposal D ("Marks-and-Bundles"). Grafted in: A's per-artifact disposition, app-profile layer, discriminated-union result and file-based command channel; B's `label_anchor` locator, `idempotent` step flag, per-locator diagnostics and intervention-id-checked resume; C's sequencing (the real LLM run lands before any replay code) and the refuse-only irreversible flow.

**What changed in this revision** (each item resolves a critic finding; the design changed, not just the caveats):

1. **Classification pipeline rebuilt** (Section 6): a *wait-race* evaluates declared business outcomes, recoverables and detectors on every 250 ms poll *after* the act, so `at_steps` means "the state this step produced" and not-found / 403 classify within one poll instead of via an 8 s timeout. A seeded member with no Share Savings account and a declared `NO_SAVINGS_ACCOUNT` outcome at s5 remove the hero flow's not-found-as-failure conflation. Validation outcomes are declared on specific message text; generic red text is a failure signal.
2. **Terminal statuses are exactly four:** `SUCCESS`, `BUSINESS_OUTCOME`, `FAILURE`, `HALTED` (a human decided, or policy required a human who was not there). `NEEDS_HUMAN` is no longer a status or an exit code; it is a *phase* in `state.json` while the process waits on the same page object. One active-time clock that pauses during human phases replaces the three disagreeing clocks.
3. **Waits** use URL-change / text predicates, never `wait_for_load_state("load")`; the emitter never emits `navigation` waits and its inference rules are written down (Section 5, rules E1–E8) with a fixture test.
4. **Risk classes renamed and re-scoped:** `read` / `write_reversible` / `write_irreversible`; opening a sub-account is `write_irreversible` (a regulated record, an account number consumed). Route patterns apply only to *submitting* actions, so the model can reach a form and be refused at the button. Policy is the only place risk can be relaxed; the artifact can only tighten.
5. **Scope pre-cut where the overrun would start:** `coords_verified` and `ax_path` designed-only (six locator kinds built); chaos modes cut from eleven to six; two templates fewer; F3 hand-authored and labelled `discovered_by: human` (no F3 discovery run); F4 discovery refusal moved to stretch; `suggested_steps.yaml` designed-only; operator page is one static page; `approve` stretch only if Day 5 starts ahead. P0 re-estimated to 8 h, P1 to 7 h (no resolvers in P1). Total **48 h planned, ~43 h floor**.
6. **Tenant override example moved to `tenants/example-b.yaml`** (`local` replays unmodified) with defined list-merge-by-id semantics and `locators_prepend`; one unit test.
7. **The headless "human" is defined:** a second Playwright client connected over CDP to the same Chromium clicks in the same tab. This makes `test_handoff_cli.py` implementable and turns `session_endpoint()` into a demonstrated same-session control transfer.
8. **Goal + target are explicit inputs:** `teller discover --goal goals/<name>.yaml --tenant local [--entry-url URL]`; the goal file declares the natural-language goal, entry URL, typed params and typed outputs. *The contract is declared up front; the model discovers the flow.*
9. **Cassette made honest:** turns keyed by observation hash, loud mismatch, F2 templates frozen by a hash test after the real run; playback sits on a cut line.
10. Cleaned: `pii_high` masked in the result example; hero handoff resumes with `skip_step` (modes defined, `auto` default); the API defence carries no Bedrock claim and no "strongest effort" claim (`high` is the default; `xhigh` is the documented escalation); `tool_choice: any` is smoke-tested on the first API call with the `auto` + prose-turn branch kept behind a flag.
11. Write-up budgeted: the seven heading strings are copied verbatim into `REPORT.md` in P0 with a per-heading page budget; section 7 is written on Day 5 from a cuts log kept during the build.

Every Section 3 requirement lands as a thin-but-real vertical slice. The non-negotiable — a genuine `claude-opus-5` run driving a live surface, with evidence in `/evidence/` — lands around hour 21 (morning of Day 3 at ~9.5 h days); the repo is submittable from that point on. README.md, REPORT.md and the discovery evidence are never cut.

---

## 1. Decisions

| # | Decision | Choice | Why | Alternative rejected |
|---|---|---|---|---|
| 1 | Language / runtime | Python 3.12 (Homebrew), `pydantic` v2, `typer`, `pytest`, `ruff`; `uv` or venv + `pyproject.toml`. | The candidate just shipped a Playwright Python CLI with pytest; every line is defensible. Pydantic makes the artifact schema a typed object with `model_json_schema()` exported to `/schema/`. | Go (community Playwright bindings; interview time goes to the binding). TypeScript/React (candidate's Playwright depth is in Python; the operator page is too small to justify a second toolchain). |
| 2 | Computer-use technology | Playwright **sync** API driving Chromium; headed for attended runs, headless for tests/CI; Chromium launched with `--remote-debugging-port` so a second client can join the same session. | Frame model, actionability waits, `page.on("dialog")`, `page.route`, `context.expose_binding`, `connect_over_cdp`. Chromium is cached locally. | Selenium (weaker frame/dialog ergonomics, no route interception). Hosted computer-use tool (coordinate-only output yields unreviewable targeting). CUA/agent SDKs (hide the policy seam). |
| 3 | LLM provider / model | Anthropic `claude-opus-5` via the official Python SDK 1.x, **manual** `messages.create` loop. | Cost of one run is irrelevant; flakiness of the single mandatory run is the #1 risk, so use the most capable Opus-tier model. The manual loop is ~80 lines and lets the PolicyGate, recorder and stuck detector sit between *decide* and *act*. | Sonnet/Haiku (saving cents on the run that matters). SDK `tool_runner` (owns the loop we need to own). Frameworks (a black box in the interview). |
| 4 | Request shape (verified against the current API reference) | `thinking={"type":"adaptive"}` (the Opus 5 default, passed explicitly for readability); **no `output_config`** — effort stays at its default `high`, with `xhigh` as the documented escalation if a run flails; `max_tokens=16000`; `tool_choice={"type":"any","disable_parallel_tool_use":True}`; every tool `strict: True` with `additionalProperties: false`; one `cache_control` breakpoint after `tools` + `system`; screenshots as JPEG q70; only the two most recent screenshots kept as images. Refusals: `client.beta.messages.create(..., betas=["server-side-fallback-2026-07-01"], fallbacks="default")` behind a flag defaulting on; `stop_reason == "refusal"` is also a stuck trigger. **`scripts/smoke_api.py` is the first API call of P2**: one request with exactly this shape asserting a `tool_use` block; if forced tool choice is rejected, `TELLER_TOOL_CHOICE=auto` enables the kept prose-turn branch (a text-only turn is answered with "Reply with exactly one tool call" and counts toward `NO_PROGRESS`). | `any` + `disable_parallel_tool_use` gives exactly one policy-checkable action per turn; `strict` removes argument parse failures; 16000 is the non-streaming default and avoids truncating thinking + tool call. Each parameter has this one-line justification for the interview and nothing more is passed. | `max_tokens=4096` (truncation on the run everything depends on). Passing `output_config.effort: high` (it is the default — a parameter to defend for nothing). Streaming (not needed at 16000). |
| 5 | Perception | Hybrid: a viewport screenshot with **numbered badges injected into each frame's own DOM** by `marks.js`, plus a compact element table `[id] role 'name' text=… frame=…` across frames, plus page facts (URL, title, pending dialog text, visible-text digest). The model acts by mark id, never by coordinates or selectors. | Works without a clean DOM. The browser positions the badges inside each frame, so **no frame-offset math and no Pillow**. Deterministic numbering (frame order, y, x) makes retries and cassettes stable. Perception is still a DOM walk — the no-DOM story lives in the Surface seam (Section 10) and is owned as such. | Pillow-drawn overlay (frame geometry). Coordinates only (unreplayable targets). Text-only inventory (badges are free once drawn in-DOM). |
| 6 | Target application | Self-built **Ledgerline Credit Union — Member Servicing Console** (FastAPI + Jinja2 at `http://127.0.0.1:8600`): frameset, table layout, `<font>` tags, no ids/test-ids, cryptic control names, `javascript:` links, `<input type=image>`, native `confirm()`, idle-session cookie, `/__chaos` one-shot fault API with **six** modes. | Section 4 invites a hostile surface; 3.3 says the interesting failures are runtime conditions. Only a mock triggers every condition deterministically, reproducibly for the grader, with zero ToS/PII exposure. It doubles as the pytest fixture. | Public demo sites (no fault injection, clean DOM). A desktop app (macOS AX tooling eats the budget). |
| 7 | Flows | **F1** harness login (never the model). **F2** read savings balance — the discovery + replay hero, the only *emitted* artifact. **F3** open sub-account — a **hand-authored** artifact labelled `provenance.discovered_by: human`; its replay exercises the `write_irreversible` confirmation gate, a declared `confirm()` dialog, `idempotent: false`, and the `VALIDATION_REJECTED` / `ALREADY_EXISTS` outcomes. **F4** a "Post Transaction" form exists on member detail so the risk classifier has a live irreversible target; a discovery run that is refused at "Post" is a Day-4/5 **stretch**, not a plan item. | One emitted hero, one write-path artifact whose provenance is honest, and the cheapest possible irreversible target. The critic's point stands: a hand-written artifact next to an emitted one muddies the story unless labelled — so it is labelled in the file and in `evidence/README.md`. | Four replayable flows (breadth the brief does not reward). F3 discovered by the model (a second flaky LLM run on Day 4). |
| 8 | Artifact schema & storage | YAML, one file per `<id>@<semver>.yaml` under `/capabilities/`, validated on load by pydantic, JSON Schema exported to `/schema/`. **Three layers:** app profile (vendor login routine + generic detectors) → capability (flow, params, outputs, declared outcomes) → tenant overrides (merge-by-id at load). Provenance points to the redacted transcript and the emitted draft by hash. **Each layer is exercised by exactly one test and one example**, and REPORT says so in one sentence — that is the whole defence against "multi-tenant plumbing". | YAML with comments is what a human diffs; JSON Schema is what a machine validates. The profile layer means a capability declares only flow-specific outcomes; the override layer means a tenant never triggers a re-record. | JSON-only (weaker review story). Embedding the transcript. Per-tenant copies. |
| 9 | Locator strategy | Ordered `LocatorBundle` per target. **Six built kinds:** `role_name` → `label_anchor` → `text_exact` → `attr_stable` → `table_cell` → `xpath_anchored`. **Designed-only:** `coords_verified`, `ax_path` (in the schema and the desktop mapping; the resolver raises `UnsupportedLocatorKind` and the kind is skipped; the recorder never emits them). Frame-scoped; per-locator robustness note; exactly-one-visible rule with a fingerprint filter; `index_used` recorded, `> 0` → `DRIFT_WARNING`; failure carries per-locator diagnostics. | `get_by_label` cannot resolve a sibling-`<td>` label — B's geometric `label_anchor` is *the* legacy case. "Never guess" is the right rule for a bank. `coords_verified` was already "never trusted for unattended replay" — building it bought nothing but hours. | ≥2-locator agreement (fails on its own mock). Perceptual-hash visual locator. Raw CSS/XPath only. |
| 10 | Waits & checkpoints | Every step declares `wait_for` ∈ {`text`, `selector`, `url`} with `timeout_ms` (default 5000) and `expect`. The wait is a **predicate race** (Section 6) that also evaluates outcomes, recoverables and detectors each poll. `url` means "the frame's URL differs from the URL captured at act time and matches the pattern" — never `wait_for_load_state("load")`, which returns immediately on an already-loaded frame. The emitter emits `text` waits only. Bounded remedies with `max_times`. **One clock:** the artifact's `budget.max_active_seconds` (F2: 120) counts automation time only and **pauses** in `AWAITING_HUMAN` / `HUMAN_IN_CONTROL`; handoff deadlines live in the policy. No `networkidle`, no sleeps; fixed viewport 1280×800, `locale`, `timezone_id`, `reduced_motion`. | Declared deadlines mean a run satisfies the same predicates or fails at the same step. Legacy pages poll, so `networkidle` never settles. `load` on a `javascript:` form submit inside the main frame was a real flake source. | `wait_for_load_state`; `networkidle`; fixed sleeps; a wall clock that ticks while a human is typing. |
| 11 | Outcome classification | A's **per-artifact disposition** under D's **one-sentence rule**: *the app said no to the DATA → business outcome; a declared, bounded remedy fixed it → recovery; a human decided, or policy required one who was absent → halted; anything else that stops the run → failure.* A detector is a business outcome only where the artifact declares it (`at_steps`), evaluated in the **post-act race** of that step (and, for `read` steps, when resolution fails). The same detector elsewhere is `FAILURE/UNDECLARED_CONDITION`. Outcome detectors match **specific message text**, never "any red text". | Classification is a property of the artifact, not the engine; every enum value has a mock trigger. `ACCESS_DENIED` is an outcome at s4 and a failure at s3 — the answer to "why is 403 an outcome here and a failure there". A human saying no is a policy result the caller branches on, not a bug next to `SURFACE_CRASHED`. | Hard-coding 403 as failure. Every detector hit as failure. Declined/aborted as failures. Red-text validation detectors (turn automation typos into "business outcomes"). |
| 12 | Retry & re-auth safety | Each step carries `idempotent` (B). After a timeout the engine **re-observes without re-acting**; it re-acts (once) only if the step is idempotent. Session re-auth runs the profile login and **restarts from `restart_anchor`** only if no non-idempotent step has executed; otherwise `FAILURE/REAUTH_UNSAFE` (escalatable). | Avoids double-posting after a timeout or expiry — the flaw in A's `resume_from: current_step` and C's blind retry. | Resume mid-flow after re-login. Blind retry. |
| 13 | Architecture & boundaries | **One CLI process** owns Playwright on the main thread. The mock app is a second local process. The operator console is a **stdlib `ThreadingHTTPServer` on a background thread that touches only files**. The automation thread waits for a command by polling `commands.jsonl` every 250 ms **inside `page.wait_for_timeout(250)`**. A human — real or scripted — acts in the same Chromium, either in the headed window or as a **second Playwright client over CDP**. | Sync Playwright dispatches page events only while the main thread is inside a Playwright call; blocking on a `threading.Event` drops every human action. Files as the channel are inspectable and are the same seam a queue would use later. CDP is what a remote operator console would attach to. | Queue/DB/websocket (scaling infra the brief penalises). Separate operator process (the file seam already allows it). |
| 14 | Control-transfer model | `controller ∈ {automation, human, none}` + a `ControlToken` checked inside `Surface.act()` (raises `ControlViolation`). Phases: `RUNNING → STUCK_EVALUATING → AWAITING_HUMAN → HUMAN_IN_CONTROL → HANDBACK_VERIFYING → RUNNING`, persisted to `runs/<id>/state.json` on every transition. Resume carries the pending `intervention_id` (stale → 409). Modes `auto` (default) \| `retry_step` \| `skip_step` \| `complete`, each verified against a named predicate. Bounded to `max_handoffs: 2`. Outputs are extracted by automation after handback, never typed by the human. | Enforced in code, not convention; `auto` removes the operator's need to know the difference between "I redid it" and "I unblocked it". | Trusting the human's "done". Unbounded re-escalation. A terminal `NEEDS_HUMAN` exit that cannot resume a live page. |
| 15 | Handoff hero demo | Chaos `interstitial_unknown`: s1's click lands on a full-page "Attestation Required" screen the artifact does not declare → `CHECKPOINT_FAILED` at s1 → pause → operator clicks "I attest" in the same Chromium (the attest page then continues to Member Search) → `resume --mode auto` → `skip_step` verified (s1's `expect` holds) → `RUNNING` at s2 → `SUCCESS` with `escalations[1]` and `human_actions.jsonl`. | Unambiguous manual action; no dependence on parked-dialog clickability. `skip_step` is the correct mode because the human's click produced s1's postcondition. | `confirm()` as the hero. Session-expired as the hero (auto-recovered here). `retry_step` (s1 has no previous step and the page is already past it). |
| 16 | Risk classes & policy | `read` → proceed; `write_reversible` → proceed with WARN + before/after screenshots; `write_irreversible` → **never performed by automation without a human decision**: discovery blocks and raises `IRREVERSIBLE_STEP`; replay pauses for confirmation unless the caller pre-authorised that exact `step_id@version` (`--confirm-step c5@1.0.0`). **Only submitting actions can be writes** (a GET-form submit is `read`; typing is `read` until submit); an unclassified POST submit defaults to `write_irreversible`; the policy file is the only place a class can be relaxed (`explicit_elements`), the artifact can only tighten. Opening a sub-account is `write_irreversible`. | "Flag only" performs the action; "block always" makes the system useless for postings. A pause with a human decision, or a version-pinned pre-authorisation from an agent that holds user consent, is the only handling that is useful and defensible for a bank. "Reversible in my mock" is not a production argument, so the mock's Close button no longer decides the class. | Block-always. Flag-only. Unpinned `--approve`. Classifying by mock affordances. |
| 17 | Redaction | **Classification first, regex as backstop**, through **one `Redactor` used by every writer**. Values bound to `pii_high`/`secret` params, outputs, `sensitive_selectors` and header-anchored `sensitive_columns` are masked wherever they appear; regexes are only `ssn` and `card` and only on free-text fields. Screenshots are blacked out **in the DOM** before capture (`[data-teller-mask]` CSS injected per frame) with Playwright `mask=` as belt-and-braces on top-level locators; DOM snapshots strip input values; artifacts hold `{param: …}` references; `trace.zip` is off by default and never committed. A negative test proves confirmation ids and run ids survive. | Single writer path = no route to disk that skips redaction. A `\d{9,12}` regex that eats confirmation ids is not "appropriate" redaction. | Pillow bbox blackout. Committing traces. Digit-run regexes as the primary mechanism. |
| 18 | Sequencing | P0 mock → P1 surface + gate + log (+ three 15-min spikes) → **P2 real LLM run** → P3 replay → P4 handoff → P5 hardening → P6 write-up. | The non-negotiable retires before replay exists. The three unknowns that would change later design (DOM blackout vs `mask=` across frames, parked-dialog clickability, a second CDP client alongside `expose_binding`) are answered on Day 1, not Day 4. | Front-loading redaction v2 and the network fence ahead of the run. Verifying dialogs in P4. |
| 19 | Stretch | Confidence & approval, minimal: `teller approve` records approver + artifact hash; `--unattended` refuses `draft`. **Only if Day 5 starts ahead of plan.** | ~2 h, reuses schema fields the core already needs. | Agent catalog, assisted LLM fallback, tenant-B skin, multi-run stability — all "next" in REPORT 7. |
| 20 | Rich failure signal | `<step>_fail.png` (redacted) + `<step>_fail.html` (values stripped) + the resolved-locator attempt table. | Debuggable, redactable, committable. | `trace.zip` as the committed signal. |
| 21 | Offline story for the grader | Replay and handoff demos **never** need a key: `make mock && make demo-replay`, `make handoff-demo` (scripted CDP human, headless). Discovery needs a key; its evidence is committed. `teller discover --offline --cassette …` replays recorded model turns through the real loop and browser, keyed by observation hash with a loud, explained mismatch; F2 templates are frozen by a hash test after the real run. Playback is a P5 cut-line item. | Graders may lack a key or a display. A cassette that silently drifts is worse than none. | A scripted FakeLLM separate from the cassette. Recording the cassette last (then it is not the genuine run). |
| 22 | Goal + target as input | `goals/<name>.yaml` declares `goal` (natural language with `{param}` slots), `capability_id`, `app_profile`, `entry_url`, typed `params`, typed `outputs`. `teller discover --goal … --param member_id=10001 --tenant local [--entry-url URL]`. The tenant supplies `base_url`; `--entry-url` overrides the goal's entry. | Makes 3.1 visible in one command, and makes the agent-facing contract a *declared* thing the model must satisfy (`done()` is refused until every declared output is bound by a `read`) rather than something inferred from a transcript. | Letting the model invent params/outputs. Deriving the target from the tenant alone. |

---

## 2. Architecture

### Components and boundaries

| Component | Package | Owns | Must not |
|---|---|---|---|
| CLI | `teller/cli.py` | `discover`, `replay`, `chaos`, `intervene`, `policy check`, `evidence export`, `schema export`, `approve` | contain logic |
| Surface | `teller/surface/` | `Surface` Protocol; `WebPlaywrightSurface` — **the only module that imports `playwright`**; `dom/common.js`, `dom/marks.js`, `dom/recorder.js`, `dom/resolve.js` | know about artifacts, the LLM or policy |
| Discovery | `teller/discovery/` | observe→decide→act loop, strict tools, prompts, stuck detectors, recorder, emitter (rules E1–E8), cassette | execute an action without `PolicyGate.check` |
| Artifact | `teller/artifact/` | pydantic models (capability, profile, tenant, goal), `ArtifactStore.load(path, tenant)` (merge-by-id → validate), save, hash | — |
| Replay | `teller/replay/` | LLM-free interpreter, wait-race, detectors, classification, result contract | import `anthropic` (asserted by a test) |
| Policy | `teller/policy/` | `PolicyGate` (single call site inside `Surface.act`), `RiskClassifier`, `Redactor`, route fence | — |
| HITL | `teller/hitl/` | `ControlToken`, phases, `state.json`, `InterventionRequest`, operator server (stdlib http, files only) | import `playwright` (asserted by a test) |
| Evidence | `teller/evidence/` | JSONL `EventLog`, redacted screenshot saver, `export` (+ `REVIEW_DIFF.md`) | — |
| Mock app | `mockapp/` | Ledgerline console, seed data, `/__chaos` | be reachable by the agent at `/__chaos` (policy denies; test asserts) |

### Data flow

1. **Discovery:** `goal file + params + tenant + policy` → harness login (credentials from env; the model never sees them) → loop: `Surface.observe()` → Messages API → one tool call → `PolicyGate.check` + `RiskClassifier` → `Surface.act(token)` → recorder captures the acted element's description (→ `LocatorBundle`) and the before/after observations → … → `done()` (refused until every declared output is bound) → `emit.py` applies E1–E8 and writes `capabilities/<id>@<ver>.yaml` (`status: draft`) + `runs/<run_id>/`. A stuck trigger writes `intervention.json`; in P2 the run then ends `FAILURE` with an `escalations[]` record (detect-and-route); attended discovery resume is a P4 cut-line item.
2. **Replay:** `artifact + params + tenant` → preflight (`PARAM_INVALID`, merge + validate, `policy check`, version range, approval state) → login → per step: pre-sweep → resolve → gate → act → **wait-race** → expect → extract → final checkpoint → typed outputs → `result.json` + exit code. A pause writes `intervention.json` and `state.json`, and the process waits on the same page.
3. **Handoff (either mode):** trigger → `InterventionRequest` → operator claims → human acts in the same Chromium (headed window, or a CDP client) while `recorder.js` streams to `human_actions.jsonl` → `resume {mode, intervention_id}` → `HANDBACK_VERIFYING` → continue on the same page object → one `result.json` at the end.

### ASCII diagram

```
 goals/<name>.yaml + --param …            capabilities/<id>@<ver>.yaml + --param …
          │                                            │
          ▼                                            ▼
┌───────────────────────────── teller (one Python process, Playwright on the main thread) ─────────────────────────────┐
│  cli.py (typer)                                                                                                      │
│   ├─ discover ─▶ discovery/loop.py ──▶ Anthropic Messages API  claude-opus-5  (strict tools, one action/turn)         │
│   │                 │ observe ◀─── Observation {screenshot+badges, element table, page facts}                          │
│   │                 │ decide  ──▶ tool_use(mark_id, intent, …)                                                        │
│   │                 │ act     ──▶ policy/gate.py + risk.py ──▶ surface/web_playwright.py.act(token) ──▶ Chromium ─┐    │
│   │                 └ recorder ──▶ discovery/emit.py (E1–E8) ──▶ capabilities/<id>@<ver>.yaml  (status: draft)  │    │
│   ├─ replay ───▶ replay/executor.py  (no `anthropic` import — tested)                                            │    │ HTTP
│   │                 pre-sweep → resolve → gate → act → WAIT-RACE{dialog|outcome|recoverable|detector|target}     │    ▼
│   │                 → expect → extract → checkpoint ──▶ classify.py ──▶ result.json {success|business_outcome|  │  mockapp/ (uvicorn :8600)
│   │                                                                                 failure|halted}             │  Ledgerline frameset console
│   ├─ hitl/state.py   controller ∈ {automation, human, none} + ControlToken (checked in Surface.act)              │  + /__chaos (policy-denied)
│   │      ▲ automation thread polls runs/<id>/commands.jsonl every 250 ms inside page.wait_for_timeout()          │
│   │      │ active-time clock paused while controller ≠ automation; state.json rewritten on every transition      │
│   └─ hitl/operator_server.py  stdlib ThreadingHTTPServer :8787, background thread, files only (no Playwright)   │
│              ▲ GET /interventions/{id}   POST claim | resume | abort | confirm | decline  (409 on stale id)       │
└──────────────┼──────────────────────────────────────────────────────────────────────────────────────────────────┼────┘
   operator's browser / `teller intervene` CLI            Chromium --remote-debugging-port=9333 ◀── second client (headed human,
                                                                                                   or test/demo via connect_over_cdp)
   runs/<run_id>/ ── events.jsonl, state.json, screenshots/, result.json, intervention.json, commands.jsonl,
                     human_actions.jsonl, transcript.redacted.json, cassette.json
                 └── teller evidence export ──▶ /evidence/<name>/ (+ index.md, REVIEW_DIFF.md)
```

Every arrow into the browser passes through `PolicyGate.check` and `ControlToken`; every arrow onto disk passes through `Redactor`. `events.jsonl` is the source of truth; `state.json`, the operator page and `result.json` are projections of it.

---

## 3. Target application

**Ledgerline Credit Union — Member Servicing Console** (`mockapp/`, FastAPI + Jinja2, `uvicorn mockapp.app:app --port 8600`). Server-rendered HTML 4.01-style: `<frameset cols="180,*">` with frames `nav` and `main`; nested `<table>` layout with spacer cells; `<font color=red>` for errors; controls named `txtF1`, `cmdGo`, `sel3`; labels are sibling `<td>`s; links as `href="javascript:doSubmit('cmd1')"`; submit as `<input type=image alt="Go">`; a session cookie with `LEDGERLINE_IDLE_SECONDS` (default 600). All data is synthetic (fictional names, 5-digit member numbers, no SSNs stored; one `SSN (last 4)` column exists purely to exercise masking). Thirteen templates share one legacy base.

### Screens

| Route (main frame unless noted) | Purpose | Notable hostility |
|---|---|---|
| `/login` | username/password form (POST) | password masked in every screenshot; harness-only |
| `/console` | frameset shell | frames `nav` + `main` |
| `/console/nav` (frame `nav`) | links: Home, Members, Reports | `javascript:` hrefs |
| `/console/home` | landing; may show **Compliance Notice** (known interstitial) | `Acknowledge` button |
| `/console/members/search` | `Member #` (txtF1) or `Last name` (txtF2), `Go` image button (**GET** form) | sibling-td labels |
| `/console/members/results?q=…` | header "Search Results"; results table with `<tr onclick>`; or "No members matched your search." | click on row, not link |
| `/console/members/{id}` | Member Detail: name, status, `SSN (last 4)`, **Accounts** table (Product, Account #, Current Balance, Opened), links `Open Sub-Account`, `Post Transaction` | header-anchored cells |
| `/console/members/{id}/subaccounts/new` | product `<select>`, nickname, initial deposit; "Open" (**POST**) fires `confirm('Open this sub-account?')` | native dialog; `write_irreversible` |
| `/console/members/{id}/subaccounts/{n}/confirmation` | "Sub-Account Opened": new account number, confirmation id `CNF-YYYYMMDD-NNNN` | F3 checkpoint; a 12-digit-free id so the redaction negative test has a target |
| `/console/members/{id}/transactions/new` | amount, type, **Post** (POST) | irreversible target; refused |
| `/console/attest` | **Attestation Required** with "I attest"; on click continues to the originally requested page | hero handoff |
| `/login?reason=expired` | "Your session has expired" | detector target |
| `/error/{403\|500}` (one template) | "You are not authorized to view this member (ERR-4031)" / "Application Error ORA-00600: internal error code" | ACCESS_DENIED / APP_ERROR |

### Seed data

`10001 Dana Whitfield` (happy path: Share Savings `0004411982`, balance `$2,431.17`; Checking; nickname `Vacation` pre-existing → `ALREADY_EXISTS`), `10002`, `10003` (extra rows so tables are non-trivial), **`10004 Marcus Oyelaran` (Checking only — no Share Savings → `NO_SAVINGS_ACCOUNT`)**, `20002` absent (not found), `30003 Restricted Branch` (403 on detail). Product limits: initial deposit > 10000 → "Initial deposit exceeds the product limit"; empty nickname → "Nickname is required".

### Runtime error states and how each is triggered

Chaos is armed with `POST /__chaos {mode, times}` (`teller chaos arm <mode> [--times N]`, `teller chaos reset`); every mode is **one-shot per arm**; state is an in-memory dict. `/__chaos` is on the policy deny list and a test asserts the agent cannot navigate to it. **Six modes**, each tied to evidence or a test; data-driven states need no chaos.

| Condition (brief 3.3) | Trigger | What the surface shows | Disposition |
|---|---|---|---|
| Validation error | data-driven on F3: deposit > 10000 or empty nickname | form re-renders with the **specific** red message | `BUSINESS_OUTCOME/VALIDATION_REJECTED` at c5 (declared on that text); any *other* red text is `FAILURE/UNDECLARED_CONDITION(validation_error_generic)` |
| Record not found | member `20002` | "No members matched your search." | `BUSINESS_OUTCOME/MEMBER_NOT_FOUND` at s3 (one poll after the page lands) |
| Record exists, requested data absent | member `10004` | Accounts table without a Share Savings row | `BUSINESS_OUTCOME/NO_SAVINGS_ACCOUNT` at s5 |
| Permission denial | member `30003` | 403 page in main frame | `BUSINESS_OUTCOME/ACCESS_DENIED` at s4; the same page at s3 would be `FAILURE/UNDECLARED_CONDITION(access_denied)` |
| Known interstitial | chaos `interstitial_known` | next navigation renders Compliance Notice | `RECOVERY/INTERSTITIAL_DISMISSED` |
| Unknown interstitial | chaos `interstitial_unknown` | next navigation renders Attestation Required | `FAILURE/CHECKPOINT_FAILED` → pause (hero handoff) |
| Expected dialog | F3 c5's `confirm()` | native confirm | handled as declared on the step (`dialog: {expect_text, respond: accept}`); logged, not a recovery |
| Unknown dialog | chaos `dialog_unknown` | `confirm()` with randomised text on next page load | dismissed (cancel) → `FAILURE/UNEXPECTED_DIALOG` → escalatable |
| Session timeout | chaos `expire_session` (or idle) | next request redirects to `/login?reason=expired` | `RECOVERY/SESSION_REESTABLISHED` (no non-idempotent step yet) or `FAILURE/REAUTH_UNSAFE` |
| Slow / failed load | chaos `slow --times N` (7 s server delay, no interim page) | frame still loading at `timeout_ms` | `RECOVERY/SLOW_LOAD_WAITED` via `frame_loading` detector (should); `--times 4` exhausts `max_times: 3` → `FAILURE/STEP_TIMEOUT` (must) |
| App error | chaos `app_error` | 500 ORA-00600 page | `FAILURE/APP_ERROR` |
| Target missing (drift) | test fixture only (a locator bundle pointing at a label that does not exist) | normal page | `FAILURE/TARGET_NOT_FOUND` with all-locator diagnostics |
| Duplicate | F3 nickname `Vacation` for 10001 | "Nickname already in use" | `BUSINESS_OUTCOME/ALREADY_EXISTS` at c5 |

Removed from the previous revision (no evidence folder needed them): `blank_page`, `dialog_known`, `permission_denied`, `validation_error` chaos modes; the `loading.html` interim page; the profile's `session_expiry_alert` recoverable.

---

## 4. Surface abstraction

### Typed interface sketch (`teller/surface/base.py`)

```python
from typing import Protocol, Literal, Optional
from pydantic import BaseModel

LocatorKind = Literal["role_name", "label_anchor", "text_exact", "attr_stable",
                      "table_cell", "xpath_anchored",            # built
                      "coords_verified", "ax_path"]              # designed-only: schema + docstring, resolver raises UnsupportedLocatorKind
SurfaceKind = Literal["web", "web_legacy", "desktop"]

class Element(BaseModel):            # one row of the element table the model sees
    mark_id: int                      # deterministic: sorted by (frame order, y, x)
    role: str                         # explicit or implied from tag/type
    name: str                         # computed accessible name (aria-label, label-for, alt, title, text)
    text: str                         # visible text, trimmed to 40 chars, redacted if sensitive
    frame: str                        # frame path, e.g. "main" or "" for top document
    bbox: tuple[int, int, int, int]   # frame-relative; diagnostics only
    sensitive: bool                   # password field / classified param or output / sensitive column / regex hit
    submits: bool                     # type=submit|image, button inside a form, javascript:doSubmit(...), onclick containing .submit()
    form_method: Optional[Literal["get", "post"]]   # of the enclosing form, if any — the RiskClassifier's first input
    table_ctx: Optional[dict]         # {table_anchor, header, row_key} when inside a table with a header row

class Observation(BaseModel):
    screenshot_jpeg: bytes            # already blacked out (DOM) and badged (marks.js)
    elements: list[Element]
    url: str; title: str
    dialog: Optional[dict]            # {"type": "confirm", "message": "..."} when a dialog is parked
    last_document_status: dict[str, int]   # frame -> HTTP status of its last document response (from page.on("response"))
    frame_loading: dict[str, bool]    # frame -> document.readyState != "complete"
    text_digest: str                  # sha256 of visible text; feeds the no-progress detector
    observation_hash: str             # sha256 of the element table (roles, names, text, frame; no bboxes) — the cassette key

class Locator(BaseModel):             # discriminated union on `kind` in the real model
    kind: LocatorKind
    frame: str
    robustness: str                   # one-line note written by the recorder (E2) or the reviewer
    surfaces: list[SurfaceKind]       # which Surface implementations can evaluate it
    # kind-specific fields: role/name, label/relation/control, text/tag, attr/value,
    # table_anchor/row_match/column, anchor_text/xpath, (x/y/viewport/verify_text, ax_path — designed-only)

class LocatorBundle(BaseModel):
    text_hint: str                    # fingerprint used to filter ambiguous matches
    tag_hint: str
    frame: str
    locators: list[Locator]           # ordered; replay tries top to bottom

class Action(BaseModel):              # closed vocabulary shared by discovery tools and artifact steps
    kind: Literal["click", "type", "select", "press", "navigate", "read", "scroll", "dismiss_dialog", "run_subflow"]
    value: Optional[str] = None       # already param-substituted by the caller
    option: Optional[str] = None; key: Optional[str] = None; url: Optional[str] = None
    accept: Optional[bool] = None

class Resolution(BaseModel):
    handle: Optional[object]          # Playwright Locator (opaque to callers)
    index_used: Optional[int]
    diagnostics: list[dict]           # [{kind, matched: n, note}] for every locator tried

class Surface(Protocol):
    def observe(self) -> Observation: ...
    def describe(self, mark_id: int) -> dict: ...          # common.js describe(el): everything needed to build a LocatorBundle
    def resolve(self, bundle: LocatorBundle) -> Resolution: ...
    def act(self, token: "ControlToken", action: Action, handle: object | None) -> "ActResult": ...
    def pending_dialog(self) -> Optional[dict]: ...
    def handle_dialog(self, accept: bool) -> None: ...
    def screenshot(self, path: str, redact: bool = True) -> None: ...
    def dom_snapshot(self, path: str) -> None: ...        # input values stripped
    def inject_recorder(self, sink) -> None: ...          # human-action capture (handoff)
    def detach_recorder(self) -> None: ...
    def capabilities(self) -> set[LocatorKind]: ...       # which locator kinds this surface evaluates
    def session_endpoint(self) -> str: ...                # http://127.0.0.1:9333 — CDP endpoint of the live Chromium
```

`WebPlaywrightSurface` implements all of it. `DesktopSurface` is a typed stub whose methods raise `NotImplementedError` and whose docstring carries the mapping table in Section 10.

### One JS library, four entry points (`teller/surface/dom/`)

`common.js` holds the shared primitives: the interactable selector, accessible-name computation, nearest-label-by-geometry (same-row left `<td>`, preceding text node, `<label for>`), table header context, `submits`/`form_method`, and **`describe(el)`** — the single function that turns an element into the description from which Python's `bundle_from_description()` builds an ordered `LocatorBundle`. `marks.js` (observe), `recorder.js` (handoff capture) and `resolve.js` (`label_anchor` and `table_cell` resolution) each `include` it — Python concatenates the files at import time; there is no bundler. The recorder therefore builds human-action bundles **with the same code discovery uses** because it is literally the same function, not a promise.

### How `observe()` works on a frameset

`marks.js` is evaluated **per frame** (`for f in page.frames`). In each frame it collects visible interactables (`a, button, input, select, textarea, [onclick], [role], tr/td with onclick or cursor:pointer, img inside a, area`), computes role, accessible name, label, stable attributes, table header context, `submits`, `form_method` and a frame-relative bbox, stamps `data-teller-mask` on sensitive nodes (password inputs, `sensitive_selectors`, cells under `sensitive_columns`, controls bound to `pii_high` params/outputs, regex hits in text nodes) and **injects a small absolutely-positioned badge into that frame's own document**. Numbers are assigned globally after collection (frame order, then y, then x) and stamped as `data-teller-mark`. A per-frame `<style>` blacks out `[data-teller-mask]` (`background:#000 !important; color:transparent !important`) before the page screenshot; badges and the style are removed after capture. If a dialog is parked, `page.evaluate` would block, so `observe()` returns a dialog-only observation first. `observation_hash` excludes bboxes so the cassette key survives a one-pixel CSS change.

### Locator strategy and fallback order

| Order | Kind | Recorded from | Robustness note the recorder writes | Surfaces | Status |
|---|---|---|---|---|---|
| 1 | `role_name` | implied/explicit role + accessible name → `frame.get_by_role(role, name=, exact=True)` | "Accessible name is operator-visible text; survives branding and CSS." | web, desktop | built |
| 2 | `label_anchor` | label text + relation (`same_row_right`, `below`, `label_for`) + control kind → `resolve.js` | "Legacy tables carry no `<label for>`; the adjacent-cell label is what vendors keep stable across tenants." | web, desktop | built (JS) |
| 3 | `text_exact` | visible text or `alt` → `get_by_text(exact=True)` scoped to frame, filtered by tag | "Same text, no role dependency." | all | built |
| 4 | `attr_stable` | `name`/`id` passing a stability heuristic (no GUID/digit-heavy values) | "Cryptic but stable within this vendor build." | web | built |
| 5 | `table_cell` | nearest table with a header row → `{table_anchor, row_match: {column, equals}, column}` → `resolve.js` | "Header-name addressed; survives row and column reordering." | web, desktop | built (JS) |
| 6 | `xpath_anchored` | XPath relative to the nearest heading/header text → `frame.locator("xpath=…")` | "Structural; last resort; flags drift when used." | web | built |
| — | `coords_verified` | — | "Only valid on the recorded viewport; never trusted for unattended replay." | all | **designed-only** |
| — | `ax_path` | — | desktop AutomationId / AX path | desktop | **designed-only** |

JS resolvers stamp every match with `data-teller-hit="<nonce>"` and Python wraps `frame.locator('[data-teller-hit="<nonce>"]')`, so all six kinds yield a Playwright `Locator` and the uniqueness rule below is uniform; the attribute is removed after the step.

**Resolution algorithm (`replay/resolve.py`):** for each locator in order, skip kinds the surface does not advertise (recorded in diagnostics as `skipped`); build the candidate set with a 1500 ms actionability wait; **0 matches → next locator**; **exactly 1 visible → resolved** (record `index_used`); **>1 visible → filter by fingerprint (`text_hint`, `tag_hint`)**: exactly one survivor → resolved with `DRIFT_WARNING`, otherwise `TARGET_AMBIGUOUS` — never guess. All locators exhausted → **if the step declares business outcomes, evaluate their detectors first** (this is how `NO_SAVINGS_ACCOUNT` fires at s5); otherwise `TARGET_NOT_FOUND` with the full diagnostics table. `index_used > 0` → `RECOVERY/LOCATOR_FALLBACK_USED` + `DRIFT_WARNING` with a `suggest_override` fragment. Dynamic values inside locators (`text: "10001"`) are canonicalised to `"{member_id}"` at record time and substituted at replay.

---

## 5. Artifact schema

Four file kinds compose one runnable capability: a **goal spec** (input to discovery, the declared contract), the **capability** (the emitted flow), the **app profile** (vendor layer) and the **tenant** (institution layer). Stored as YAML; validated by `teller/artifact/model.py`; JSON Schema exported to `/schema/{capability,app_profile,tenant,result}.schema.json` (a test fails on drift).

### `goals/read_savings_balance.yaml` (the declared contract — input to discovery)

```yaml
schema_version: 1
capability_id: ledgerline.member.read_savings_balance
app_profile: ledgerline-msc
entry_url: "{base_url}/console/home"                 # overridable with --entry-url
goal: >
  Look up member {member_id} in the Member Servicing Console and read the current balance
  and account number of their Share Savings account.
params:
  member_id: { type: string, pattern: "^[0-9]{5}$", required: true, classification: pii_low,
               description: Five-digit member number, example: "10001" }
outputs:
  savings_balance:        { type: decimal, parse: currency_usd, classification: pii_low }
  savings_account_number: { type: string, pattern: "^[0-9]{10}$", classification: pii_high }
```

The model receives the goal with values substituted and the output names it must bind via `read(mark_id, output_name, intent)`; `done()` is refused with an `is_error` tool result until every declared output is bound. Params and outputs are copied verbatim into the emitted capability (E1).

### `capabilities/ledgerline.member.read_savings_balance@1.0.0.yaml` (reviewed copy; the untouched emitted draft sits in `evidence/capabilities/…draft.yaml`)

```yaml
# One file = one version of one capability. status gates unattended replay.
schema_version: 1                                   # format of THIS file; bumped only on breaking shape changes
capability:
  id: ledgerline.member.read_savings_balance        # stable, agent-facing name
  version: 1.0.0                                    # semver: major = params/outputs contract, minor = steps, patch = locator/wait tweak
  status: draft                                     # draft | approved | deprecated
  title: Read a member's current Share Savings balance
  description: >
    Looks up a member by member number and returns the current balance and account number of their
    Share Savings account. Returns MEMBER_NOT_FOUND, ACCESS_DENIED or NO_SAVINGS_ACCOUNT as business outcomes.
  risk_class: read                                  # max over steps at save time; a test rejects hand-edits that disagree
  app:
    profile: ledgerline-msc                         # -> apps/ledgerline-msc/profile.yaml
    version_range: ">=4.1 <5"                       # UI generation recorded against; mismatch at load forces attended mode
    surface: web_legacy
    entry_url: "{base_url}/console/home"            # base_url comes from the tenant, never from the artifact
  budget:
    max_active_seconds: 120                         # automation time only; the clock PAUSES while a human holds control; policy may cap lower
  provenance:                                       # pointers, not content
    discovered_by: claude-opus-5
    discovery_run_id: run_2026-09-10T09-03-11_a9f2
    goal_sha256: 1a7c…                              # sha256 of goals/read_savings_balance.yaml
    transcript_sha256: 5c9e…                        # sha256 of runs/<id>/transcript.redacted.json
    draft_path: evidence/capabilities/ledgerline.member.read_savings_balance@1.0.0.draft.yaml   # added at review; test compares flow
    recorded_at: 2026-09-10T09:09:40Z
    human_authored_steps: []                        # step ids a human performed during discovery, if any
  review:                                           # reviewer-owned; the emitter leaves these empty
    reviewed_by: null
    reviewed_at: null
    notes: null                                     # e.g. "authored 3 outcomes; moved label_anchor first at s2; tightened s4 wait"
    approved_by: null                               # set by `teller approve` (stretch); unattended replay requires it
    approved_at: null
    artifact_sha256: null                           # hash of everything above `review` at approval; any edit invalidates approval

params:                                             # copied from the goal spec (E1)
  member_id:
    type: string
    pattern: "^[0-9]{5}$"
    required: true
    classification: pii_low                         # none | pii_low | pii_high | secret — drives masking everywhere
    description: Five-digit member number
    example: "10001"                                # stored only for none/pii_low; synthetic
  # credentials are NOT params: the harness login routine in the app profile types them from env

outputs:                                            # returned in result.outputs on SUCCESS only
  savings_balance:
    type: decimal
    parse: currency_usd                             # "$2,431.17" -> 2431.17; parse failure => FAILURE/OUTPUT_PARSE_FAILED
    classification: pii_low
    source_step: s5                                 # bound by the read() call (E1)
  savings_account_number:
    type: string
    pattern: "^[0-9]{10}$"
    classification: pii_high                        # persisted as ****1982 everywhere; full value returned to the caller in memory only
    source_step: s6

business_outcomes:                                  # AUTHORED IN REVIEW: a happy-path transcript never sees these.
  MEMBER_NOT_FOUND:                                 # Evaluated in the post-act race of the steps listed in at_steps — "the state this step produced".
    description: No member exists with that member_id
    detect: { text_contains: "No members matched your search.", frame: main }
    at_steps: [s3]                                  # the same detector at any other step is FAILURE/UNDECLARED_CONDITION
    returns: { member_id: "{member_id}" }
  ACCESS_DENIED:
    description: The operator may not view this member (restricted branch)
    detect: { any_of: [ { http_status: 403 }, { text_contains: "You are not authorized" } ], frame: main }
    at_steps: [s4]
  NO_SAVINGS_ACCOUNT:                               # the member exists but holds no Share Savings account — a legitimate answer, not a defect
    description: The member has no Share Savings account
    detect: { all_of: [ { text_contains: "Accounts", frame: main }, { text_absent: "Share Savings", frame: main } ] }
    at_steps: [s5]                                  # evaluated when s5's target cannot be resolved (read step) — see Section 4
    returns: { member_id: "{member_id}" }

recoverable_conditions:                             # flow-specific remedies; vendor-generic ones (session expiry) come from the profile
  - id: compliance_notice
    detect: { text_contains: "Compliance Notice", frame: main }
    remedy: { action: click, target: { text_hint: "Acknowledge", tag_hint: input, frame: main,
              locators: [ { kind: role_name, role: button, name: "Acknowledge" }, { kind: text_exact, text: "Acknowledge" } ] } }
    max_times: 1
  - id: slow_load                                   # should-tier: if cut, the STEP_TIMEOUT path still reports a failed load
    detect: { frame_loading: true, frame: main }
    remedy: { action: wait_and_retry, backoff_ms: [2000, 4000, 8000] }   # extends the race deadline; never re-acts
    max_times: 3

restart_anchor: s1                                  # where session re-establishment restarts; allowed only if no non-idempotent step has run

steps:
  - id: s1
    action: click
    intent: Open the Members area from the left navigation       # the model's stated reason, kept for reviewers
    idempotent: true                                             # may be re-acted once after a timeout; false => re-observe only
    target:
      text_hint: "Members"
      tag_hint: a
      frame: nav
      locators:                                                  # ordered; replay records which index resolved
        - { kind: role_name, role: link, name: "Members", robustness: "accessible name; stable across branding", surfaces: [web, desktop] }
        - { kind: text_exact, text: "Members", tag: a, robustness: "same text, no role dependency", surfaces: [web, desktop] }
        - { kind: xpath_anchored, anchor_text: "Servicing", xpath: "following::a[1]", robustness: "structural; last resort", surfaces: [web] }
    wait_for: { state: text, text: "Member Search", frame: main, timeout_ms: 5000 }    # E3: heading of the after-state
    expect: { text_contains: "Member Search", frame: main }     # E4: postcondition = this step's checkpoint
    risk_class: read

  - id: s2
    action: type
    intent: Enter the member number
    idempotent: true
    target:
      text_hint: "Member #"
      tag_hint: input
      frame: main
      locators:
        - { kind: label_anchor, label: "Member #", relation: same_row_right, control: text_input,
            robustness: "sibling <td> label; the operator-visible label is what the vendor keeps stable", surfaces: [web, desktop] }
        - { kind: attr_stable, attr: name, value: txtF1, robustness: "cryptic but stable in this vendor build", surfaces: [web] }
    value: { param: member_id }                                  # a reference, never a literal (E5)
    clear_first: true
    expect: { value_equals: { param: member_id } }               # no wait_for: typing changes nothing observable beyond the field (E3)
    risk_class: read

  - id: s3
    action: click
    intent: Submit the search
    idempotent: true                                             # GET-form submit; RiskClassifier => read (E7)
    target: { text_hint: "Go", tag_hint: input, frame: main,
              locators: [ { kind: role_name, role: button, name: "Go", robustness: "alt text is the accessible name", surfaces: [web, desktop] },
                          { kind: attr_stable, attr: name, value: cmdGo, robustness: "stable control name", surfaces: [web] } ] }
    wait_for: { state: text, text: "Search Results", frame: main, timeout_ms: 5000 }   # races against MEMBER_NOT_FOUND (declared at this step)
    expect: { text_contains: "Search Results", frame: main }
    risk_class: read

  - id: s4
    action: click
    intent: Open the matching member's detail page
    idempotent: true
    target:
      text_hint: "{member_id}"                                   # dynamic; substituted from params at replay
      tag_hint: tr
      frame: main
      locators:
        - { kind: text_exact, text: "{member_id}", tag: tr, robustness: "row text contains the parameter; canonicalised", surfaces: [web, desktop] }
        - { kind: xpath_anchored, anchor_text: "Member #", xpath: "ancestor::table[1]//tr[td[1][normalize-space()='{member_id}']]", surfaces: [web] }
    wait_for: { state: text, text: "Member Detail", frame: main, timeout_ms: 8000 }   # races against ACCESS_DENIED (declared at this step)
    expect: { all_of: [ { text_contains: "Member Detail", frame: main }, { text_contains: "{member_id}", frame: main } ] }
    risk_class: read

  - id: s5
    action: read
    intent: Read the Share Savings balance from the Accounts table
    idempotent: true
    target:
      frame: main
      tag_hint: td
      locators:
        - { kind: table_cell, table_anchor: "Accounts", row_match: { column: "Product", equals: "Share Savings" }, column: "Current Balance",
            robustness: "header-name addressed; survives column reordering", surfaces: [web, desktop] }
        - { kind: xpath_anchored, anchor_text: "Share Savings", xpath: "ancestor::tr[1]/td[3]", robustness: "positional; flags drift", surfaces: [web] }
    extract: { output: savings_balance, parse: currency_usd }   # unresolvable target here => NO_SAVINGS_ACCOUNT detector is evaluated before TARGET_NOT_FOUND
    risk_class: read

  - id: s6
    action: read
    intent: Read the Share Savings account number
    idempotent: true
    target:
      frame: main
      tag_hint: td
      locators:
        - { kind: table_cell, table_anchor: "Accounts", row_match: { column: "Product", equals: "Share Savings" }, column: "Account #", surfaces: [web, desktop] }
    extract: { output: savings_account_number, parse: string }
    mask_in_evidence: true                                       # pii_high output: cell blacked out in screenshots, ****last4 in logs
    risk_class: read

checkpoint:                                         # final success condition, evaluated after the last step; SUCCESS requires it
  all_of:
    - { text_contains: "Member Detail", frame: main }
    - { text_contains: "{member_id}", frame: main }
    - { output_present: savings_balance }
    - { output_present: savings_account_number }

dialogs:
  policy: fail_on_unknown                           # a dialog matching no step-level `dialog` declaration is dismissed => FAILURE/UNEXPECTED_DIALOG

escalation_policy:
  on_hard_failure: pause                            # pause and wait for a human (attended) | fail => FAILURE immediately (--unattended)
  on_irreversible_step: require_confirmation        # confirm in the console, or caller pre-authorises `--confirm-step <id>@<version>`
  # claim/control deadlines and max_handoffs live in the policy file — one place for every clock
```

### `capabilities/ledgerline.member.open_subaccount@1.0.0.yaml` (hand-authored; excerpt)

```yaml
capability:
  id: ledgerline.member.open_subaccount
  version: 1.0.0
  status: draft
  risk_class: write_irreversible                    # opening an account creates a regulated record and consumes an account number
  app: { profile: ledgerline-msc, version_range: ">=4.1 <5", surface: web_legacy,
         entry_url: "{base_url}/console/members/{member_id}" }     # parameterised deep-link entry
  budget: { max_active_seconds: 120 }
  provenance:
    discovered_by: human                            # NOT emitted from a model run — authored to exercise the write path in replay
    discovery_run_id: null
    transcript_sha256: null
    recorded_at: 2026-09-12T…
    notes: The F2 artifact is the emitted one; this one exists so the confirmation gate, a declared dialog and the validation outcomes are exercised.
params:
  member_id:       { type: string, pattern: "^[0-9]{5}$", required: true, classification: pii_low }
  product:         { type: string, enum: [Share Savings, Club Savings, Money Market], required: true, classification: none }
  nickname:        { type: string, pattern: "^.{0,20}$", required: true, classification: none }
  initial_deposit: { type: decimal, required: true, classification: none }
outputs:
  new_account_number: { type: string, pattern: "^[0-9]{10}$", classification: pii_high, source_step: c6 }
  confirmation_id:    { type: string, pattern: "^CNF-[0-9]{8}-[0-9]{4}$", classification: none, source_step: c7 }
business_outcomes:
  VALIDATION_REJECTED: { detect: { text_matches: "Initial deposit exceeds the product limit|Nickname is required", frame: main }, at_steps: [c5] }
  ALREADY_EXISTS:      { detect: { text_contains: "Nickname already in use", frame: main }, at_steps: [c5] }
steps:
  # c1 click "Open Sub-Account" (read) -> wait text "New Sub-Account"; c2 select product; c3 type nickname; c4 type initial deposit
  - id: c5
    action: click
    intent: Submit the new sub-account
    idempotent: false                               # POST; never re-acted; re-auth after this step is REAUTH_UNSAFE
    risk_class: write_irreversible                  # gate pauses for confirmation unless --confirm-step c5@1.0.0
    target: { text_hint: "Open", tag_hint: input, frame: main,
              locators: [ { kind: role_name, role: button, name: "Open" }, { kind: attr_stable, attr: name, value: cmdOpen } ] }
    dialog: { expect_text: "Open this sub-account?", respond: accept }   # the app's own confirm(); declared, so it is handled, not escalated
    wait_for: { state: text, text: "Sub-Account Opened", frame: main, timeout_ms: 8000 }
    expect: { text_contains: "Confirmation", frame: main }
  # c6 read new account number (table_cell / label_anchor) -> output; c7 read confirmation id -> output
```

### `apps/ledgerline-msc/profile.yaml` (vendor layer, shared by every capability and tenant)

```yaml
schema_version: 1
profile: ledgerline-msc
product: Ledgerline Member Servicing Console
login:                                              # harness-only subflow; exempt from RiskClassifier; values are secret refs, never literals
  entry_url: "{base_url}/login"
  steps:
    - { id: l1, action: type,  target: { locators: [ { kind: label_anchor, label: "User ID",  relation: same_row_right, control: text_input } ] }, value: { secret: LEDGERLINE_USER } }
    - { id: l2, action: type,  target: { locators: [ { kind: label_anchor, label: "Password", relation: same_row_right, control: password } ] },   value: { secret: LEDGERLINE_PASS } }
    - { id: l3, action: click, target: { locators: [ { kind: role_name, role: button, name: "Sign In" } ] }, wait_for: { state: url, url_matches: "/console", timeout_ms: 8000 } }
  checkpoint: { url_matches: "/console" }
detectors:                                          # evaluated in every pre-sweep and every wait-race poll of every capability
  session_expired:          { url_matches: "/login\\?reason=expired" }
  login_wall:               { url_matches: "/login$" }
  app_error:                { any_of: [ { http_status_gte: 500 }, { text_matches: "Application Error|ORA-[0-9]{5}" } ] }
  access_denied:            { any_of: [ { http_status: 403 }, { text_contains: "You are not authorized" } ] }
  validation_error_generic: { css_exists: "font[color=red]" }   # a FAILURE signal unless a capability declares a specific-text outcome at that step
recoverable_conditions:
  - { id: session_expired, detect: { detector: session_expired },
      remedy: { action: run_subflow, subflow: login, then: restart_from_anchor }, max_times: 1 }   # refused if a non-idempotent step already ran
sensitive_selectors: [ "input[type=password]" ]     # blacked out in every screenshot and stripped from DOM snapshots
sensitive_columns:   [ "Account #", "SSN (last 4)" ] # header-anchored: cells under these headers are masked (classification-driven, not regex)
ui_fingerprint: { frame_names: [nav, main], title_pattern: "Ledgerline .* Servicing Console" }   # cheap entry-time drift check
```

### Tenant layer

`tenants/local.yaml` — the demo tenant; **replays every artifact unmodified**:

```yaml
tenant: local
base_url: http://127.0.0.1:8600
credentials: { LEDGERLINE_USER: env, LEDGERLINE_PASS: env }      # names only; resolved from the environment at run time
policy: policies/ledgerline.yaml
app_version: "4.2"
overrides: {}
```

`tenants/example-b.yaml` — a hypothetical second institution running the same vendor build with a relabelled nav item (the one override example; used by `test_artifact_roundtrip.py::test_override_merge_by_step_id`):

```yaml
tenant: example-b
base_url: http://127.0.0.1:8600
credentials: { LEDGERLINE_USER: env, LEDGERLINE_PASS: env }
policy: policies/ledgerline.yaml
app_version: "4.3"
overrides:
  ledgerline.member.read_savings_balance:
    steps:                                          # keyed by step id; merged into the matching list element
      s1:
        target:
          locators_prepend:                         # adds a tenant-specific locator AHEAD of the base list; base fallbacks survive
            - { kind: role_name, role: link, name: "Member Lookup", robustness: "tenant relabelled the nav item", surfaces: [web, desktop] }
    recoverable_conditions_add: []                  # tenant-specific interstitials would go here
```

**Merge semantics (`artifact/store.py`), defined once:** scalars and mappings follow JSON-merge-patch; **lists whose elements carry `id` are merged by id** (`steps`, `recoverable_conditions`, `locators` when given as a full replacement); `locators_prepend` / `locators_append` add to the base list; `recoverable_conditions_add` appends. An override naming an unknown step id is a load error. The merged artifact is written to `runs/<id>/artifact.merged.yaml` for audit.

**Versioning rules:** `schema_version` changes only with breaking shape changes and the loader refuses unknown versions; `capability.version` is semver with the policy in the comment; a re-record or hand edit writes a new file and keeps the old one; `review.artifact_sha256` pins approval to exact content, so any edit reverts `status` to `draft` (enforced on save).

### Emitter rules (`discovery/emit.py`) — the transcript → draft artifact contract, tested by `test_emit_rules.py` against a fixture run

| Rule | Input | Output |
|---|---|---|
| **E1 Contract** | goal spec | `params`, `outputs` copied verbatim; `outputs[*].source_step` = the step whose `read(mark, output_name)` bound it; `done()` before all outputs are bound is refused, so a draft always has a complete contract |
| **E2 Steps & targets** | each executed, policy-approved tool call (`ask_human`/`done` are not steps) | one step with `action`, `intent` (from the tool call), `target` = `bundle_from_description(describe(mark))` with the fixed order of Section 4 and the fixed robustness notes; unsupported kinds never emitted |
| **E3 wait_for** | before/after observations of the acted frame | if the frame's URL changed **or** its `text_digest` changed: `wait_for: {state: text, text: <heading>}` where `<heading>` is the first of: the frame's first `h1/h2/h3/b/font[size>=4]` text new in the after-state, the new `title`; heading text equal to a param value or a `pii_high` value is skipped. If nothing observable changed (typing, selecting): no `wait_for`. **`navigation` is never emitted.** |
| **E4 expect** | action kind + after-state | `type`/`select` → `value_equals`; `click`/`press` with a change → `text_contains: <heading>` (the wait's own target); `read` → none (extract is the check); `navigate` → `url_matches` |
| **E5 Canonicalisation** | typed literals, route segments, locator text | any value equal to a param value → `{param: name}` / `"{name}"`; a test fails if any `pii_high` literal or secret survives |
| **E6 Timeouts** | observed act→settle latency | `timeout_ms = max(5000, 2 × observed)` rounded up to 1000 |
| **E7 idempotent / risk_class** | `Element.submits`, `form_method`, RiskClassifier | `idempotent: false` iff the action submitted a POST form; `risk_class` as classified at act time |
| **E8 Checkpoint & header** | final observation, run metadata | `checkpoint.all_of` = the last step's `expect` predicates + `output_present` for every output; `provenance.*` filled; `review.*` null; `business_outcomes: {}` (authored in review); `status: draft` |

**Review rules (what a human may change between draft and reviewed copy)** — `test_review_preserves_flow.py` enforces the second row for every capability with a `draft_path`:
- **May:** author `business_outcomes` and `recoverable_conditions`; edit `description`, `robustness` notes, locator *order*, `wait_for` text/timeouts; tighten `expect`; fill `review.*`.
- **May not:** add or remove steps; change a step's `action`, `frame`, `text_hint` or `tag_hint`; change `params`/`outputs`. Those require a re-record. `teller evidence export` writes `REVIEW_DIFF.md` (draft → reviewed unified diff) next to the evidence so a reviewer sees exactly what human judgment added.

---

## 6. Replay engine

`teller/replay/executor.py` is a straight-line interpreter over `steps`. A test asserts the `teller.replay` package never imports `anthropic`, and `result.llm_invoked` is asserted `false` on every replay.

### Per-run

1. **Preflight (no browser):** validate params (`PARAM_INVALID`); merge tenant overrides and validate (`ArtifactStore.load`); `policy check` every step's action kind and URL pattern (`POLICY_VIOLATION` before launch); `app.version_range` vs the tenant's `app_version` (mismatch → warning + force attended); `--unattended` requires `status: approved` (stretch) and turns `on_hard_failure` into `fail`.
2. Launch Chromium (headed if attended; `--remote-debugging-port=9333`), fixed viewport/locale/timezone, `reduced_motion`, `accept_downloads=False`; register `page.on("dialog")` (parks the dialog) and `page.on("response")` (document statuses per frame); run the profile `login` subflow; check `ui_fingerprint`.
3. Start the **active-time clock** (`budget.max_active_seconds`, capped by policy). It stops in `AWAITING_HUMAN` and `HUMAN_IN_CONTROL` and restarts at `HANDBACK_VERIFYING`; `result.timing` reports `active_ms` and `paused_ms` separately. Exceeding it → `FAILURE/RUN_BUDGET_EXCEEDED`.

### Per-step evaluation order (the classification pipeline)

Sweep order inside any evaluation is fixed: **parked dialog → business outcomes declared at this step → recoverable conditions (artifact, then profile) → profile detectors → the wait target.** Outcomes come before the wait target so a page that shows both "Search Results" and "No members matched" classifies as the outcome.

1. **Pre-sweep** of the state the previous step left (or the entry state): parked dialog (none should be here — unknown → dismiss + `UNEXPECTED_DIALOG`) → recoverable conditions → profile detectors. Business outcomes are **not** evaluated here (they belong to the step that produced the state). A remedy with budget runs and re-sweeps; a detector with no disposition → `FAILURE/UNDECLARED_CONDITION(<detector id>)`.
2. **Resolve** the `LocatorBundle` (Section 4); record `index_used`; `LOCATOR_FALLBACK_USED` + `DRIFT_WARNING` if `> 0`. Unresolvable at a step that declares outcomes → evaluate those detectors; one fires → `BUSINESS_OUTCOME`; none → `TARGET_NOT_FOUND`.
3. **Gate:** `PolicyGate.check(action, resolved_target, current_url)` → `POLICY_VIOLATION` before acting. `RiskClassifier` (Section 8): `write_irreversible` → **pause for confirmation** (`CONFIRMATION_REQUIRED` intervention; `allowed_resolutions: confirm | decline | abort`) unless `--confirm-step <id>@<version>` matches; under `--unattended` with no pre-authorisation → `HALTED/CONFIRMATION_REQUIRED` without acting.
4. **Act** with Playwright actionability; capture `url_before` per frame; `write_*` steps take before/after screenshots.
5. **Wait-race:** poll every 250 ms until `timeout_ms`, applying the sweep order each poll, first hit wins:
   - parked dialog: matches the step's `dialog` declaration → respond as declared, continue racing; otherwise dismiss → `FAILURE/UNEXPECTED_DIALOG`;
   - a declared outcome → `BUSINESS_OUTCOME` (terminal; not-found and 403 classify on the first poll after the page lands);
   - a recoverable with budget → run its remedy (`click` → continue racing with a fresh deadline; `wait_and_retry` → extend the deadline by the next backoff; `run_subflow: login` → restart from `restart_anchor` if no non-idempotent step has run, else `REAUTH_UNSAFE`); budget exhausted → `RECOVERY_LIMIT_EXCEEDED`;
   - a profile detector with no disposition here → `FAILURE/UNDECLARED_CONDITION`;
   - the wait target (`text` present / `selector` visible / `url` changed from `url_before` and matching) → step 6;
   - **timeout:** evaluate `expect` on the current state; holds → step 6 (the action landed late). Otherwise, if `idempotent: true`, re-act **once** and race again; a second timeout → `FAILURE/STEP_TIMEOUT`. If `idempotent: false` → `FAILURE/STEP_TIMEOUT` immediately (never re-post).
6. **Expect:** false → `CHECKPOINT_FAILED` with `expected` (rendered predicate) and `observed` (url, title, first 200 chars of visible text, detector hits) + `<step>_fail.png` + `<step>_fail.html`.
7. **Extract** (`read` steps): `currency_usd` / `string` / `int` / `regex` → `OUTPUT_PARSE_FAILED` with the raw (redacted) text. A legitimately non-numeric display such as "N/A" would be declared by the reviewer as an outcome with `target_text_matches` at that step — the mechanism exists; the mock does not seed the case.

After the last step the **final `checkpoint`** must hold for `SUCCESS`; outputs are returned only on `SUCCESS`.

### Remedies and their bounds

| Remedy | Behaviour | Bound |
|---|---|---|
| `click` (known interstitial) | resolve + click the declared target, continue the race with a fresh deadline | `max_times` |
| `wait_and_retry` (`frame_loading`) | extend the race deadline by the next `backoff_ms`; never re-acts | `len(backoff_ms)` and `max_times` |
| `run_subflow: login, then: restart_from_anchor` | re-run the profile login, restart from `restart_anchor` **only if `run.non_idempotent_executed == false`**; else `FAILURE/REAUTH_UNSAFE` (escalatable) | `max_times: 1` → second expiry is `RECOVERY_LIMIT_EXCEEDED` |
| declared step `dialog` | accept/dismiss as declared; logged as `dialog.handled` (part of the step, not a recovery) | once per act |
| unknown dialog | **dismiss (cancel)**, record the text, `FAILURE/UNEXPECTED_DIALOG` | — |

Dismiss rather than accept for an unknown dialog because accepting an unknown `confirm()` may commit an action; cancelling is the conservative default and the human sees the resulting page state in the handoff.

### Error taxonomy (`teller/replay/result.py`)

```python
class Status(StrEnum):                     # the ONLY terminal statuses; each has an exit code
    SUCCESS = "success"                    # checkpoint held, all outputs extracted                       -> exit 0
    BUSINESS_OUTCOME = "business_outcome"  # the app said no to the DATA; caller must branch             -> exit 10
    FAILURE = "failure"                    # automation could not complete; debuggable                   -> exit 20
    HALTED = "halted"                      # a human decided not to proceed, or policy required a human who was not present -> exit 30
# "needs human" is a PHASE in runs/<id>/state.json while the process waits on the same page — never a Status.

class OutcomeCode(StrEnum):                # declared per artifact via business_outcomes[*].at_steps
    MEMBER_NOT_FOUND = auto()      # member 20002 -> "No members matched your search." at s3
    ACCESS_DENIED = auto()         # member 30003 -> 403 "You are not authorized" at s4
    NO_SAVINGS_ACCOUNT = auto()    # member 10004 -> Accounts table without a Share Savings row at s5
    VALIDATION_REJECTED = auto()   # F3 deposit 50000 -> "Initial deposit exceeds the product limit" at c5
    ALREADY_EXISTS = auto()        # F3 nickname "Vacation" on 10001 -> "Nickname already in use" at c5

class RecoveryCode(StrEnum):               # result.recoveries[]; never terminal on their own
    INTERSTITIAL_DISMISSED = auto()  # chaos interstitial_known -> Compliance Notice -> Acknowledge clicked
    SLOW_LOAD_WAITED = auto()        # chaos slow -> frame still loading at timeout -> deadline extended (should)
    SESSION_REESTABLISHED = auto()   # chaos expire_session before any non-idempotent step -> login subflow -> restart from s1
    LOCATOR_FALLBACK_USED = auto()   # role_name failed, text_exact resolved -> also DRIFT_WARNING

class HaltCode(StrEnum):                   # result.halt = {code, step_id, by, note}; the caller branches on policy, not on a bug
    CONFIRMATION_DECLINED = auto()   # operator declined a write_irreversible step in the console
    CONFIRMATION_REQUIRED = auto()   # --unattended reached a write_irreversible step with no matching --confirm-step; nothing was acted
    ABORTED_BY_HUMAN = auto()        # operator pressed Abort during any pause

class FailureCode(StrEnum):                # each carries step_id, expected, observed, screenshot, dom_snapshot, diagnostics
    TARGET_NOT_FOUND = auto()          # every locator exhausted and no declared outcome fired; diagnostics table attached
    TARGET_AMBIGUOUS = auto()          # >1 visible match the fingerprint cannot separate; never guess
    CHECKPOINT_FAILED = auto()         # chaos interstitial_unknown -> click succeeded, "Member Search" never appeared (hero handoff)
    UNDECLARED_CONDITION = auto()      # a detector fired at a step where the artifact does not declare it (e.g. access_denied at s3)
    UNEXPECTED_DIALOG = auto()         # chaos dialog_unknown -> confirm() text matched no declaration; dismissed
    APP_ERROR = auto()                 # chaos app_error -> 500 ORA-00600 page
    STEP_TIMEOUT = auto()              # chaos slow --times 4 -> deadline exhausted; non-idempotent steps time out without re-acting
    RECOVERY_LIMIT_EXCEEDED = auto()   # session expired twice in one run (max_times 1)
    REAUTH_UNSAFE = auto()             # session expired after a non-idempotent step executed; restart refused
    OUTPUT_PARSE_FAILED = auto()       # balance cell not parseable as currency_usd
    POLICY_VIOLATION = auto()          # step would leave the allowlist or use a denied action kind; stops BEFORE acting
    PARAM_INVALID = auto()             # member_id "abc" fails ^[0-9]{5}$ in preflight; no browser launched
    RUN_BUDGET_EXCEEDED = auto()       # active-time clock exhausted
    HANDBACK_STATE_MISMATCH = auto()   # no resume mode's predicate held after max_handoffs re-escalations
    SURFACE_CRASHED = auto()           # browser/page closed unexpectedly
    INTERNAL_ERROR = auto()            # engine exception; traceback path in evidence
```

**Unresolved escalations.** There is no `ESCALATION_TIMEOUT` code: when nobody claims within `claim_deadline_s`, or a claimant never resumes within `control_deadline_s`, the terminal result is **what `--unattended` would have produced** — `FAILURE/<original code>` for a hard-failure pause, `HALTED/CONFIRMATION_REQUIRED` for a confirmation pause — with `escalations[i].resolution: timed_out`. The debuggable cause is preserved and the caller sees that a human was asked.

The classification rule lives once in `replay/classify.py` and once in REPORT section 3: *the app said no to the data → `BUSINESS_OUTCOME`; a declared bounded remedy fixed it → `RECOVERY`; a human decided, or policy required one who was absent → `HALTED`; anything else that stops the run → `FAILURE`. Under `on_hard_failure: pause`, a `FAILURE` first becomes a pause on the same live page; whatever the human does, the run still ends in one of the four statuses.*

### Result contract (`result.json`, pydantic discriminated union on `status`)

```json
{
  "schema_version": 1,
  "run_id": "run_2026-09-11T09-41-02_c17d",
  "mode": "replay",
  "capability": { "id": "ledgerline.member.read_savings_balance", "version": "1.0.0", "status": "draft" },
  "tenant": "local",
  "params": { "member_id": "10001" },
  "status": "success",
  "outputs": { "savings_balance": "2431.17", "savings_account_number": "****1982" },
  "outcome": null,
  "failure": null,
  "halt": null,
  "recoveries": [ { "code": "INTERSTITIAL_DISMISSED", "condition_id": "compliance_notice", "step_id": "s1" } ],
  "warnings":   [ { "code": "DRIFT_WARNING", "step_id": "s2", "index_used": 1, "suggest_override": "runs/<id>/suggested_overrides.yaml" } ],
  "steps": [ { "step_id": "s1", "status": "ok", "index_used": 0, "duration_ms": 412, "screenshot": "screenshots/s1_after.png" } ],
  "escalations": [],
  "evidence_dir": "runs/run_2026-09-11T09-41-02_c17d/",
  "timing": { "started_at": "…", "finished_at": "…", "active_ms": 6120, "paused_ms": 0 },
  "llm_invoked": false,
  "policy_sha256": "9b1d…"
}
```

Exactly one of `outputs` / `outcome` / `failure` / `halt` is non-null (enforced by the union). `outcome` = `{ code, step_id, message, returns }`. `failure` = `{ code, step_id, expected, observed, screenshot, dom_snapshot, diagnostics, message }`. `halt` = `{ code, step_id, by, note }`. `escalations[]` = `{ intervention_id, trigger, step_id, requested_at, claimed_by, claimed_at, released_at, resolution ∈ {confirmed, declined, resumed:<mode>, aborted, timed_out}, note, action_count, human_actions_path }`. **`pii_high` values are masked in the file** (`****1982` above) and in `params`; full `outputs` are returned to the caller in memory and written only with `--emit-full-outputs`. CLI exit codes: `0` success, `10` business outcome, `20` failure, `30` halted — an agent harness branches without parsing.

---

## 7. Escalation and handoff

### Stuck detection

**Discovery (evaluated after every action, cheapest first):**

| # | Trigger | Rule |
|---|---|---|
| 1 | `MODEL_SELF_REPORT` | the model calls `ask_human(reason)`; the prompt tells it to when unsure, when facing data it should not read, or before anything that moves money |
| 2 | `NO_PROGRESS` | 3 consecutive actions with identical `(url, text_digest, observation_hash)`; prose-only turns (when `tool_choice=auto`) count |
| 3 | `OSCILLATION` | the `(action, mark text_hint, frame)` tuple repeats within a window of 6 (A-A or A-B-A-B) |
| 4 | `POLICY_BLOCKED_TWICE` | `PolicyGate` rejected two proposed actions in a row |
| 5 | `IRREVERSIBLE_STEP` | `RiskClassifier` says `write_irreversible` — blocked and routed for a human decision |
| 6 | `UNKNOWN_STATE` | a parked dialog matching no known text, a page with zero interactable marks, or a `refusal` stop reason |
| 7 | `BUDGET` | `max_steps` (20) or `max_active_seconds` (360) reached without `done()` |

A login page reappearing mid-run is not a stuck trigger: the harness re-runs the profile login once (the model never has a credential tool); a second occurrence is `UNKNOWN_STATE`.

**Replay:** any `FailureCode` under `escalation_policy.on_hard_failure: pause`; a `write_irreversible` step without a matching `--confirm-step`; `REAUTH_UNSAFE`. Business outcomes never page a human.

**P2 vs P4 behaviour, stated honestly.** In P2 (the real run), a discovery trigger writes `intervention.json`, prints the console URL and the CDP endpoint, and ends the run `FAILURE/<trigger>` with `escalations[0].resolution: unattended` — *detect and route*. Attended discovery (wait, human acts, resume with a synthetic user message "An operator intervened: <note>. Current screen attached.") is built in P4 on the cut line; if cut, REPORT 5 says the discovery-side case is detect-and-route only and the replay-side case is the full loop.

Each trigger produces an `InterventionRequest` `{intervention_id, run_id, mode, capability_id/goal, step_id, intent, trigger, reason, url, screenshot (redacted), last_5_events, allowed_resolutions, session_endpoint, claim_deadline, control_deadline}` written to `runs/<id>/intervention.json` and logged.

### Control-transfer state machine (`hitl/state.py`, persisted to `runs/<id>/state.json` on every transition)

```
                      stuck trigger                    request written, console notified
  RUNNING ───────────────────────────▶ STUCK_EVALUATING ──────────────────────────────▶ AWAITING_HUMAN
  controller=automation                controller=automation                             controller=none, clock PAUSED
  clock running                        (freeze screenshot, URL, DOM,                     (browser idle; no one acts)
       ▲                                pending action into the request)                      │            │
       │                                                                                      │ claim      │ operator POST claim {operator}
       │                                                                                      │ deadline   ▼
       │                                                                        FINISHED (what --unattended        HUMAN_IN_CONTROL
       │                                                                        would give; resolution=timed_out)  controller=human, clock PAUSED
       │                                                                                                           recorder.js injected; automation loops
       │                                                                                                           page.wait_for_timeout(250) reading
       │                                                                                                           commands.jsonl, screenshot every 2 s
       │   verification passes; clock RESUMES                                                                       │        │         │
       └────────────────────── HANDBACK_VERIFYING ◀──── resume {mode, note, intervention_id} ─────────────────────┘        │         │
         auto:        try skip_step, then retry_step, then complete — first predicate that holds wins            abort ──▶ FINISHED   │
         skip_step:   THIS step's `expect` holds                 → RUNNING at next step                          (HALTED/ABORTED_BY_HUMAN)
         retry_step:  the step's PRECONDITION holds              → RUNNING at same step (re-resolve, re-act;    decline (confirmation
                      (previous step's `expect`, or for the      refused for idempotent: false — use skip_step)  pause only) ──▶ FINISHED
                      first step the login checkpoint + ui_fingerprint)                                          (HALTED/CONFIRMATION_DECLINED)
         complete:    the final `checkpoint` holds               → FINISHED(SUCCESS; outputs extracted by automation)
         confirm (confirmation pause): controller=automation, the gated step acts                                confirm ──▶ RUNNING (act)
         nothing holds → STUCK_EVALUATING with trigger HANDBACK_STATE_MISMATCH (max_handoffs: 2, then FINISHED FAILURE/HANDBACK_STATE_MISMATCH)
```

`Surface.act()` requires a `ControlToken` and raises `ControlViolation` unless `controller == automation`; a unit test proves the driver refuses to act during `HUMAN_IN_CONTROL`. Every transition is an event with `actor ∈ {automation, operator:<name>, system}`, timestamp and reason; `state.json` `{run_id, phase, controller, step_id, intervention_id, active_ms, updated_at}` is the answer to "who is (or should be) in control" for anyone who cannot read the event log.

### How the live session is exposed — and who the "human" is

Attended runs launch Chromium **headed** with `--remote-debugging-port=9333`; the operator uses the very window, context, cookies and frames the automation was driving — nothing is relaunched. `session_endpoint()` (`http://127.0.0.1:9333`) is printed in the intervention request. **The same endpoint is how the scripted human works:** `test_handoff_cli.py` and `make handoff-demo` run `teller replay … --hitl cli` as a subprocess, wait for `intervention.json`, open a *second* Playwright client with `chromium.connect_over_cdp("http://127.0.0.1:9333")`, take `browser.contexts[0].pages[0]`, click "I attest" in frame `main`, then run `teller intervene resume <run_id> --mode auto`. Because the click happens in the page, `recorder.js` sees it and calls the automation's `expose_binding`, which dispatches while the automation thread sits in `page.wait_for_timeout(250)` — so the test asserts the click appears in `human_actions.jsonl` and the run ends `SUCCESS` with `escalations[0].resolution == "resumed:skip_step"`. A 15-minute spike in P1 confirms a CDP client can drive a Playwright-launched Chromium while a binding is registered. Production design (documented, not built): a worker exposes that CDP endpoint behind a noVNC/CDP-proxy console; the state machine and token are unchanged because they live above the Surface.

### Operator surface

`hitl/operator_server.py` — stdlib `ThreadingHTTPServer` on `127.0.0.1:8787`, started on first escalation in a daemon thread. It reads `intervention.json`, `state.json` and the latest screenshot; it **never imports Playwright** (tested). Endpoints: `GET /interventions/{id}` (**one static HTML page**, `<meta http-equiv=refresh content=2>`: capability/goal, step + intent, trigger + reason, screenshot, last 5 events, controller badge, CDP endpoint, note field, buttons), `GET /interventions/{id}.json`, `POST /interventions/{id}/{claim|resume|abort|confirm|decline|dialog_accept|dialog_dismiss}`. Each POST **appends one line to `runs/<id>/commands.jsonl`** `{intervention_id, command, mode, note, operator, ts}`; a POST whose `intervention_id` is not the pending one, or which arrives after resolution, gets **409** and is not written. The CLI twin `teller intervene claim|resume|abort|confirm|decline <run_id> [--mode auto|retry_step|skip_step|complete] [--note …] [--operator …]` appends the same line, so the whole flow is scriptable and testable headless (`--hitl cli`). The `dialog_*` commands exist regardless of the P1 spike result (they are two lines each); the spike decides only what REPORT says about clicking a parked dialog directly. Cut line: drop the HTML page, keep JSON + CLI.

### How resume is signalled

While `AWAITING_HUMAN` or `HUMAN_IN_CONTROL`, the automation thread runs `while True: page.wait_for_timeout(250); read new lines of commands.jsonl; check deadlines; every 2 s save a redacted screenshot`. Staying inside a Playwright call keeps `expose_binding` callbacks, `framenavigated`, `response` and `dialog` events flowing — the reason this design never blocks on a `threading.Event`. On `resume`, the thread detaches the recorder, takes a fresh observation, restarts the active clock and runs `HANDBACK_VERIFYING`.

### What is recorded

- `runs/<id>/intervention.json` (the request, then `claimed_by/claimed_at/released_at/resolution`) and `state.json` (live phase/controller).
- `runs/<id>/human_actions.jsonl` — from `context.add_init_script(recorder.js)` (every frame, survives navigation) + `context.expose_binding("__teller_hitl", sink)`: clicks (role, name, text_hint, frame, **LocatorBundle via `common.js describe()`**), input changes (value by classification: password → `<secret>`, `pii_high` → `****last4`, else clear), select changes, Enter/Escape/Tab, navigations, dialog outcomes; a throttled redacted screenshot (≤1/s) per event, plus `handoff_before.png` / `handoff_after.png`.
- `result.escalations[]` and all transitions in `events.jsonl` with `controller` stamped on every event; one `run_id` across the whole handoff.
- **Designed only:** `suggested_steps.yaml` (human actions promoted to artifact-shaped steps for a reviewer) — the bundles in `human_actions.jsonl` already carry everything it would need; REPORT 7 lists it.

---

## 8. Safety

### Allowlist and budgets (`policies/ledgerline.yaml`)

```yaml
schema_version: 1
origins: ["http://127.0.0.1:8600"]                 # any other origin is refused, including redirects
routes_allow: ["/login", "/console/**", "/error/**"]
routes_deny:  ["/__chaos**", "/console/admin/**"]  # deny wins; shows origin allowlisting alone is insufficient
actions_allow: [click, type, select, press, navigate, read, scroll, dismiss_dialog]
navigate_allow_new_origins: false
downloads: false
popups: close_and_log
budgets:                                           # every clock in one place
  discovery: { max_steps: 20, max_active_seconds: 360 }
  replay:    { max_active_seconds_cap: 300 }       # an artifact's budget.max_active_seconds may be lower, never higher
  handoff:   { claim_deadline_s: 900, control_deadline_s: 1800, max_handoffs: 2 }
risk:                                              # applies ONLY to submitting actions (Element.submits) and dialogs; GET-form submits and navigation are read
  reversible_patterns:                             # a record the operator can undo from the same console; no money movement, no regulated artifact
    controls: ["(?i)^(save|update|continue)$"]
    routes:   ["/console/members/*/contact"]      # not in the mock; exercised by the classifier unit test only
  irreversible_patterns:                           # money movement, account opening/closing, deletions, anything with a regulatory footprint
    controls: ["(?i)^(open|post|transfer|withdraw|reverse|close|delete|wire)"]
    routes:   ["/console/members/*/subaccounts/new", "/console/members/*/transactions/**"]
    dialog_text: ["(?i)open this|post|transfer|cannot be undone"]
  default_for_unmatched_post: write_irreversible   # an unknown POST submit is irreversible until a reviewer says otherwise
  explicit_elements: []                            # the ONLY place a class can be relaxed: [{control, route, class}]
redaction:
  regexes: { ssn: "\\b\\d{3}-\\d{2}-\\d{4}\\b", card: "\\b\\d{13,19}\\b" }   # backstop only, free-text fields only; classification does the real work
  known_secret_env: [LEDGERLINE_PASS, ANTHROPIC_API_KEY]
```

**Enforcement, three layers, all in code the model cannot bypass:** (1) `PolicyGate.check(action, resolved_target, current_url)` — one class, one call site inside `Surface.act`, for both discovery and replay; a denied action in discovery returns a `tool_result` with `is_error: true` and the reason (two in a row escalate), in replay it is `POLICY_VIOLATION` before acting. (2) `page.route("**/*")` aborts any document/navigation request whose origin or path is outside the allowlist, so a `javascript:` link or a redirect cannot leave the fence (P5). (3) A post-action URL check in `observe()`. `teller policy check <artifact>` statically verifies an artifact before any replay. Tests: navigation to `/__chaos`, a click on an external link and a `navigate` tool call to another host are all blocked with a recorded event; the policy file's sha256 is written into every result.

### Risk classes and policy

`RiskClassifier.classify(action, element, url, pending_dialog, step_declaration)`: `read` for `read/scroll/navigate/type/select/press` and for any click on a non-submitting element or a **GET**-form submit; for a **POST** submit or a click that raises a dialog, match `controls` (accessible name), `routes` (form action or current URL) and `dialog_text` against the irreversible patterns, then the reversible ones, else `default_for_unmatched_post`; then apply `explicit_elements` (policy may relax); then the step's declared `risk_class` (artifact may only tighten). The harness `login` subflow is exempt (flagged `harness: true`).

| Class | Examples | Discovery | Replay |
|---|---|---|---|
| `read` | navigate, click links/rows/tabs, type into fields, select, read, scroll, GET-form submits (the search "Go") | proceed | proceed |
| `write_reversible` | a "Save"/"Update" POST on a contact-details route — in the taxonomy and the classifier test; **no flow in the mock is one**, and REPORT says so | proceed; model must state `intent`; WARN + before/after screenshots | proceed; WARN + before/after screenshots; `idempotent: false` blocks re-acting |
| `write_irreversible` | F3 "Open" (account opening), F4 "Post", any control/route/dialog matching the patterns, any unmatched POST | **blocked** → `IRREVERSIBLE_STEP` intervention (human performs it in the live session or declines) | **pause** for confirmation unless `--confirm-step <id>@<version>`; `--unattended` without it → `HALTED/CONFIRMATION_REQUIRED` |

Justification in REPORT: flag-only performs the action; block-always makes the system useless for postings; a human decision, or a version-pinned pre-authorisation from an agent that already holds user consent, is the only handling that is both useful and defensible for a bank. Why account opening is irreversible: it creates a regulated record and consumes an account number; that a mock has a "Close" button changes nothing about a real core. Limits: classification is pattern-based and can miss an innocuously labelled destructive control, which is why unmatched POSTs default to irreversible, why `explicit_elements` exists and why `status: approved` is a human review.

### Redaction rules

| Artefact | Rule |
|---|---|
| Credentials | never enter the model context, the artifact or the log: resolved by a `CredentialProvider` from env and typed by the harness login subflow; no tool can read a password field; every known secret value (plain and URL-encoded) is replaced by `<secret>` in any outbound text |
| Artifacts | param references only (E5); a test fails if any `pii_high` literal or secret survives; `example` stored only for `none`/`pii_low` |
| Logs & transcript | `Redactor.scrub()` is the only writer path: **classification first** — every value bound to a `pii_high`/`secret` param, output, `sensitive_selectors` element or `sensitive_columns` cell is masked (`****last4` / `<secret>`) wherever it appears; **regex backstop** (`ssn`, `card`) only on free-text fields (visible-text digests, transcript text, human notes), never on engine-owned structured fields (run ids, timestamps, step ids, confirmation ids typed `none`); image blocks in the persisted transcript are replaced by file refs to the already-redacted PNGs; raw API payloads are never written |
| Screenshots | blacked out **in the DOM before capture** (`[data-teller-mask]` stamped by `marks.js` on password inputs, `sensitive_selectors`, cells under `sensitive_columns`, controls bound to `pii_high` params/outputs, regex-hit text nodes) plus Playwright `mask=` on top-level locators; the model and the disk receive the same redacted image (the model reads `pii_high` cells via `read()`, whose tool result is masked; the recorder captures the bundle, replay extracts the value) |
| DOM snapshots | input values stripped; masked nodes' text replaced |
| Result file | `params` and `outputs` masked by classification; full outputs only in memory unless `--emit-full-outputs` |
| Playwright trace | off by default; `--trace` writes `runs/<id>/trace.zip` labelled *unredacted, debug only*; `evidence export` refuses to copy it |
| Repo hygiene | `.env` gitignored; `tests/test_redaction_audit.py` greps `runs/` and `evidence/` for every fixture secret and `pii_high` literal and fails on any hit, **and asserts a confirmation id `CNF-20260912-0042` and a run id survive unscrubbed** (the false-positive story); run before every evidence commit |

Limits stated plainly in REPORT: regex PII detection is heuristic; masking depends on the DOM walker seeing the element; the LLM provider sees redacted screenshots of synthetic data, which in production means a data-processing agreement and zero-retention terms, not a code change.

---

## 9. Evidence and observability

### Every run writes `runs/<run_id>/`

| File | Content |
|---|---|
| `events.jsonl` | one pydantic `Event` per line: `ts, run_id, mode, controller, phase, step_id, type ∈ {run.start, observe, decide, policy_check, risk_check, act, wait, condition_detected, recovery, checkpoint, escalation, handoff.*, human_action, dialog, result, run.end}`, payload (for `decide`: the tool call, its `intent`, token usage — redacted) |
| `state.json` | live `{phase, controller, step_id, intervention_id, active_ms, updated_at}`; rewritten on every transition |
| `screenshots/` | discovery: every turn, badges retained so a reviewer sees what the model saw; replay: `sNN_after.png` per step, `sNN_fail.png` on failure; handoff: before/after + throttled |
| `transcript.redacted.json` | discovery only: the full messages array with images replaced by paths |
| `usage.json` | discovery only: tokens, cache reads, turns, wall time |
| `cassette.json` | discovery only (`--record-cassette`): `{meta: {model, recorded_at, template_hashes}, turns: [{index, observation_hash, response}]}` |
| `artifact.merged.yaml` | replay: the artifact after tenant overrides — what actually ran |
| `result.json` | Section 6 contract |
| `<step>_fail.png`, `<step>_fail.html` | the richer failure signal (redacted PNG, values-stripped DOM of every frame) |
| `intervention.json`, `commands.jsonl`, `human_actions.jsonl` | when an escalation occurred |
| `suggested_overrides.yaml` | when a `DRIFT_WARNING` fired (should) |

Console output mirrors `events.jsonl` at INFO with the same redaction so the demo reads live.

### `/evidence/` (curated with `teller evidence export <run_id> --to evidence/<name>`, which re-runs the Redactor and writes an `index.md` step table)

| Folder | Demonstrates | Must / should |
|---|---|---|
| `evidence/capabilities/…read_savings_balance@1.0.0.draft.yaml` + `REVIEW_DIFF.md` | the artifact **as emitted** by the run, and exactly what review changed (the reviewed copy is `/capabilities/…`) | must |
| `evidence/discovery-read_balance/` | **the genuine `claude-opus-5` run**: events, badged screenshots, redacted transcript, usage, cassette, result | must |
| `evidence/replay-success/` | member 10001 → `SUCCESS` + outputs (`pii_high` masked) | must |
| `evidence/replay-not_found/` | member 20002 → `BUSINESS_OUTCOME/MEMBER_NOT_FOUND` at s3, classified on the first poll | must |
| `evidence/replay-no_savings_account/` | member 10004 → `BUSINESS_OUTCOME/NO_SAVINGS_ACCOUNT` at s5 (an unresolvable target that is an answer, not a defect) | must |
| `evidence/replay-app_error/` | chaos `app_error` → `FAILURE/APP_ERROR` with `s4_fail.png` + `s4_fail.html` | must |
| `evidence/replay-recovered/` | chaos `interstitial_known` + `expire_session` → `SUCCESS` with `INTERSTITIAL_DISMISSED` and `SESSION_REESTABLISHED` (the re-auth safety argument, exercised) | must |
| `evidence/handoff-unknown_interstitial/` | chaos `interstitial_unknown` → pause → human clicks in the same Chromium → `resume --mode auto` → `skip_step` → `SUCCESS`, `escalations[1]`, `human_actions.jsonl` (the committed take is a real human in the headed window; `make handoff-demo` reproduces it headless with the CDP scripted human) | must |
| `evidence/replay-confirmation_required/` | F3 `--unattended` with no `--confirm-step` → `HALTED/CONFIRMATION_REQUIRED` before acting | must (no LLM, no human — cheap) |
| `evidence/README.md` | index of the above, what each proves, and the labelled provenance of both artifacts | must |
| `evidence/replay-access_denied/` | member 30003 → `BUSINESS_OUTCOME/ACCESS_DENIED` at s4 | should |
| `evidence/replay-open_subaccount-confirmed/` | F3 attended: confirmation pause → operator confirms → declared `confirm()` accepted → `SUCCESS` with `new_account_number` masked | should |
| `evidence/replay-slow_recovered/` | chaos `slow` → `SLOW_LOAD_WAITED` | should (falls with the `slow` cut) |
| `evidence/discovery-post_transaction_refused/` | F4 goal → model reaches the form → "Post" blocked → `IRREVERSIBLE_STEP` | stretch |
| `evidence/handoff.mp4` | 60–90 s recording of the handoff run | optional |

---

## 10. Heterogeneity and multi-tenant story

**Surface seam.** The recorded flow never mentions the DOM: steps reference `LocatorBundle`s and `Action`s; the replay engine only knows "resolve in order, require uniqueness, record which index resolved". `Surface` is the only place a technology is imported, and each locator kind declares the surfaces that can evaluate it, so an artifact **degrades** (skips unsupported kinds, lower confidence, `DRIFT_WARNING`) rather than breaks. Legacy web is the built case: framesets are frame paths, table layouts are `label_anchor` and `table_cell`, non-semantic markup is handled by treating anything with `onclick` or a pointer cursor as interactable. Owned limitation, stated in REPORT 4: perception on the built surface is a DOM walk; what makes the design portable is that nothing above `Surface` knows that.

**Desktop mapping (documented in `DesktopSurface`'s docstring):**

| Surface concern | Web (built) | Desktop (designed) |
|---|---|---|
| `observe()` elements | `marks.js` per frame | macOS AX (`pyobjc`/`atomacos`) or Windows UIA (`pywinauto`) tree walk → role, name, bbox, window/pane path; badges drawn on the screen grab |
| `role_name` | `get_by_role` | AX role + title / UIA ControlType + Name |
| `label_anchor` | sibling-td geometry (`resolve.js`) | UIA `LabeledBy` or nearest static text by geometry |
| `text_exact` | `get_by_text` | Name/Value match; OCR on a Citrix/RDP bitmap |
| `table_cell` | header-anchored (`resolve.js`) | UIA Grid/Table patterns |
| `attr_stable` / `xpath_anchored` | web only | replaced by `ax_path` (AutomationId / AX path) — designed-only today |
| `coords_verified` | designed-only | hit-test at point + OCR verify — designed-only |
| waits / detectors | url, text, selector, frame_loading, http_status | window title, control visible, OCR text, busy cursor |
| dialogs | `page.on("dialog")` | modal window detection |
| act | Playwright locator actions | UIA Invoke/SetValue or OS input events |
| live-session handoff | CDP endpoint of the same Chromium | VNC/RDP into the same desktop session; same `ControlToken` |

Discovery loop, artifact schema, replay interpreter, PolicyGate, Redactor and handoff state machine are unchanged.

**Multi-tenant reuse.** An artifact is recorded against `app.profile` + `version_range`, never a tenant. Three layers merge at load: **app profile** (vendor login routine, generic detectors, sensitive selectors/columns, `ui_fingerprint`) → **capability** (flow, params, outputs, declared outcomes) → **tenant** (`base_url`, credential refs, policy, `app_version`, sparse `overrides` merged by id: prepend a tenant-specific locator, add a tenant interstitial, change a wait). The base artifact is never edited per tenant, and the merged result is written into the run's evidence. Locator ordering favours accessible names and header-anchored cells because those survive branding, CSS and column reordering — the typical inter-tenant variation; canonicalisation of routes and typed values into `{param}` references makes the flow data-independent (implemented, because redaction already needs it). **Each layer has exactly one example and one test:** profile → `test_replay_against_mock` (login + detectors), capability → the F2 replays, tenant → `example-b` merge test.

**Drift detection and management.** Entry-time `ui_fingerprint` comparison → `DRIFT_SUSPECTED` warning; every replay records `index_used` per step; `index_used > 0` → `DRIFT_WARNING` + `suggested_overrides.yaml`; `TARGET_NOT_FOUND` carries per-locator diagnostics. Designed only: a per-`(tenant, capability, step)` health score aggregated from `events.jsonl` that flags a capability for review when the primary locator fails consistently for one tenant (tenant drift) or for all tenants (vendor upgrade → bounded re-discovery of that step); approval state per `(capability version, tenant)`; `version_range` mismatch forcing attended mode (built). What is built: `Surface` protocol + web implementation, override merge with one unit test and one example, canonicalisation, drift warnings. What is designed only: desktop surface, `coords_verified`/`ax_path`, health scoring, per-tenant approval, tenant-B demo.

---

## 11. Repo layout

```
/README.md                         setup, keys, demo path (goal + target → discover → replay → replay with error → handoff), offline modes
/REPORT.md                         the seven fixed headings from Section 6 of the brief, verbatim (see Section 12 for the page budget)
/evidence/                         curated runs + README.md index + capabilities/ (emitted draft + REVIEW_DIFF.md)
/schema/                           capability.schema.json, app_profile.schema.json, tenant.schema.json, result.schema.json (make schemas)
/policies/ledgerline.yaml          allowlist, action kinds, budgets, risk patterns, redaction regexes
/apps/ledgerline-msc/profile.yaml  vendor layer: login routine, detectors, recoverables, sensitive selectors/columns, ui_fingerprint
/tenants/local.yaml                base_url, credential env refs, app_version, overrides: {}
/tenants/example-b.yaml            the one override example (locators_prepend on s1)
/goals/read_savings_balance.yaml   declared contract for F2 (input to discovery)
/goals/open_subaccount.yaml        declared contract for F3 (documents the hand-authored artifact's params/outputs)
/capabilities/                     ledgerline.member.read_savings_balance@1.0.0.yaml (emitted, reviewed),
                                   ledgerline.member.open_subaccount@1.0.0.yaml (hand-authored, provenance.discovered_by: human)
/docs/cuts-log.md                  running log of every cut taken (date, what, why, what it would take) → REPORT section 7
/docs/spikes.md                    Day-1 spike results (DOM blackout vs mask=, parked dialog, CDP second client)
/mockapp/
  app.py                           FastAPI routes, session cookie, chaos hooks
  chaos.py                         one-shot fault registry (6 modes) + /__chaos endpoint
  seed.py                          synthetic members and accounts (10001–10004, 30003)
  templates/                       base_legacy.html, frameset.html, nav.html, login.html, home.html, notice.html, attest.html, search.html,
                                   results.html, detail.html, subaccount_new.html, confirmation.html, transaction_new.html, error.html
/scripts/smoke_api.py              first API call of P2: exact request shape, asserts a tool_use block
/src/teller/
  cli.py                           typer: discover, replay, chaos, intervene, policy, evidence, schema, approve
  surface/base.py                  Surface Protocol, Observation, Element, Locator union, LocatorBundle, Action, Resolution
  surface/web_playwright.py        the only Playwright import: observe/describe/resolve/act/dialogs/screenshot/recorder/session_endpoint
  surface/bundles.py               bundle_from_description(): description -> ordered LocatorBundle (shared by discovery recorder and human recorder)
  surface/dom/common.js            shared primitives + describe(el)
  surface/dom/marks.js             per-frame walker, in-DOM badges, mask stamping, deterministic numbering
  surface/dom/recorder.js          human-action capture during handoff (uses describe)
  surface/dom/resolve.js           label_anchor and table_cell resolvers (stamp data-teller-hit)
  surface/desktop_stub.py          DesktopSurface: NotImplementedError + mapping table docstring
  discovery/loop.py                observe -> decide -> act, budgets, stuck detectors, context trimming, cassette record/offline
  discovery/tools.py               strict tool schemas (click, type, select, press, navigate, read, scroll, dismiss_dialog, ask_human, done) — required `intent`
  discovery/prompts.py             system prompt, observation formatter
  discovery/recorder.py            captures description + before/after observations per executed action
  discovery/emit.py                rules E1–E8 -> draft artifact
  artifact/model.py                pydantic: Capability, Step, Target, Locator kinds, Outcome, Recoverable, Review, Provenance, Budget, AppProfile, Tenant, GoalSpec
  artifact/store.py                load(path, tenant) -> merge-by-id -> validate; save with version/hash rules
  replay/executor.py               LLM-free interpreter (Section 6 order, wait-race, active clock)
  replay/resolve.py                ordered resolution, exactly-one rule, fingerprint filter, outcome-on-unresolvable, diagnostics
  replay/detectors.py              text_contains/text_absent/text_matches/url_matches/http_status(_gte)/css_exists/value_equals/output_present/
                                   dialog_text_matches/frame_loading/target_text_matches/all_of/any_of
  replay/classify.py               the one-sentence rule in code
  replay/result.py                 Status/OutcomeCode/RecoveryCode/HaltCode/FailureCode + ReplayResult discriminated union + exit codes
  replay/parsers.py                currency_usd, string, int, regex
  policy/gate.py                   PolicyGate.check — single call site (inside Surface.act)
  policy/risk.py                   RiskClassifier (submitting-action rule, patterns, explicit_elements, artifact-only-tightens)
  policy/redact.py                 Redactor — the only writer path (classification first, regex backstop)
  policy/route_fence.py            page.route allowlist fence (P5)
  hitl/state.py                    ControlToken, phases, transitions, state.json persistence, InterventionRequest
  hitl/operator_server.py          stdlib ThreadingHTTPServer, files only, 409 on stale intervention_id
  hitl/operator.html               one static page, meta refresh
  hitl/commands.py                 commands.jsonl append/read shared by server and `teller intervene`
  evidence/log.py                  JSONL EventLog
  evidence/shots.py                redacted screenshot + DOM snapshot saver
  evidence/export.py               curated copy + index.md + REVIEW_DIFF.md
/tests/
  unit/    test_policy_gate.py test_risk.py (GET submit=read, POST unmatched=irreversible, artifact only tightens) test_redactor.py (incl. negative cases)
           test_marks.py (fixture frameset: numbering, labels, table ctx, submits/form_method) test_locators.py (six kinds on the fixture)
           test_detectors.py test_classify.py test_state_machine.py (controller guard raises; stale id rejected; clock pauses)
           test_artifact_roundtrip.py (example-b merge-by-id + locators_prepend; local identical; hash/approval reset)
           test_schema_export.py test_emit_rules.py (fixture run -> expected draft) test_review_preserves_flow.py
  integration/ test_replay_against_mock.py (parametrised: success, not_found, no_savings_account, access_denied, undeclared_condition,
               app_error, interstitial_known, expire_session, reauth_unsafe, target_not_found, confirmation_required, dialog_unknown)
               test_handoff_cli.py (subprocess replay + CDP scripted human + intervene resume) test_chaos_denied.py
               test_discovery_offline_cassette.py (P5 cut line) test_template_freeze.py
  guards/  test_no_llm_in_replay.py test_only_surface_imports_playwright.py test_operator_no_playwright.py test_redaction_audit.py
  fixtures/ frameset.html, members.json, recorded_run_f2.json, expected_read_balance.draft.yaml
/Makefile                          mock, test, schemas, smoke, discover, demo-replay, replay-not-found, replay-no-savings, replay-error,
                                   replay-recovered, replay-confirmation-required, handoff-demo (headless, scripted human), handoff-attended, evidence
/pyproject.toml  /.env.example  /.gitignore  /docker-compose.yml (mock app only, optional)
```

---

## 12. Build plan

Total **48 h** over five days of ~9.5 h; cut lines bring the floor to **~43 h**. Commit after every phase; after P2 the repo is always submittable. Cumulative hours in brackets.

**Day-1 checkpoint rule (the critic's point that overruns start in P0/P1, not P4):** if P0 is not finished by the end of Day 1, slide the F3/F4 templates to P3 and take the `slow` cut *immediately*; if P1 is not finished by hour 15, take the P4 discovery-resume cut and the P5 cassette-playback cut *before* starting P2. Cuts are logged in `docs/cuts-log.md` as they happen.

| Phase | Deliverable | Est. h | Retires | Cut line |
|---|---|---|---|---|
| **P0 — Scaffold, mock console, models, configs** [8] | `pyproject`, `Makefile`, `.env.example`, `.gitignore`, `README.md` stub, **`REPORT.md` with the seven heading strings verbatim and an HTML-comment page budget under each**, `docs/cuts-log.md` (1 h). `mockapp/`: 13 templates on one legacy base, routes, session cookie + idle expiry, seed data incl. `10004`, one-shot `/__chaos` with six modes, `teller chaos arm\|reset` (4 h). Pydantic models for capability/profile/tenant/goal/result/event, loader with `schema_version` check, `make schemas` → four JSON schemas (2 h). Hand-written `profile.yaml`, `tenants/local.yaml`, `tenants/example-b.yaml`, `policies/ledgerline.yaml`, both goal files, a hand-written sample artifact for tests (1 h). | 8 | Section 4 target app; 3.2 schema shape/versioning/review fields; 3.3 simulable runtime conditions; 3.1 goal + target as declared input | F3 (`subaccount_new`, `confirmation`) and F4 (`transaction_new`) templates slide to P3 — the F2 run does not need them; `slow` mode slides to P3. |
| **P1 — Surface, gate, log, spikes** [15] | **Three 15-min spikes first**, results in `docs/spikes.md`: DOM blackout + `mask=` on frame-scoped locators; parked `confirm()` clickability in a headed window; a second CDP client clicking in a Playwright-launched Chromium with an active `expose_binding` (0.75 h). `dom/common.js` + `marks.js` (accessible name, label geometry, table ctx, `submits`/`form_method`, badges, mask stamping, deterministic numbering) (2.5 h). `WebPlaywrightSurface`: launch config incl. CDP port, `observe()` (dialog-first), `describe()`, `act(token, …)` with `ControlToken`, `screenshot()` (blackout + mask), `dom_snapshot()`, dialogs, `session_endpoint()`; `bundles.py` (2 h). `PolicyGate` v1 inside `Surface.act`, `RiskClassifier` v1 (submitting-action rule + patterns), `EventLog`, `Redactor` v1 (known-value scrub, `pii_high` last4, password, sensitive columns) (1 h). Harness login from profile (0.5 h). Tests: gate, risk, redactor, marks on the fixture frameset (1 h). **No resolvers in P1** — the recorder only captures descriptions; resolution is P3. | 7 | 3.1 interaction mechanism that works without a clean DOM; 3.4 allowlist enforcement + risk classification; 3.5 structured log; the three design unknowns | Regex text-node masking to P5 (classification-driven masking ships now). |
| **P2 — Discovery loop, emitter, THE REAL RUN** [23] | `scripts/smoke_api.py` first (0.25 h). `loop.py`: manual Messages loop with the Decision-4 shape, `stop_reason` handling (`tool_use` / prose turn behind `TELLER_TOOL_CHOICE=auto` / `refusal` / `max_tokens`), context trimming, `cache_control`, stuck detectors 1–7 (escalation = detect-and-route: `intervention.json` written, run ends `FAILURE` with an `escalations[]` record), `--record-cassette` (2.5 h). `tools.py` (`read(mark, output_name)`, `done()` refused until outputs bound) + `prompts.py` (1 h). `recorder.py` + `emit.py` implementing E1–E8; `test_emit_rules.py` on `recorded_run_f2.json` (2 h). **Run F2 for real, 2–3 attempts budgeted**; review the draft (author the three outcomes, check strategies, `review.notes`, `reviewed_by`); `evidence export` → `evidence/discovery-read_balance/`, `evidence/capabilities/…draft.yaml`; commit; README demo path made real (2.25 h). | 8 | 3.1 goal-driven loop against a live surface; 3.2 artifact emitted from a run by stated rules; 3.5 discovery evidence; **Section 4 non-negotiable** | Accept the first clean run even if review reorders a strategy (recorded in `review.notes` and `REVIEW_DIFF.md`); if the model flails after three attempts: shrink the element table to interactables only, shorten the goal wording, try `output_config: {effort: "xhigh"}` — never change stacks. **Submittable after this phase.** |
| **P3 — Replay engine, taxonomy, result, error evidence** [32] | `executor.py` with the Section 6 order, wait-race and active clock (2.5 h). `resolve.py` + `resolve.js`: six kinds, exactly-one rule, fingerprint filter, outcome-on-unresolvable, diagnostics, `suggested_overrides.yaml` (2 h). `detectors.py` (full predicate vocabulary), remedies with bounds and the idempotency/re-auth rules, declared/unknown dialogs (1.5 h). `classify.py`, `result.py` (four statuses + `HaltCode`), exit codes, `<step>_fail.png/html`, `teller policy check`, `--confirm-step`/`--unattended` → `HALTED/CONFIRMATION_REQUIRED` (1 h). Hand-authored F3 artifact (+ slid templates if any) (0.5 h). Tests: `test_no_llm_in_replay`, `test_locators`, `test_detectors`, `test_classify`, `test_artifact_roundtrip`, parametrised `test_replay_against_mock` (1.5 h). Evidence: `replay-success`, `-not_found`, `-no_savings_account`, `-app_error`, `-recovered` (interstitial + session expiry), `-confirmation_required` (+ `-access_denied`). REPORT sections 2 and 3 drafted as excerpts. | 9 | 3.3 deterministic replay, four-way result contract, immediate outcome classification; 3.5 richer failure signal; 3.4 irreversible handling without a human present; Section 6 error-replay evidence | `slow` remedy (`STEP_TIMEOUT` path stays; `SLOW_LOAD_WAITED` declared, documented as not exercised); `suggested_overrides.yaml`. |
| **P4 — Handoff** [40] | `hitl/state.py`: phases, `ControlToken` enforcement, transitions, `state.json`, `InterventionRequest`, clock pause (1.5 h). `commands.py` + `teller intervene`; `operator_server.py` + one static page; 409 on stale id (1.5 h). Poll-inside-`wait_for_timeout` wait loop; `recorder.js` capture with redaction; screenshots every 2 s (1.5 h). `HANDBACK_VERIFYING` with `auto\|retry_step\|skip_step\|complete`; `escalations[]`; attended confirm/decline for `write_irreversible` (1 h). `test_handoff_cli.py` (subprocess + CDP scripted human + resume) and `test_state_machine.py` (1 h). Evidence: `handoff-unknown_interstitial/` (real human take, headed), `replay-open_subaccount-confirmed/`; REPORT sections 5 and 6 drafted (1.5 h). | 8 | 3.6 detect/route, take control of the live session, hand back with verification, record the human; 3.4 conservative handling of irreversible actions end to end | In order: attended discovery resume (document detect-and-route only); `replay-open_subaccount-confirmed` evidence (`confirmation_required` already covers the gate); optional video. **Never** cut the state machine, `ControlToken`, the recorder, handback verification or the CDP test. |
| **P5 — Hardening** [44] | `route_fence.py`, `test_chaos_denied`, `test_redaction_audit` (positive + negative), guard tests, `test_review_preserves_flow`, `test_template_freeze` (hashes from `cassette.json.meta`), regex text-node masking, ruff clean, type hints, fresh-clone `make mock && make demo-replay && make handoff-demo` with no key (2.5 h). `--offline` cassette playback keyed by `observation_hash` with the loud mismatch message + `test_discovery_offline_cassette` (1.5 h). | 4 | 3.4 redaction complete and tested; Section 7 code quality; offline story | Cassette playback (README then says: discovery needs a key; replay and handoff demos never do). |
| **P6 — REPORT, evidence curation, push** [48] | REPORT.md: **sections 4 and 7 first** (7 from `docs/cuts-log.md`, in the candidate's words), then 1–3, 5, 6 at the page budget below; excerpts only, full files linked by path (2.5 h). Final `evidence export` pass, `evidence/README.md` with labelled provenance, `REVIEW_DIFF.md`, redaction audit, README demo path re-run from a clean clone, push private → flip public, email per Section 11 (1.5 h). Stretch only if Day 5 starts ahead: `teller approve` + `--unattended` gate; failing that, the F4 discovery refusal run if ≥ 1.5 h remain. | 4 | 3.7 heterogeneity & multi-tenant write-up; Section 6 deliverables; Section 8 stretch (conditional) | Stretch cut first. Sections 4 and 7 exist before anything else in this phase so a partial phase still yields a complete write-up. |

**Day map (~9.5 h/day):** Day 1 — P0 + P1 spikes and `dom/` JS. Day 2 — rest of P1, P2 through the smoke test and first live attempt. Day 3 — remaining attempts, review, evidence commit (**the real run lands ~hour 21**), P3 first half. Day 4 — P3 rest, P4 first two-thirds. Day 5 — P4 rest, P5, P6.

**REPORT.md budget (~2.9 pages; the seven headings copied verbatim from the brief):**

| Heading (verbatim) | Budget | Contents (excerpts only; everything else is linked by path) |
|---|---|---|
| `1. Architecture` | 0.5 p | the ASCII diagram trimmed to 12 lines; five decisions with one-line trade-offs (manual loop, marks-in-DOM, one process + file channel, YAML + pydantic, goal spec as declared contract) |
| `2. Artifact schema` | 0.5 p | one step + the `business_outcomes` block + the versioning/review rules; why outcomes are authored in review; the three layers in one sentence with their one test each |
| `3. Determinism & error handling` | 0.5 p | the one-sentence rule; the sweep order; the taxonomy table condensed to one row per class; idempotent/re-auth rule; drift in two sentences |
| `4. Heterogeneity & multi-tenant` | 0.4 p | the seam; the desktop mapping table condensed to five rows; override example; what is built vs designed |
| `5. Escalation & handoff` | 0.4 p | triggers in one line each; the state machine compressed; CDP same-session; the three resume predicates; discovery-side status (attended or detect-and-route) |
| `6. Safety` | 0.3 p | three enforcement layers; risk classes with the account-opening justification; classification-first redaction and its limits |
| `7. Cuts` | 0.3 p | from `docs/cuts-log.md`: what was cut, when, why, what it would take; what to build next |

**When the deliverables get written:** `README.md` — stub in P0, demo path real in P2 (`--goal` + `--tenant` + `--entry-url` shown, with the one-line tenant→target mapping), replay/handoff commands in P3/P4, verified from a clean clone in P5 and again in P6. `REPORT.md` — headings in P0, sections 2–3 in P3, 5–6 in P4, 4 and 7 first thing in P6, then polish. `/evidence/` — discovery in P2, replays in P3, handoff in P4, final redaction pass and index in P6.

---

## 13. Stretch pick

**Confidence & approval, minimal form (Section 8, third bullet), ~2 h, in P6, only if Day 5 starts ahead of plan.** `teller approve <artifact> --by <name>` sets `review.approved_by/approved_at/artifact_sha256` and `status: approved`; any later edit resets to `draft` on save; `teller replay --unattended` refuses a `draft` artifact. It reuses fields the core already carries, makes the draft → review → approved workflow honest rather than implied, and completes the control-transfer story: "who may run this without a human present" is its natural end.

**Second choice if the first is out of reach but ≥ 1.5 h remain:** the F4 discovery refusal run (`evidence/discovery-post_transaction_refused/`) — a 3-tick genuine LLM run in which the model reaches the Post Transaction form and is blocked at "Post". It is a stretch because it carries the same flakiness as the hero run and the replay-side `HALTED/CONFIRMATION_REQUIRED` evidence already demonstrates the irreversible gate without a model.

Rejected: agent catalog (the result contract, exit codes and typed `params` already are the agent-facing interface; a tool-definition projection is "next" in REPORT 7); assisted LLM fallback (re-introduces a model into the path the rubric grades as model-free); tenant-B skin (the override seam, canonicalisation and drift warnings carry the design story; `example-b.yaml` + its test is the demonstration); multi-run stability (a loop, low signal on a deterministic mock); codegen (orthogonal).

---

## 14. Risks and mitigations

| Risk | Mitigation |
|---|---|
| The mandatory discovery run is flaky: wrong marks, loops, never calls `done()`, truncated turns, forced tool choice rejected. | Most capable Opus-tier model, `max_tokens: 16000`, strict tools, one action per turn, deterministic mark numbering, badges rendered by the browser, harness login so the model starts on the home screen, a declared contract so `done()` cannot be premature, `max_steps` 20, stuck detectors that turn a bad run into demonstrated escalation evidence; **`smoke_api.py` before the first real turn** with the `auto` + prose-turn branch kept; 2–3 attempts budgeted; every attempt's evidence kept. If it fails three times: fix the observation format and try `effort: xhigh`, not the stack. |
| Frameset handling breaks badges, masking or locator scoping. | Badges and blackout are injected inside each frame's own document — no offset math; every locator is frame-scoped; `test_marks.py`/`test_locators.py` run against a fixture frameset; the Day-1 spike checks blackout + `mask=` across frames before anything depends on them. |
| Human actions are silently not captured during handoff (sync Playwright dispatches events only inside Playwright calls). | The wait loop is `page.wait_for_timeout(250)` + file poll, never `threading.Event.wait()`; `test_handoff_cli.py` asserts a CDP-scripted click during `HUMAN_IN_CONTROL` appears in `human_actions.jsonl`. |
| A second CDP client cannot attach, or its clicks bypass the binding. | Day-1 spike; fallback is `page.evaluate`-driven clicks issued by the test process through a `teller intervene simulate-click` debug command (documented as test-only), and the committed handoff evidence is a real human in the headed window either way. |
| A parked JS dialog is not clickable in the headed window. | Hero demo uses a full-page unknown interstitial; the console offers `dialog_accept`/`dialog_dismiss` commands executed by the automation thread; spiked on Day 1 and documented either way. |
| Retry or re-auth double-posts a form. | `idempotent` per step (derived from `form_method` by E7); re-observe before any re-act; non-idempotent steps are never re-acted; re-auth restarts from the anchor only if no non-idempotent step executed, else `REAUTH_UNSAFE` → human; `test_replay_against_mock[reauth_unsafe]`. |
| Outcome text and wait text coexist on one page and misclassify. | Fixed sweep order (outcomes before the wait target) inside every poll; `test_replay_against_mock[not_found]` asserts `BUSINESS_OUTCOME` on a page whose header also says "Search Results". |
| Business-outcome detectors cannot come from a happy-path transcript. | Stated explicitly: vendor detectors live in the profile, flow outcomes are authored in review, `REVIEW_DIFF.md` shows exactly what review added, `test_review_preserves_flow` proves review did not touch the flow; REPORT says so. |
| Redaction is too aggressive (masks what the model must read; eats confirmation ids) or leaky. | Classification is explicit per param/output/column; the demo's read target is `pii_low`; `pii_high` cells are read through `read()` with a masked tool result; regexes are a backstop on free text only; `test_redaction_audit` has positive and negative cases; traces are never committed. |
| The cassette silently drifts after template edits. | Turns keyed by `observation_hash`; `test_template_freeze` pins the F2 templates' hashes after the real run; chaos pages are separate templates; a mismatch fails with a message naming the template and the fix (re-record or run live). |
| Estimates slip (handoff is the usual overrun, but P0/P1 is where it starts). | P0/P1 re-estimated to 8/7 h with no resolvers in P1; the Day-1 checkpoint rule takes P3/P4/P5 cuts pre-emptively; cut lines pre-decided and ordered: stretch → cassette playback → discovery-side resume → F3 confirmed evidence → `slow` → `suggested_overrides`; REPORT sections 4 and 7 are written before anything else on Day 5; `docs/cuts-log.md` makes section 7 a transcription, not a reconstruction. |
| Grader cannot run it (no key, no display). | `make mock && make demo-replay` and `make handoff-demo` need no key and run headless (the latter with the CDP scripted human); `--hitl cli` works headless; fresh-clone check in P5 and P6; `--offline --cassette` if it survives the cut. |
| Classification argued in the interview (permission denied, unknown dialog, declined confirmation, account opening). | The rule is one sentence, in code and in REPORT; every enum value has a reproducible mock trigger; `at_steps` scoping answers "why is 403 an outcome here and a failure there"; `HALTED` answers "why is a human saying no not a failure"; the regulated-record argument answers "why is opening an account irreversible". |
| Over-plumbed for one tenant (three config layers, four schemas). | Each layer has exactly one example and one test, listed in REPORT 2 in one sentence; nothing in the plumbing has a second consumer built "for later". |

---

## 15. Decisions the candidate must make personally

1. **LLM provider and key.** Default is Anthropic `claude-opus-5`; create the key, export `ANTHROPIC_API_KEY`, never commit it. The loop is Anthropic-specific (strict tools, image blocks, `cache_control`, fallbacks); if you switch providers, the seam is `discovery/loop.py`'s single `create()` call plus `tools.py`, and you must redo the request-shape defence. Decide whether to keep the refusal-fallback beta flag on (recommended on) and whether to escalate to `output_config.effort: xhigh` if a run flails (recommended as the *only* escalation; do not pass `effort` otherwise).
2. **Names you will say out loud:** CLI (`teller`), mock (`Ledgerline Credit Union`), capability id prefix, repo name; make them yours.
3. **Classification calls you must own in the interview:** permission denied as a declared business outcome at s4 and a failure at s3; a member with no savings account as an outcome, not `TARGET_NOT_FOUND`; unknown dialog dismissed (cancel) rather than accepted; a human's decline as `HALTED`, not `FAILURE`; account opening as `write_irreversible` (regulated record, consumed account number) and an unmatched POST defaulting to irreversible; `write_reversible` present in the taxonomy but absent from the mock's flows, and why that is honest.
4. **How hostile to make the mock** beyond the fixed list: everything in Section 3 is the floor; add nothing that costs more than an hour.
5. **Whether to attempt the F4 discovery refusal run and the optional screen recording** — both trade Day-5 hours and the first carries LLM flakiness; the replay-side `HALTED/CONFIRMATION_REQUIRED` evidence already covers the gate.
6. **Operator identity** shown in the console and in `escalations[].claimed_by` (a name or handle you are comfortable publishing).
7. **The reviewed artifact.** After the real run, you author the three `business_outcomes`, check every strategy and its robustness note, sign `review.reviewed_by`, and confirm `REVIEW_DIFF.md` shows only what the review rules allow. The draft stays in evidence untouched.
8. **The hand-authored F3 artifact's wording** — its `provenance.notes` explain why it exists; decide whether you are comfortable defending a hand-authored capability next to an emitted one (the plan's position: yes, because it is labelled and because it exercises code paths the hero cannot).
9. **Tenant `example-b` content** (which label a hypothetical second institution renamed) — the file exists to make the merge rule testable, nothing more.
10. **Cuts you actually take.** Log them in `docs/cuts-log.md` as they happen; REPORT section 7 is written on Day 5 from that log, in your words — not from this plan's predictions.
11. **GitHub:** public repo timing (push private during the build, flip to public after the redaction audit passes), and the email submission per Section 11 of the brief (repo URL on its own line, from the address you applied with, no zip).
