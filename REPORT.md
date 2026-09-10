# REPORT — Computer-Use Automation System

Through-line: **the model discovers; the artifact becomes a reusable capability; deterministic
replay is how an agent invokes it in production.** The model is kept out of the production path and
the human is kept within reach of it.

## 1. Architecture

One Python process owns the browser (Playwright, sync API). The mock target app is a second local
process. The operator console is a stdlib HTTP server on a background thread that touches only files.
No queue, no database: the seams are real, the plumbing is not.

```
 goal + params                            capability + params
      │                                           │
      ▼                                           ▼
 discovery/loop ─▶ Decider (gemini|anthropic|scripted)      replay/executor  (imports no LLM SDK — guard-tested)
      │ observe ◀─ Observation {badged+masked screenshot, element table, page facts}
      │ decide  ─▶ ONE tool call by MARK ID, with a required `intent`
      │ act     ─▶ policy/gate.check ─▶ surface.act(ControlToken) ─▶ Playwright ─▶ Chromium ─▶ mockapp
      └ recorder ─▶ emit ─▶ capabilities/<id>@<ver>.yaml (draft)      load: capability ⊕ tenant overrides; vendor profile alongside
 hitl/state: controller ∈ {automation, human, none}; Surface.act refuses unless automation holds it
 hitl/handoff: wait inside page.wait_for_timeout, poll runs/<id>/commands.jsonl ◀─ operator console | `teller intervene`
 evidence: events.jsonl (controller on every line), screenshots/, result.json, <step>_fail.{jpg,html}
```

- **Perception is frame-native.** `marks.js` runs in every frame, computes accessible names,
  sibling-cell labels and data-table context, and draws numbered badges into each frame's own DOM, so
  badges land where Chromium rendered the controls with no offset arithmetic. The model chooses a badge;
  the surface owns targeting. Rejected: selectors (need a clean DOM), bare coordinates (unreplayable
  targets), a hosted computer-use tool (hides the policy seam).
- **One gate.** `PolicyGate.check` is called only from the surface (inside `act`, plus the entry URL),
  so discovery and replay cannot diverge on what is allowed. Import boundaries (no LLM SDK in replay,
  Playwright only in the surface package) are guard-tested.
- **The provider is a flag.** `Decider` keeps provider-native history (Gemini echoes thought
  signatures; Anthropic pairs tool_use/tool_result) behind one interface; artifact, replay and handoff
  never learn which model ran.
- **Files as the handoff channel.** Sync Playwright dispatches page events only inside a Playwright
  call, so the automation polls `commands.jsonl` from within `page.wait_for_timeout` rather than
  blocking on a thread primitive. Console and CLI append to the same file.

## 2. Artifact schema

A capability is YAML validated by pydantic on load; its JSON Schema is exported to `schema/` and
drift-tested. Three files compose one runnable capability: the **vendor profile** (login routine with
secret refs, generic detectors, sensitive selectors, UI fingerprint), the **capability** (the flow) and
the **tenant** (base URL, credential env names, policy, sparse overrides). The capability never names
a URL, a credential or a tenant. Excerpt of the real artifact (emitted by the Gemini run, then
reviewed; both copies in `evidence/capabilities/`):

```yaml
params:   { member_id: { type: string, pattern: "^[0-9]{5}$", classification: pii_low } }
outputs:  { savings_account_number: { type: string, pattern: "^[0-9]{10}$", classification: pii_high, source_step: s5 },
            savings_balance: { type: decimal, parse: currency_usd, source_step: s6 } }
business_outcomes:                       # authored in review — a happy-path run never sees these
  MEMBER_NOT_FOUND: { detect: { text_contains: "No members matched your search.", frame: main }, at_steps: [s3] }
  ACCESS_DENIED:    { detect: { any_of: [{ http_status: 403 }, { text_contains: "You are not authorized" }] }, at_steps: [s4] }
steps:
  - id: s2, action: type, intent: "Enter member number {member_id} into Member # field.", idempotent: true, risk_class: read
    target: { frame: main, text_hint: "Member #", locators:
      - { kind: label_anchor, label: "Member #", relation: same_row_right, control: text_input,
          robustness: "legacy tables carry no <label for>; the adjacent-cell label is what vendors keep stable" }
      - { kind: attr_stable, attr: name, value: txtF1, robustness: "cryptic but stable within this vendor build" }
      - { kind: coords_verified, x: 171, y: 82, verify_text: "Member #", robustness: "recorded viewport only; never unattended" } ] }
    value: { param: member_id }          # a reference, never a literal
    expect: { value_equals: { param: member_id } }
checkpoint: { all: [ { text_contains: "Member Detail", frame: main }, { output_present: savings_account_number }, { output_present: savings_balance } ] }
```

**Targets are ordered bundles**, each strategy with a robustness note; replay tries them top to
bottom, records which index resolved, and never guesses between ambiguous matches. **Values are
references**: the emitter canonicalises every literal equal to a parameter value, including inside
locators and the model's intent (the model typed `10001`; the file says `{member_id}`), so the artifact
is data-independent. **Classification is a property of the artifact**: the same 403 is a declared
outcome at s4 and a failure anywhere else. **Every step carries `intent`, `idempotent`, `risk_class`**
so a reviewer reads why, a retry knows what is safe, the gate knows what needs a human. Versioning:
`schema_version` for the format, semver for the capability, `status: draft → approved` pinned to a
content hash so any edit reverts approval; provenance points at the redacted transcript by hash only.

