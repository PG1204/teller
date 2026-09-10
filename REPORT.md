# REPORT — Computer-Use Automation System

Through-line: **the model discovers; the artifact becomes a reusable capability; deterministic
replay is how an agent invokes it in production.** Everything below is built to keep the model out
of the production path and the human in reach of it.

## 1. Architecture

One Python process owns the browser (Playwright, sync API, main thread). The mock target app is a
second local process. The operator console is a stdlib HTTP server on a background thread that
touches only files. No queue, no database, no services: the seams are real, the plumbing is not.

```
 goal + params                         capability + params
      │                                        │
      ▼                                        ▼
 discovery/loop ──▶ Decider (gemini | anthropic | scripted)      replay/executor  (no LLM import — tested)
      │ observe ◀── Observation {badged+masked screenshot, element table, page facts}
      │ decide  ──▶ one tool call: click/type/select/read_value/… by MARK ID (+ required intent)
      │ act     ──▶ policy/gate.check ──▶ surface.act(ControlToken) ──▶ Playwright ──▶ Chromium ──▶ mockapp (frameset, tables, /__chaos)
      └ recorder ──▶ emit ──▶ capabilities/<id>@<ver>.yaml (status: draft)
                                                │ load: app profile ⊕ capability ⊕ tenant overrides
 hitl/state: controller ∈ {automation, human, none}; Surface.act refuses unless automation holds it
 hitl/handoff: pause inside page.wait_for_timeout, poll runs/<id>/commands.jsonl ◀── operator console / `teller intervene`
 evidence: events.jsonl (controller stamped on every line), screenshots/, result.json, <step>_fail.{jpg,html}
```

**Key decisions and trade-offs**

- *Perception is hybrid and frame-native.* `marks.js` runs in every frame, computes accessible
  names, sibling-cell labels and data-table context, and draws numbered badges **into each frame's
  own DOM**. The screenshot therefore shows badges exactly where Chromium rendered the controls, with
  no frame-offset arithmetic, and the same element table drives both the model and the recorder. The
  model never sees a selector or a coordinate; it chooses a badge, the surface owns targeting.
- *One gate, one writer.* `PolicyGate.check` has exactly one call site (inside `Surface.act`), so
  discovery and replay cannot diverge on what is allowed. `Redactor` is the only path to disk for
  telemetry. Both are enforced by tests, not convention.
- *The provider is a flag.* `Decider` keeps provider-native history (Gemini needs thought signatures
  echoed; Anthropic needs tool_use/tool_result pairing) and exposes a neutral transcript. Switching
  Gemini ⇄ Anthropic changes one module; artifact, replay and handoff are untouched.
- *Files as the handoff channel.* Sync Playwright dispatches page events only while the main thread is
  inside a Playwright call, so the automation waits by polling `commands.jsonl` inside
  `page.wait_for_timeout(250)` rather than blocking on a threading primitive. The web console and the
  CLI twin append to the same file; a queue or DB row would occupy the same seam later.
- *Rejected:* a hosted computer-use tool (coordinate-only actions give unreplayable targets), an agent
  framework (hides the policy seam the design is about), Selenium (weaker frame/dialog/route
  ergonomics), a real desktop surface (eats the budget for no rubric gain — designed instead, §4).

## 2. Artifact schema

A capability is a YAML file, validated by pydantic on load, with its JSON Schema exported to
`schema/capability.schema.json` (a test fails on drift). Three layers compose at load time:

```
apps/<profile>/profile.yaml   vendor layer: login routine (secret refs only), generic detectors,
                              known dialogs, sensitive selectors, ui_fingerprint
capabilities/<id>@<ver>.yaml  the flow: params, outputs, business_outcomes, recoverable_conditions,
                              steps (each: intent, idempotent, risk_class, target bundle, wait_for, expect)
tenants/<tenant>.yaml         base_url, credential env names, policy, app_version, sparse overrides
```

Excerpt (`capabilities/ledgerline.member.read_savings_balance@1.0.0.yaml`, emitted by the real Gemini run,
then reviewed — draft and reviewed copies side by side in `evidence/capabilities/`):

