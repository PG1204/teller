# Evidence: `replay-session_expired`

Session expired mid-flow: vendor login routine re-run and the flow restarted from restart_anchor (SESSION_REESTABLISHED) because no non-idempotent step had executed; SUCCESS.

## Result

- **status**: `success`
- **mode**: replay  ·  **run**: `rep_2026-09-10T05-55-00_7b6f`  ·  **llm_invoked**: False
- **capability**: `ledgerline.member.read_savings_balance@1.0.0` (draft)
- **params**: `{"member_id": "10001"}`
- **outputs**: `{"savings_account_number": "****1982", "savings_balance": "2431.17"}`
- **recovery**: `SESSION_REESTABLISHED` (session_expired) at `s1`

## Steps

| step | status | locator | index | ms | screenshot |
|---|---|---|---|---|---|
| s1 | ok | role_name | 0 | 196 | [s1_after.jpg](screenshots/s1_after.jpg) |
| s2 | ok | label_anchor | 0 | 150 | [s2_after.jpg](screenshots/s2_after.jpg) |
| s3 | ok | role_name | 0 | 183 | [s3_after.jpg](screenshots/s3_after.jpg) |
| s4 | ok | text_exact | 0 | 215 | [s4_after.jpg](screenshots/s4_after.jpg) |
| s5 | ok | table_cell | 0 | 133 | [s5_after.jpg](screenshots/s5_after.jpg) |
| s6 | ok | table_cell | 0 | 117 | [s6_after.jpg](screenshots/s6_after.jpg) |

## Screenshots

### s1_after
![s1_after](screenshots/s1_after.jpg)

### s2_after
![s2_after](screenshots/s2_after.jpg)

### s3_after
![s3_after](screenshots/s3_after.jpg)

### s4_after
![s4_after](screenshots/s4_after.jpg)

### s5_after
![s5_after](screenshots/s5_after.jpg)

### s6_after
![s6_after](screenshots/s6_after.jpg)

## Files

- `events.jsonl`
- `result.json`
- `screenshots/s1_after.jpg`
- `screenshots/s2_after.jpg`
- `screenshots/s3_after.jpg`
- `screenshots/s4_after.jpg`
- `screenshots/s5_after.jpg`
- `screenshots/s6_after.jpg`
- `state.json`
