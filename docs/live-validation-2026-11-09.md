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

## UI artifacts

- Desktop: [`user-page-desktop.png`](../output/screenshots/user-page-desktop.png)
- Mobile: [`user-page-mobile.png`](../output/screenshots/user-page-mobile.png)

Both were captured from the deployed page at HTTP 200. The mobile page width
matched the 390 px viewport with no horizontal overflow. The screenshots are
visual evidence, not a full keyboard/screen-reader accessibility audit.
