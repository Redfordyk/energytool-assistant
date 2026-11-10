# Functional test coverage

`tests/test_functional_matrix.py` contains 500 deterministic parameterized contract checks:

| Cases | Contract | Expected behavior |
|---:|---|---|
| 100 | Supported quantities and equivalent unit spellings | Claims are accepted with the source quote bound to the claim. |
| 50 | Incompatible physical units | Claims are rejected. |
| 50 | Changed numeric values | Claims are rejected. |
| 100 | Possible Russian mobile numbers | Numbers normalize to E.164. |
| 100 | Phone values containing unsupported characters | Input is rejected with HTTP 422. |
| 50 | Valid idempotency keys | Keys are accepted. |
| 50 | Malformed idempotency keys | Input is rejected with HTTP 422. |

These tests exercise production validators directly; they are functional unit contracts, not 500 browser or full-dialogue runs. The red-team suite separately contains 100 two-session multi-step scenarios and 350 adversarial cases. API-level lead confirmation, duplicate prevention, knowledge approval, session versioning, and browser workflows remain covered by their dedicated tests.

`tests/test_concurrent_chat.py` additionally opens 50 unrelated session locks together and, with PostgreSQL plus a deterministic blocked model stub, sends 50 concurrent chats. It verifies the configured four-chat capacity, 46 overload responses, and that rejected requests write no message rows. This is an overload-control contract, not a sustained 50-user latency or model throughput benchmark.

Run the complete suite, including browser E2E, with:

```powershell
$env:RUN_E2E = '1'
python -m pytest -q
```
