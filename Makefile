.PHONY: install mock test unit integration schemas lint discover demo-replay replay-not-found replay-error replay-recovered handoff-demo approve evidence clean

PY := .venv/bin/python
TELLER := .venv/bin/teller
MOCK_PORT ?= 8600

install:
	python3.12 -m venv .venv
	$(PY) -m pip install -q --upgrade pip
	$(PY) -m pip install -q -e ".[dev]"
	$(PY) -m playwright install chromium

mock:
	@set -a; [ -f .env ] && . ./.env; set +a; $(PY) -m uvicorn mockapp.app:app --port $(MOCK_PORT)

test:
	$(PY) -m pytest

unit:
	$(PY) -m pytest tests/unit tests/guards tests/mockapp

integration:
	$(PY) -m pytest tests/integration -m integration

lint:
	.venv/bin/ruff check src mockapp tests

schemas:
	$(TELLER) schema export --to schema/

# --- demo path (mock app must be running: `make mock` in another shell) ---
discover:
	$(TELLER) discover --goal "Look up member 10001 and read the current balance and account number of their Share Savings account" --tenant local --param member_id=10001

demo-replay:
	$(TELLER) replay capabilities/ledgerline.member.read_savings_balance@1.0.0.yaml --tenant local --param member_id=10001

replay-not-found:
	$(TELLER) replay capabilities/ledgerline.member.read_savings_balance@1.0.0.yaml --tenant local --param member_id=20002

replay-error:
	$(TELLER) chaos arm app_error
	$(TELLER) replay capabilities/ledgerline.member.read_savings_balance@1.0.0.yaml --tenant local --param member_id=10001 --hitl none

replay-recovered:
	$(TELLER) chaos arm interstitial_known
	$(TELLER) replay capabilities/ledgerline.member.read_savings_balance@1.0.0.yaml --tenant local --param member_id=10001

handoff-demo:
	$(TELLER) chaos arm interstitial_unknown
	$(TELLER) replay capabilities/ledgerline.member.read_savings_balance@1.0.0.yaml --tenant local --param member_id=10001 --headed

approve:
	$(TELLER) approve capabilities/ledgerline.member.read_savings_balance@1.0.0.yaml --by "$(USER)"

evidence:            # regenerate evidence/ (mock must be running); pass DISC=<discovery run id> to include it
	scripts/evidence.sh $(DISC)

clean:
	rm -rf runs/ .pytest_cache .ruff_cache
