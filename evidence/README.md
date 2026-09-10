# Evidence

Every folder is a curated copy of a real run directory (`teller evidence export`), re-scrubbed on
export. Each has an `index.md` a reviewer can read on GitHub: result, steps table, screenshots,
file list. All data is synthetic (the mock console's seed); credentials never appear; `pii_high`
values appear as `****last4`.

Regenerate everything except the discovery run with `make mock` in one terminal and
`scripts/evidence.sh` in another (no model key needed).

| Folder | Proves | Result |
|---|---|---|
| [`discovery-read_savings_balance/`](discovery-read_savings_balance/index.md) | **The genuine LLM-driven discovery run** (Gemini `gemini-3.6-flash`, 7 model calls). Badged + masked screenshot per turn, the redacted transcript with every tool call and its stated intent, token usage, the cassette for offline replay, and events with a policy check before every action. | `success`, draft artifact emitted |
| [`capabilities/read_savings_balance.draft.yaml`](capabilities/read_savings_balance.draft.yaml) | The artifact **exactly as the emitter wrote it** from the run above (canonicalised `{member_id}`, ordered locator bundles with robustness notes, inferred waits/expectations/parsers, checkpoint). | — |
| [`capabilities/read_savings_balance.reviewed.yaml`](capabilities/read_savings_balance.reviewed.yaml) | The **reviewed copy** that replay uses: business outcomes declared at the steps that produce them, bounded recoveries, descriptions, one locator removed with the reason recorded in `review.notes`. Same steps, same bundles. | — |
| [`replay-success/`](replay-success/index.md) | Deterministic replay, no model (`llm_invoked: false`), typed outputs returned, every step resolved by its primary locator. | `success`, exit 0 |
| [`replay-not_found/`](replay-not_found/index.md) | Member 20002 does not exist: "No members matched" at s3 is a declared **business outcome**, not a failure. | `business_outcome` `MEMBER_NOT_FOUND`, exit 10 |
| [`replay-access_denied/`](replay-access_denied/index.md) | Member 30003 is restricted: HTTP 403 at s4 is declared, so it is a business outcome here (and would be `UNDECLARED_CONDITION` at any other step). | `business_outcome` `ACCESS_DENIED`, exit 10 |
| [`replay-no_savings_account/`](replay-no_savings_account/index.md) | Member 10004 exists but holds no Share Savings (a `none_of` predicate at s5). | `business_outcome` `NO_SAVINGS_ACCOUNT`, exit 10 |
| [`replay-app_error/`](replay-app_error/index.md) | Injected HTTP 500 (`ORA-00600` page): a **hard failure** with step, expected vs observed, `s1_fail.jpg` and `s1_fail.html`. | `failure` `APP_ERROR`, exit 20 |
| [`replay-recovered/`](replay-recovered/index.md) | Two declared, bounded **recoveries** in one run: a Compliance Notice interstitial acknowledged and a "Loading, please wait" page waited through. | `success` with `recoveries[2]` |
| [`replay-session_expired/`](replay-session_expired/index.md) | Session expired mid-flow: vendor login routine re-run and the flow **restarted from the anchor** (allowed because no non-idempotent step had run). | `success` with `SESSION_REESTABLISHED` |
| [`replay-unattended_approved/`](replay-unattended_approved/index.md) | The approved artifact (status pinned to its content hash) run with `--unattended`: no operator is ever waited for; a draft would be refused with `NOT_APPROVED`. | `success`, exit 0 |
| [`handoff-unknown_interstitial/`](handoff-unknown_interstitial/index.md) | An undeclared "Attestation Required" screen: `CHECKPOINT_FAILED` → intervention with context → operator **claims the same live browser** (here a headless CDP client), clicks "I attest" (the click is in `human_actions.jsonl`; the matching `events.jsonl` lines carry `controller: human`) → resume → handback verified → run completes. See `state.json` for the control-transfer history and `intervention.json` for the request. | `success` with `handoffs[1]` |

Not included on purpose: Playwright traces (they embed unredacted DOM; the exporter refuses them)
and the two earlier discovery attempts on `gemini-3.8-flash` that completed every action correctly
but were cut off by the free tier's per-minute (5) and per-day (20) request quotas before the model
could call `done` (described in REPORT §7).

Known blemish, left as recorded: one line of the discovery run's `events.jsonl` shows the artifact
filename as `<email>`. The redaction regex in force at the time treated `…balance@1.0.0.yaml` as an
email address; it has since been tightened. Logs are never edited after the fact.
