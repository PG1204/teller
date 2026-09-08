# teller — discovery-to-replay computer use for legacy bank back-office UIs

An LLM drives a real UI once to accomplish a goal (**discovery**). The successful run is turned into a
typed, versioned **capability artifact**. Production invocations **replay** that artifact deterministically
with no model in the loop, classify what happened (success / business outcome / failure), and **escalate to a
human** who takes over the same live browser session when the automation cannot safely proceed.

Target surface for this implementation: **Ledgerline Credit Union — Member Servicing Console**, a self-built,
deliberately legacy web app (frameset, table layout, no ids/test-ids, native dialogs, on-demand fault injection).

_Setup and demo path are filled in as the build lands (see `docs/PLAN.md`)._

## Setup

```bash
make install          # venv + deps + Playwright Chromium
cp .env.example .env  # add GEMINI_API_KEY (or ANTHROPIC_API_KEY) for discovery only
```

## Run

```bash
make mock             # terminal 1: the mock console on http://127.0.0.1:8600
make discover         # terminal 2: LLM-driven discovery run (needs a key)
make demo-replay      # deterministic replay of the saved artifact (no key)
```

## Layout

See `docs/PLAN.md` §11 while the build is in progress.