```yaml
params:   { member_id: { type: string, pattern: "^[0-9]{5}$", classification: pii_low } }
outputs:  { savings_balance: { type: decimal, parse: currency_usd, source_step: s5 },
            savings_account_number: { type: string, classification: pii_high, source_step: s6 } }
business_outcomes:                       # AUTHORED IN REVIEW — a happy-path run never sees these
  MEMBER_NOT_FOUND: { detect: { text_contains: "No members matched your search.", frame: main }, at_steps: [s3] }
  ACCESS_DENIED:    { detect: { any_of: [{ http_status: 403 }, { text_contains: "You are not authorized" }] }, at_steps: [s4] }
steps:
  - id: s2, action: type, intent: Enter the member number, idempotent: true, risk_class: read
    target: { frame: main, text_hint: "Member #", locators:
      - { kind: label_anchor, label: "Member #", relation: same_row_right, control: text_input,
          robustness: "legacy tables carry no <label for>; the adjacent-cell label is what vendors keep stable" }
      - { kind: attr_stable,  attr: name, value: txtF1, robustness: "cryptic but stable within this build" }
      - { kind: coords_verified, x: 171, y: 82, verify_text: "Member #", robustness: "recorded viewport only; never unattended" } ] }
    value: { param: member_id }          # a reference, never a literal
    expect: { value_equals: { param: member_id } }
checkpoint: { all: [ { text_contains: "Member Detail", frame: main }, { output_present: savings_balance }, … ] }
```

Why this shape: **targets are ordered bundles, not selectors** — replay tries strategies top to
bottom, records which index resolved, and never guesses between ambiguous matches; **values are
references** (`{param: …}`, `{secret: …}`) and the emitter canonicalises every literal equal to a
param value, so the artifact is data-independent and cannot carry PII; **classification is a
property of the artifact** (`at_steps`), so the same 403 is a legitimate answer at s4 and a failure
anywhere else; **every step carries `intent`, `idempotent` and `risk_class`** so a reviewer can read
why, a retry can know what is safe, and the gate can know what needs a human. Versioning: `schema_version`
for the file format, semver for the capability (major = params/outputs contract), `status:
draft → approved` pinned to a content hash — any edit reverts approval. Provenance points at the
redacted transcript by hash; the transcript is evidence, not part of the flow.

## 3. Determinism & error handling

**Determinism.** Fixed viewport/locale/timezone/reduced-motion; no `networkidle`, no sleeps, no
randomness. Every step declares `wait_for` (text / URL predicate with a deadline) and `expect` (its
postcondition); resolution applies the *exactly-one-visible* rule with a fingerprint filter for ties;
`index_used > 0` is reported as `LOCATOR_FALLBACK_USED` + `DRIFT_WARNING` with a
`suggested_overrides.yaml` for the reviewer. Retries are gated on `idempotent`: after a timeout the
engine re-observes, and re-acts only if the step declares it safe — a form is never double-posted.
Session re-establishment re-runs the vendor login routine and restarts from `restart_anchor` **only
if no non-idempotent step has executed**; otherwise `REAUTH_UNSAFE` goes to a human.

**Classification — one rule, in code once (`replay/classify.py`).**

| the app said no to the DATA (a detector the artifact declares *at this step*) | `BUSINESS_OUTCOME` — exit 10, caller branches |
|---|---|
| a declared, bounded remedy fixed it (`max_times`) | `RECOVERY` — reported, never terminal |
| a human stopped or refused, or policy needed a human who was not there | `DECLINED` — exit 30 |
| anything else that stops the run | `FAILURE` — exit 20, with step, expected, observed, screenshot, DOM |

Every observation is swept in this order: parked dialog → declared outcomes at this step →
recoverables (artifact, then vendor profile) → vendor detectors with no disposition here → nothing.
The sweep also runs on every 250 ms poll of the wait after an action (the *wait-race*), so a
"record not found" classifies within one poll instead of via a timeout. Failure codes are an enum
(`TARGET_NOT_FOUND` with per-locator diagnostics, `TARGET_AMBIGUOUS`, `CHECKPOINT_FAILED`,
`UNDECLARED_CONDITION`, `UNEXPECTED_DIALOG` (dismissed, never accepted), `APP_ERROR`, `STEP_TIMEOUT`,
`REAUTH_UNSAFE`, `OUTPUT_PARSE_FAILED`, `POLICY_VIOLATION`, `PARAM_INVALID`, `NOT_APPROVED`,
`ESCALATION_TIMEOUT`, …), each with a reproducible trigger in the mock (`teller chaos arm <mode>`).
Evidence: `evidence/replay-*` covers success, not-found, access-denied, an injected 500 (with
`s1_fail.jpg` + `s1_fail.html`), a known interstitial, a slow load and a session expiry.

**Drift (secondary).** An entry-time `ui_fingerprint` (frame names, title) → `DRIFT_SUSPECTED`;
per-step `index_used` → `DRIFT_WARNING`; tenant `app_version` outside the recorded
`version_range` → warning. All three are the signals a fleet would aggregate (§4).

## 4. Heterogeneity & multi-tenant

