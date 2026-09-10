#!/usr/bin/env bash
# Regenerate the replay + handoff evidence under evidence/ against a running mock console.
# Usage: make mock   (other terminal)   then   scripts/evidence.sh [discovery_run_id]
# Needs no LLM key: replay is model-free; the handoff operator is a headless CDP client.
set -euo pipefail
cd "$(dirname "$0")/.."
T=.venv/bin/teller
PY=.venv/bin/python
CAP=capabilities/ledgerline.member.read_savings_balance@1.0.0.yaml
export LEDGERLINE_USER="${LEDGERLINE_USER:-teller1}" LEDGERLINE_PASS="${LEDGERLINE_PASS:-Ledger!2026}"

last_run() { ls -td runs/rep_* | head -1 | xargs basename; }
export_last() { $T evidence export "$(last_run)" --to "evidence/$1" --note "$2" >/dev/null; echo "  -> evidence/$1"; }
replay() { $T replay "$CAP" --hitl none "$@" > /tmp/teller-replay.out 2>&1 || true; grep -E '"status"' /tmp/teller-replay.out | head -1 || true; $T chaos reset >/dev/null; }

curl -fsS -o /dev/null http://127.0.0.1:8600/login || { echo "mock console is not running (make mock)"; exit 1; }
$T chaos reset >/dev/null

echo "[1/8] replay success (member 10001)";            replay --param member_id=10001;  export_last replay-success "Deterministic replay of the discovered capability with the recorded input: SUCCESS with typed outputs (pii_high masked), exit 0."
echo "[2/8] replay not found (member 20002)";          replay --param member_id=20002;  export_last replay-not_found "'No members matched your search.' at s3 is a declared BUSINESS_OUTCOME (MEMBER_NOT_FOUND), not a failure: exit 10, returns member_id."
echo "[3/8] replay access denied (member 30003)";      replay --param member_id=30003;  export_last replay-access_denied "HTTP 403 at s4 is declared as BUSINESS_OUTCOME ACCESS_DENIED; the same detector at any other step would be a FAILURE (UNDECLARED_CONDITION)."
echo "[4/8] replay no savings account (member 10004)"; replay --param member_id=10004;  export_last replay-no_savings_account "Member exists but holds no Share Savings: BUSINESS_OUTCOME NO_SAVINGS_ACCOUNT at s5 (none_of predicate)."
echo "[5/8] replay injected 500";                      $T chaos arm app_error >/dev/null;           replay --param member_id=10001; export_last replay-app_error "Injected HTTP 500 (ORA-00600 page): hard FAILURE APP_ERROR with step, expected/observed, s1_fail.jpg and s1_fail.html; exit 20."
echo "[6/8] replay recovered (interstitial + slow)";   $T chaos arm interstitial_known >/dev/null; $T chaos arm slow >/dev/null; replay --param member_id=10001; export_last replay-recovered "Two declared, bounded recoveries in one run: Compliance Notice acknowledged (INTERSTITIAL_DISMISSED) and a 'Loading, please wait' page waited through (SLOW_LOAD_WAITED); still SUCCESS."
echo "[7/8] replay session expiry";                    $T chaos arm expire_session >/dev/null;      replay --param member_id=10001; export_last replay-session_expired "Session expired mid-flow: vendor login routine re-run and the flow restarted from restart_anchor (SESSION_REESTABLISHED) because no non-idempotent step had executed; SUCCESS."
echo "[8/8] handoff on the live session (unknown interstitial)"
$T chaos arm interstitial_unknown >/dev/null
$PY scripts/handoff_demo.py "$CAP" > /tmp/teller-handoff.out 2>&1 || true
grep -E '"status"|handoff' /tmp/teller-handoff.out | head -3
export_last handoff-unknown_interstitial "Replay hits an undeclared 'Attestation Required' screen -> CHECKPOINT_FAILED -> intervention raised -> operator claims the SAME live browser (here a headless CDP client), clicks 'I attest' (captured in human_actions.jsonl with controller=human) -> resume retry_step -> handback verified -> SUCCESS with handoffs[1]."

if [ "${1:-}" != "" ]; then
  echo "[+] discovery run $1"
  $T evidence export "$1" --to evidence/discovery-read_savings_balance --note "The genuine LLM-driven discovery run (Gemini) that produced the draft capability: badged+masked screenshots per turn, redacted transcript, token usage, cassette for offline replay, events with policy checks." >/dev/null
  mkdir -p evidence/capabilities
  cp "runs/$1/draft/"*.yaml evidence/capabilities/read_savings_balance.draft.yaml
  cp "$CAP" evidence/capabilities/read_savings_balance.reviewed.yaml
  echo "  -> evidence/discovery-read_savings_balance, evidence/capabilities/"
fi
echo "done"
