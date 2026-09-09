# teller — discovery-to-replay computer use for legacy bank back-office UIs

**The model discovers. The artifact becomes a reusable capability. Deterministic replay is how an
AI agent invokes it in production.**

1. **Discovery** — an LLM drives a real, hostile legacy web UI (frameset, table layout, no ids)
   through an observe → decide → act loop to accomplish a natural-language goal. It acts only by
   numbered badges on the screen; a policy gate sits between every decision and the browser.
2. **Artifact** — the successful run is emitted as a typed, versioned, reviewable YAML capability:
   ordered steps, an ordered *bundle* of locator strategies per control with a robustness note
   each, typed params and outputs, declared business outcomes, bounded recoveries, a checkpoint.
3. **Replay** — the capability runs with no model in the loop, classifies what happened
   (`success` / `business_outcome` / `failure` / `declined`), returns typed outputs, and leaves a
   debuggable trail (events, screenshots, DOM snapshot on failure).
4. **Handoff** — when it cannot safely proceed, it pauses on the **same live browser session**,
   raises an intervention with context, lets a human take control (recorded), and resumes.

Target surface: **Ledgerline Credit Union — Member Servicing Console**, a self-built mock of a
2005-era core-banking back office with on-demand fault injection. No real systems, credentials or
PII anywhere.

Design write-up: [`REPORT.md`](REPORT.md). Evidence of real runs: [`evidence/`](evidence/README.md).

---

## Setup

Requirements: Python 3.12, a Chromium that Playwright can install (`make install` does it), and —
for the discovery run only — an LLM API key. Replay, the mock app and the whole test suite run
with no key and no display.

```bash
git clone <this repo> && cd teller
make install                 # venv, dependencies, Playwright Chromium
cp .env.example .env         # then set GEMINI_API_KEY (or ANTHROPIC_API_KEY) for discovery
```

`.env` is gitignored. The only secrets the system ever handles are the mock console's login
(defaults `teller1` / `Ledger!2026`, synthetic) and your model API key; neither is written to any
artifact, log or screenshot (see *Safety* in the report and `tests/guards/test_redaction_audit.py`).

## Demo path

Terminal 1 — the target application:

```bash
make mock                    # Ledgerline console on http://127.0.0.1:8600 (log in with teller1 / Ledger!2026 to look around)
```

Terminal 2 — discovery (real model), then replay (no model):

```bash
# 1) LLM-driven discovery: goal + typed params -> draft capability + evidence in runs/<run_id>/
.venv/bin/teller discover \
  --goal "Look up member 10001 and read the current balance and account number of their Share Savings account" \
  --param member_id=10001 --param-decl "member_id:string:pii_low:^[0-9]{5}$" \
  --output savings_balance:decimal:pii_low:currency_usd \
  --output savings_account_number:string:pii_high \
  --provider gemini            # or: --provider anthropic

# 2) deterministic replay of the saved capability, other inputs, no model
.venv/bin/teller replay capabilities/ledgerline.member.read_savings_balance@1.0.0.yaml --param member_id=10001   # -> success, exit 0
.venv/bin/teller replay capabilities/ledgerline.member.read_savings_balance@1.0.0.yaml --param member_id=20002   # -> business_outcome MEMBER_NOT_FOUND, exit 10
.venv/bin/teller replay capabilities/ledgerline.member.read_savings_balance@1.0.0.yaml --param member_id=30003   # -> business_outcome ACCESS_DENIED, exit 10

# 3) inject runtime faults into the mock, then replay
.venv/bin/teller chaos arm app_error           && .venv/bin/teller replay capabilities/ledgerline.member.read_savings_balance@1.0.0.yaml --param member_id=10001 --hitl none   # -> failure APP_ERROR (+ s1_fail.jpg, s1_fail.html), exit 20
.venv/bin/teller chaos arm interstitial_known  && .venv/bin/teller replay capabilities/ledgerline.member.read_savings_balance@1.0.0.yaml --param member_id=10001                # -> success with a recorded recovery
.venv/bin/teller chaos arm expire_session      && .venv/bin/teller replay capabilities/ledgerline.member.read_savings_balance@1.0.0.yaml --param member_id=10001                # -> success, session re-established, restarted from the anchor

# 4) human handoff on the live session (headed browser + operator console at http://127.0.0.1:8787)
.venv/bin/teller chaos arm interstitial_unknown && .venv/bin/teller replay capabilities/ledgerline.member.read_savings_balance@1.0.0.yaml --param member_id=10001 --headed
#   -> the run pauses on an "Attestation Required" screen it does not know; open the console, Claim,
#      click "I attest" in the automation's own Chromium window, then "Hand control back" (retry_step).
#   Headless twin of the same channel from another shell:
.venv/bin/teller intervene claim <run_id> --operator you && .venv/bin/teller intervene resume <run_id> --mode retry_step
```

Other modes: `--provider scripted --cassette runs/<id>/cassette.json` replays a recorded discovery
through the real loop and browser with no key; `--unattended` never waits for a human and refuses
`draft` artifacts; `--confirm-step s7@1.0.0` pre-authorises one irreversible step by id and version.

`make` shortcuts: `make discover`, `make demo-replay`, `make replay-not-found`, `make replay-error`,
`make replay-recovered`, `make handoff-demo`, `make test`, `make lint`, `make schemas`.

## Reading the output

`replay` prints the result contract and exits `0` success · `10` business outcome · `20` failure ·
`30` declined (a human stopped or refused; or policy needed a human in `--unattended`). Every run
writes `runs/<run_id>/` with `events.jsonl` (structured, redacted, `controller` on every line),
`screenshots/`, `result.json`, and on failure `<step>_fail.jpg` + `<step>_fail.html`. Curated copies
for the reviewer live under [`evidence/`](evidence/README.md).

## Layout

```
capabilities/     emitted + reviewed capability artifacts (<id>@<semver>.yaml)
apps/<profile>/   vendor layer: login routine, generic detectors, sensitive selectors, ui fingerprint
tenants/          institution layer: base_url, credential env refs, policy, sparse overrides
policies/         allowlist (origins, routes, action kinds), risk patterns, redaction regexes
schema/           JSON Schema generated from the pydantic models (drift-tested)
evidence/         curated runs: discovery, replays (success / outcome / error / recovered), handoff
mockapp/          Ledgerline console (FastAPI + Jinja2) with the one-shot /__chaos fault API
src/teller/
  surface/        Surface protocol; WebPlaywrightSurface (only Playwright import); marks.js, resolvers.js, recorder.js
  discovery/      tools, prompts, Decider seam (gemini | anthropic | scripted), loop, recorder, emitter
  artifact/       pydantic schema + store (load → tenant override merge → validate; approval hash)
  replay/         model-free interpreter, classification, detectors, parsers, result contract
  policy/         PolicyGate (single call site), RiskClassifier, Redactor (single writer path)
  hitl/           control-transfer state machine, ControlToken, handoff controller, operator console, intervene CLI
  evidence/       JSONL event log, evidence export
tests/            unit · guards (architecture boundaries, redaction audit) · integration (live mock + headless Chromium)
```

## Tests

```bash
make test          # everything: ~250 tests incl. live replay/handoff scenarios against the mock (starts its own instance)
make unit          # fast: schema, policy, state machine, parsers, classification, emitter, mock app
make lint
```