**Surface seam.** The recorded flow never mentions the DOM: steps reference locator bundles and a
closed action vocabulary; the interpreter only knows "resolve in order, require uniqueness, record the
index". `Surface` is a Protocol with one implementation (`WebPlaywrightSurface`, the only module that
imports Playwright — guard-tested). Each locator kind declares which surfaces can evaluate it, so an
artifact **degrades** on a new surface (unsupported kinds are skipped with a diagnostic) rather than
breaking. Legacy web is the built case: framesets are frame paths, table layouts are `label_anchor`
(same-row label cell) and `table_cell` (header-addressed), non-semantic markup is "anything with
`onclick` or a pointer cursor". A desktop surface maps directly: `observe()` from the macOS AX /
Windows UIA tree with badges drawn on the screen grab; `role_name` → AX role + title; `label_anchor` →
UIA `LabeledBy` or nearest static text by geometry; `table_cell` → UIA grid patterns;
`attr_stable`/`xpath_anchored` → an `ax_path` kind (already in the schema); `coords_verified` → hit-test
+ OCR; dialogs → modal-window detection. Discovery loop, schema, interpreter, gate, redactor and the
handoff machine are unchanged.

**Multi-tenant reuse.** An artifact is recorded against `app.profile` + `version_range`, never a
tenant. Three layers merge at load time; the base artifact is never edited per tenant. A tenant
patch is sparse and reviewable: mappings deep-merge, `steps` are keyed by id, lists replace, and
`target.locators_prepend` puts a tenant's relabelled control first while keeping the vendor defaults
as fallbacks (`tenants/example-b.yaml` does exactly this; the live test shows the fallback firing and
the `DRIFT_WARNING` + suggested override being produced). Locator order favours accessible names and
header-addressed cells because those survive branding, CSS and column reordering — the typical
inter-tenant variation. Canonicalisation makes the flow data-independent, so the same artifact serves
every member number and every institution. Designed, not built: a per-(tenant, capability, step)
health score from `index_used` in `events.jsonl` that flags tenant drift (one tenant's primary
locator failing) versus vendor drift (all tenants failing after an upgrade → bounded re-discovery
of that step), and approval state per (capability version, tenant).

## 5. Escalation & handoff

**Detecting "stuck".** Discovery: the model's own `ask_human`; three identical observations;
A-B-A-B oscillation; two policy blocks in a row; an irreversible step; an unknown dialog, a blank page
or a refusal; the step/time budget. Replay: any hard failure under `on_hard_failure: pause`, an
irreversible step without a matching `--confirm-step`, `REAUTH_UNSAFE`. Business outcomes never page
a human.

**Taking control of the live session.** The state machine (`hitl/state.py`) is
`RUNNING → STUCK_EVALUATING → AWAITING_HUMAN → HUMAN_IN_CONTROL → HANDBACK_VERIFYING → RUNNING`, with
`controller ∈ {automation, human, none}` derived from the state. `Surface.act` demands a
`ControlToken` and raises `ControlViolation` unless automation holds control — enforced in code and
unit-tested. An `InterventionRequest` (capability/goal, step, intent, trigger, reason, URL, redacted
screenshot, last five events, CDP endpoint, deadline) is written to `runs/<id>/intervention.json`.
The operator uses **the very Chromium window the automation was driving** (headed runs) — nothing is
relaunched; the console shows a live screenshot and *Claim*. On claim, `recorder.js` is injected into
every frame and the human's clicks, typed values (password fields never send a value; the rest is
scrubbed), key presses and navigations stream through a Playwright binding into `human_actions.jsonl`,
logged with `controller: human`. The automation meanwhile does nothing but poll.

**Handing back.** *Resume* carries the pending `intervention_id` (a stale id gets 409 / a CLI error)
and a mode: `retry_step` (run the same step again), `skip_step` (the human did it — the step's
`expect` is verified before moving on), `complete` (the human finished — only the final checkpoint
remains). `confirm` / `decline` answer an irreversible step; `abort` ends the run as `declined`.
Verification failing sends the run back to `STUCK_EVALUATING`, bounded by `max_handoffs`. Every
transition is an event with actor and reason; the result carries `handoffs[]` with who, when, mode,
note and the number of recorded human actions. The live test (`tests/integration/test_handoff.py`)
plays the operator as a *second CDP client on the same browser*: unknown interstitial → pause →
claim → the human clicks "I attest" → `retry_step` → `success` with `handoffs[1]` and the click in
`human_actions.jsonl`. Nobody claiming within `handoff_timeout_s` ends as `ESCALATION_TIMEOUT`.

Mocked deliberately: the operator UI is one static page over the file channel; production would
front the same page with a CDP/noVNC view of the session. The state machine, the token and the
channel would not change.

## 6. Safety