## 3. Determinism & error handling

**Determinism.** Fixed viewport, locale, timezone, reduced motion; no `networkidle`. Every step
declares `wait_for` (a text or URL predicate with a deadline) and `expect` (its postcondition).
Resolution requires exactly one visible match, with a fingerprint filter for ties; a fallback index is
reported as `LOCATOR_FALLBACK_USED` plus `DRIFT_WARNING` with a `suggested_overrides.yaml`. After a
timeout the engine re-observes and re-acts only if the step is `idempotent`, so a form is never
double-posted. Session re-establishment restarts from `restart_anchor` only if no non-idempotent step
has run; otherwise `REAUTH_UNSAFE` goes to a human. If a postcondition already holds after a remedy or
a handback, the step is complete.

**Classification, one rule, in code once (`replay/classify.py`):**

| The app said no to the DATA (a detector the artifact declares *at this step*) | `business_outcome`, exit 10 |
|---|---|
| A declared, bounded remedy (`max_times`) fixed it | recovery: reported, never terminal |
| A human stopped or refused, or policy needed a human who was not there | `declined`, exit 30 |
| Anything else that stops the run | `failure`, exit 20: step, expected, observed, screenshot, DOM |

Every observation is swept in this order: parked dialog → outcomes declared at this step →
recoverables (artifact, then vendor profile) → vendor detectors with no disposition → nothing. The
sweep also runs on every 250 ms poll after an action, so "record not found" classifies within one poll.
Unknown dialogs are dismissed, never accepted. Runtime failure codes (`APP_ERROR`, `CHECKPOINT_FAILED`,
`STEP_TIMEOUT`, `UNEXPECTED_DIALOG`, `UNDECLARED_CONDITION`, session expiry) each have a fault the mock
injects on demand; preflight codes (`PARAM_INVALID`, `NOT_APPROVED`, `POLICY_VIOLATION`) come from
inputs. Evidence: `evidence/replay-*` covers success, three business outcomes, an injected 500 with
`s1_fail.jpg` and `s1_fail.html`, two recoveries in one run, a session expiry and an approved
unattended run. Drift, secondarily: an entry-time UI fingerprint, per-step `index_used` and a tenant
`app_version` outside the recorded range each raise a warning.

## 4. Heterogeneity & multi-tenant

**Surface seam.** The recorded flow never mentions the DOM: steps reference locator bundles and a
closed action vocabulary; the interpreter knows only "resolve in order, require uniqueness, record the
index". `Surface` is a Protocol; `WebPlaywrightSurface` is the only module importing Playwright. Each
locator kind declares the surfaces that can evaluate it, so an artifact degrades on a new surface
rather than breaking. Legacy web is the built case: framesets are frame paths, table layouts are
`label_anchor` and `table_cell`, non-semantic markup is anything with `onclick` or a `tabindex`. A
desktop surface maps kind for kind: `observe()` from the macOS AX or Windows UIA tree with badges on
the screen grab; `role_name` → AX role + title; `label_anchor` → UIA `LabeledBy` or nearest static
text by geometry; `table_cell` → grid patterns; `attr_stable`/`xpath_anchored` → the `ax_path` kind
already in the schema; dialogs → modal windows. Loop, schema, interpreter, gate, redactor and handoff
are unchanged.

**Multi-tenant reuse.** An artifact is recorded against a vendor profile and a version range, never a
tenant. Tenant patches are sparse and reviewable: mappings deep-merge, steps are keyed by id, lists
replace, and `target.locators_prepend` puts a tenant's relabelled control first with vendor defaults
as fallbacks (`tenants/example-b.yaml`; the live test shows the fallback firing and the drift warning).
Locator order favours accessible names and header-addressed cells because those survive branding and
column reordering, the typical inter-tenant variation. Designed, not built: a per-(tenant, capability,
step) health score from `index_used` across runs, separating tenant drift (one tenant fails) from vendor
drift (all fail after an upgrade → bounded re-discovery of that step); approval per (version, tenant).

## 5. Escalation & handoff

**Stuck.** Discovery: the model's own `ask_human`; three identical observations; A-B-A-B oscillation;
two policy blocks in a row; an irreversible step; an unknown dialog, blank page or refusal; the step or
time budget. Replay: any hard failure under `on_hard_failure: pause`, an irreversible step without a
matching `--confirm-step`, `REAUTH_UNSAFE`. Business outcomes never page a human.

