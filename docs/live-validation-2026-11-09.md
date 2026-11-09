# Live validation record

This record describes the test deployment at `https://forgedev.ru` and is not
an expert accuracy certification.

## Release

- Application revision: `9e1bc246cfadec6b9aa8c170f595a8723c8f0ece`
- Prompt revision: `claims-semantic-entailment-v6`
- Active KB revision during the question run:
  `d5f1bbba0094d1eb7e4918bd7a1d37c621ae5cd498010ca2cba041ddddafd66a`
- `/health/ready`: HTTP 200, ready
- Docker image rebuilt from the release archive; PostgreSQL remained healthy.

## DeepSeek response smoke

The same 24 local question IDs were run in separate sessions before and after
the verifier prompt update. The question set had already been used during
development, so it is a regression set, not an independent or blind gold set.

| Run | Prompt | Answer returned | Safe fallback | HTTP errors |
| --- | --- | ---: | ---: | ---: |
| Before calibration | `claims-context-quantity-v5` | 19/24 | 5/24 | 0 |
| After calibration | `claims-semantic-entailment-v6` | 21/24 | 3/24 | 0 |

All 24 traces used BM25 with no configured embedding or reranker endpoint. The
five baseline fallbacks and the three remaining post-update fallbacks were
rejected by claim verification. A subsequent direct retry of those three
remaining cases passed claim verification, which shows the result is
stochastic; it does not prove correctness. The observed increase in returned
answers must not be reported as a measured answer-accuracy gain. Independently
annotated gold answers, unanswerable labels, and blind holdout review are still
needed for the answer quality KPIs.

## Latency smoke

The 20-request run at concurrency 4 is recorded in
[`runtime-benchmark-forgedev-20261109.json`](runtime-benchmark-forgedev-20261109.json).
All 20 sessions were created, all 20 chat requests returned HTTP 200, all 20
returned trace IDs, and none returned the safe fallback. Measured HTTP latency:

- p50: 4.11 s
- p95: 5.12 s
- p99: 7.22 s

This small synthetic sample meets the 8 s p95 target for that run only. A
separate 10-request sample had a 10.07 s p95, demonstrating external model
latency variation. TTFT is unavailable because streaming is not implemented.
The 50/100-concurrent-session and outage/chaos targets remain untested; the
current 1 vCPU / 2 GB test host and API rate limits do not establish those
capacity targets.

## Organizer demo and retrieval regression

The organizer's `demo_questions.csv` is the same file as local
`data/questions.csv` (SHA-256
`2adab2f1781b8b12c50230bf82a1af2e74259fc09ff6f998eb5ae68ef72ac4ea`). The
18-row HTTP run created and closed all sessions, returned HTTP 200 for every
row, returned 14 substantive answers, and used the safe fallback for 4. All
four fallbacks had `claim_verification_failed`; there were no retrieval/API
errors. In-memory replays later passed verification for those cases at least
once, confirming that a single live pass is stochastic. This is not an
answer-correctness score, and the four original refusals remain in the report.

On the same 18 questions and 292 chunks, the deterministic BM25 regression
reported source Recall@5/@10 = 1.00 and evidence Recall@10 = 1.00, MRR@10 =
0.89 across 19 evidence anchors. The detailed reports are
[`retrieval-baseline.json`](retrieval-baseline.json) and
[`chunk-retrieval-baseline.json`](chunk-retrieval-baseline.json). This dataset
was used during development, so these are regression results, not a blind
holdout. The live deployment used BM25 only: compatible embedding and reranker
endpoints were not configured.

## UI artifacts

- Desktop: [`user-page-desktop.png`](../output/screenshots/user-page-desktop.png)
- Mobile: [`user-page-mobile.png`](../output/screenshots/user-page-mobile.png)

Both were captured from the deployed page at HTTP 200. The mobile page width
matched the 390 px viewport with no horizontal overflow. The screenshots are
visual evidence, not a full keyboard/screen-reader accessibility audit.