**Allowlist** (`policies/ledgerline.yaml`): permitted origins, route globs (deny wins — `/__chaos**`
is denied so origin allowlisting alone is shown to be insufficient), permitted action kinds,
`max_steps`, `max_run_seconds`. Enforced before every action by the single gate, for `navigate`
destinations and link `href`s alike, and re-checked on the current URL so a page that drifted off
the allowlist stops the run. `teller policy check <artifact>` verifies an artifact statically before
any browser launches; the policy's sha256 is stamped into every result.

**Risk classes.** `read` → proceed. `reversible_write` (a submit with a review screen or an undo
path) → proceed with before/after screenshots and `idempotent: false`. `irreversible_write` (money
movement, closures, deletions: control-text and *submitting-route* patterns, dialog text, plus
tenant-listed explicit controls) → **never performed by automation without a human decision**:
discovery blocks and escalates; replay pauses for confirmation unless the caller pre-authorised that
exact `step@version`; `--unattended` ends as `declined`. Why not flag-only or block-always: flagging
performs the action anyway; blocking makes the system useless for back-office postings; a human
decision, or a version-pinned pre-authorisation from an agent that already holds consent, is the only
option that is both useful and defensible for a bank. Route patterns apply only to *submitting*
clicks, so the model can reach a form and be refused at the button.

**Redaction.** Credentials are resolved from the environment by the harness login routine; the
model has no credential tool and never sees a login screen. Screenshots are masked **at capture**
(password fields, profile `sensitive_selectors`, and text nodes matching the policy regexes wrapped
by `marks.js`) so the model and the disk receive the same redacted image; `pii_high` values are read
through `read_value` and appear as `****last4` in every log and result (full outputs only in memory
unless `--emit-full-outputs`); artifacts hold `{param}` references only; DOM snapshots strip input
values; the redaction audit test greps everything committed for fixture secrets and account numbers.
Playwright traces are never written by default and the exporter refuses to copy them.

**Limits, stated plainly.** Risk classification is pattern-based and can miss an innocuously labelled
destructive control (hence `explicit_elements` and human approval). Regex PII detection is heuristic;
masking depends on the DOM walker seeing the node. The model provider receives redacted screenshots
of synthetic data; in production that is a data-processing agreement and zero-retention terms, not a
code change. The network-level route fence (`page.route`) is designed, not built — the gate and the
post-action URL check do the work today.

## 7. Cuts

**What was cut, and why.**

- *Model provider for the run.* The discovery run was done on Gemini's free tier (`gemini-3.6-flash`)
  rather than Claude. The model was never the problem — in all three attempts it chose the right
  control every turn and typed the parameter placeholder — the free tier's quota was: 5 requests per
  minute and 20 per day per model. The loop now paces itself to the quota and honours the server's
  retry delay; the two earlier attempts on `gemini-3.8-flash` completed all six actions and were cut
  off one call before `done`. Switching to Anthropic is a flag (`--provider anthropic`); nothing else
  changes.
- *Only one flow discovered.* The brief's "open a sub-account" flow (a `reversible_write` with a
  native `confirm()`) and the refuse-only "post transaction" flow (`irreversible_write`) are exercised
  by the mock and the policy tests but were not discovered with the model, to spend the daily quota
  on a clean hero run. The risk-class path is proven by unit tests and by the replay engine's
  `ConfirmationRequired` handling, not by evidence of a model being refused.
- *Network-level route fence* (`page.route` aborting off-allowlist requests) is designed, not built;
  the gate plus the post-action URL check cover the allowlist today.
- *Operator console* is one static page over the file channel; no CDP/noVNC embedding.
- *Desktop surface* is a typed mapping in §4, not code. *Tenant health scoring* is designed only.
- *`coords_verified`* is emitted as a last-resort strategy but the resolver treats it as untrusted;
  it never resolved in any run.

**What broke during the evidence pass and what it taught.** The first replay of the discovered
artifact fell back on step 2: Playwright's accessibility engine does not consider a sibling-cell label
an accessible name, so a `role_name` strategy for a legacy input can never resolve. The recorder now
skips `role_name` for geometrically labelled controls; the reviewed artifact records the removal in
`review.notes`. The recovered and handoff runs then surfaced a second rule that belongs in the
engine: after a remedy or a handback, if a step's postcondition already holds, the step is complete
— re-acting would look for a control that is legitimately gone. Both fixes are covered by the live
integration suite.

**Next, in order.** (1) The irreversible flow discovered with the model and its refusal recorded as
evidence. (2) `page.route` fence. (3) A second tenant skin of the mock to demonstrate
`locators_prepend` overrides end to end with a real relabel. (4) Health scoring from `index_used`
across runs. (5) The agent-facing catalogue: project each approved capability's params/outputs into
a tool definition — the result contract and exit codes already are the interface.