**Taking control.** `RUNNING → STUCK_EVALUATING → AWAITING_HUMAN → HUMAN_IN_CONTROL →
HANDBACK_VERIFYING → RUNNING`, with `controller` derived from the state. `Surface.act` demands a
`ControlToken` and raises `ControlViolation` unless automation holds control: code, unit-tested, not
convention. An `InterventionRequest` (capability or goal, step, intent, trigger, reason, URL, redacted
screenshot, last events, CDP endpoint, deadline) is written to the run directory. The operator uses
**the same Chromium window** the automation was driving. On claim, `recorder.js` is injected into
every frame and the human's clicks, typed values (password fields never send a value), keys and
navigations stream into `human_actions.jsonl`, logged with `controller: human`. The automation only
polls.

**Handing back.** Resume carries the pending intervention id (a stale id gets 409) and a mode:
`retry_step`, `skip_step` (the step's `expect` is verified first), `complete` (only the final
checkpoint remains); `confirm`/`decline` answer an irreversible step; `abort` ends as `declined`. A
failed check raises a new intervention, bounded by `max_handoffs`; nobody claiming within
`handoff_timeout_s` ends as `ESCALATION_TIMEOUT`. Every transition is an event with actor and reason;
the result carries `handoffs[]`. Evidence: `evidence/handoff-unknown_interstitial/`, where the
operator is a second CDP client on the same browser. Mocked deliberately: the console is one static
page over the file channel, loopback-only, with no authentication beyond the random intervention id;
production fronts it with a CDP/noVNC view and operator identity. Machine, token and channel are
unchanged by that.

## 6. Safety

**Allowlist** (`policies/ledgerline.yaml`): origins, route globs with deny-wins (`/__chaos**` is
denied, so origin allowlisting alone is shown to be insufficient), action kinds, step and time budgets.
Enforced before every action, on `navigate` destinations and link targets, and re-checked on the
current URL. `teller policy check` verifies an artifact statically; the policy's sha256 is stamped
into every result.

**Risk classes.** `read` proceeds. `reversible_write` proceeds with a before screenshot and
`idempotent: false`. `irreversible_write` (money movement, closures, deletions: control-text patterns
on committing controls, submitting-route patterns, dialog text, policy-listed controls) is **never
performed without a human decision**: discovery blocks and escalates; replay pauses for confirmation
unless the caller pre-authorised that exact `step@version`; `--unattended` ends as `declined`.
Flag-only performs the action anyway; block-always makes the system useless for postings; a human
decision or a version-pinned pre-authorisation is the only handling both useful and defensible for a
bank. Patterns apply to committing controls, so the model can open the "Post Transaction" form and be
refused at the Post button (live-tested).

**Redaction.** Credentials come from the environment through the harness login; the model has no
credential tool and never sees a login screen. Every persisted screenshot, in discovery and replay,
first wraps text matching the policy regexes and any registered sensitive value, then Playwright masks
those spans together with password fields and profile selectors, so model and disk receive the same
redacted image. `pii_high` values appear as `****last4` in every log and result; full values stay in
memory unless `--emit-full-outputs`. Artifacts hold references only; DOM snapshots strip input values
and sensitive selectors; no tracing is enabled and the exporter refuses trace files; an audit test
greps every text file under `evidence/`, `capabilities/`, `apps/`, `tenants/` and `policies/` for
fixture secrets and account numbers.

**Limits.** Risk classification is pattern-based and can miss an innocuous label, hence
`explicit_elements` and human approval. Regex PII detection is heuristic and depends on the DOM
walker seeing the node. The provider receives redacted screenshots of synthetic data; in production
that is a data-processing agreement, not a code change. The network-level route fence (`page.route`)
is designed, not built.

## 7. Cuts

- **Provider.** The discovery run used Gemini's free tier (`gemini-3.6-flash`). The model was never the
  problem: in all three attempts it chose a correct control every turn. The quota was (5 requests per
  minute, 20 per day per model); two earlier attempts on `gemini-3.8-flash` completed all six actions
  and were cut off one call before `done`. The loop now paces itself, honours the server's retry delay
  and fails fast on the daily cap. Switching to Anthropic is `--provider anthropic`.
- **One flow discovered with the model.** The post-transaction flow (`irreversible_write`) is covered
  by a hand-authored artifact in the live tests (Post refused unattended, performed only when
  pre-authorised) but was not discovered with the model, to spend the daily quota on a clean hero run.
- **Designed only:** `page.route` fence; desktop surface; tenant health scoring; CDP/noVNC console; a
  real second tenant skin (`example-b` runs against the same mock and shows the fallback, not a
  relabel). `coords_verified` is emitted last and never resolved in any run.
- **What the pre-submission audit taught.** Replay step screenshots went through a path that masked
  only by CSS selector, so account numbers were legible in the first evidence set while the model's own
  screenshots were masked; every persisted screenshot now goes through the same text wrapping, with a
  live test asserting it. Playwright's accessibility engine does not treat a sibling-cell label as an
  accessible name, so `role_name` could never resolve for legacy inputs; the recorder now skips it for
  geometrically labelled controls. After a remedy or a handback a step's postcondition may already
  hold, and re-acting would look for a control that is legitimately gone; the engine checks first.
- **Next, in order:** the irreversible flow discovered and refused with the model as evidence; the
  route fence; a real second tenant skin; health scoring from `index_used`; an agent-facing catalogue
  projecting approved capabilities into tool definitions (the result contract and exit codes already
  are the interface).
