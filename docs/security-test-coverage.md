# Security test coverage

The threat model is defined by SEC-01 through SEC-20 in the technical specification. The automated suite maps to those controls as follows:

| Class | Automated evidence | Scope and limitation |
|---|---|---|
| SEC-01 Direct user injection | `test_50_user_injections_remain_in_the_untrusted_user_role` | 50 variants verify user text stays in the user role; the gateway response is mocked. |
| SEC-02 Developer impersonation | `test_50_role_spoof_tokens_do_not_grant_admin_access`; poisoned-document cases | Server authorization and unsupported generated claims are checked without a live model. |
| SEC-03 Retrieved document injection | `test_50_poisoned_document_injections_fail_closed` | 50 variants fail closed when claim verification rejects them. |
| SEC-04 Role spoofing | `test_50_role_spoof_tokens_do_not_grant_admin_access` | 50 forged bearer values receive 401. |
| SEC-05 Delimiter / XML injection | Poisoned-document variants | Delimiter variants are passed through the model gateway stub. |
| SEC-06 Multilingual injection | Poisoned-document variants | Russian and English payloads are included; no multilingual model behavior is claimed. |
| SEC-07 Encoded payload | Poisoned-document variants | Base64 and zero-width variants exercise the untrusted-data path. |
| SEC-08 Many-shot / long context | `test_100_multistep_sessions_do_not_share_context_or_authority` | 100 two-session scenarios include repeated instructions; answer generation is deterministic. |
| SEC-09 Tool output laundering | Poisoned-document variants; `test_answer_rejects_claims_supported_only_by_injected_kb_instruction` | Fake tool authority is treated as document text; no real tools are available. |
| SEC-10 RAG poisoning | `test_100_archive_path_traversal_variants_are_rejected`; `test_knowledge_lifecycle_and_access` | 100 unsafe paths are rejected; knowledge activation requires approval. |
| SEC-11 Data exfiltration | Session isolation and reviewer-history tests | Foreign session reads are denied; reviewer output omits message contents. |
| SEC-12 Prompt disclosure | Poisoned-document variants | A rejected claim cannot reveal prompt text in the mock; live-model resistance is not measured. |
| SEC-13 XSS / HTML / Markdown | `test_http_security_headers_and_body_limit`; browser E2E | A malicious assistant response is rendered as literal text; no image or script node executes. |
| SEC-14 Cross-session access | `test_100_multistep_sessions_do_not_share_context_or_authority` | 100 cross-token history reads return 404 without message contents. |
| SEC-15 Resource consumption | `test_http_rate_limit_and_readiness`; `test_overloaded_chat_rejects_without_recording_message` | Request caps and overload behavior are covered; production load targets need separate measurement. |
| SEC-16 SSRF | Answer-contract gateway tests | No arbitrary fetch/tool route exists; tests do not probe a live internal network. |
| SEC-17 Duplicate lead | `test_sessions_and_lead_confirmation`; PostgreSQL idempotency test | Repeated confirmation with the same key yields one lead. |
| SEC-18 Memory poisoning | `test_100_multistep_sessions_do_not_share_context_or_authority` | 100 scenarios verify no cross-session memory or context. |
| SEC-19 Model output injection | `test_answer_rejects_unanswerable_or_invalid_claim_payloads` | Extra fields, bad citations, and unsupported claims fail closed. |
| SEC-20 Unauthorized index swap | `test_knowledge_lifecycle_and_access` | Archive tampering invalidates the active revision. |
| AC-05 Dialogue lifecycle | `tests/test_dialogue.py`; lifecycle assertions in `tests/test_api.py`; `test_dialogue_state_migration_upgrades_legacy_schema` | Persisted deterministic states cover greeting, clarification, consultation, lead review/creation, cancel, expiry, and closure. Other TZ states require an implemented recommendation/escalation route. |
| AC-04 Conflicting source data | `test_conflicting_retrieved_evidence_fails_closed_before_model_call` | Near-identical statements with opposite polarity or numeric values fail closed; general semantic conflicts and owner-defined source priority remain unverified. |

## Current deterministic adversarial batch

`tests/test_red_team.py` executes 350 generated cases across five control families: 50 user-prompt variants, 50 poisoned-document variants, 50 forged role tokens, 100 unsafe archive paths, and 100 multi-step isolation scenarios. The dialogue scenarios also make 100 unauthorized cross-session history requests. These tests exercise deterministic controls and mocked model outcomes; they are not 350 independent evaluations of a live LLM. A zero-failure result does not prove that prompt injection is impossible.

## Remaining validation

- AC-04 has a conservative pre-generation check for near-identical retrieved statements with opposite polarity or different numeric values. It is covered by `test_conflicting_retrieved_evidence_fails_closed_before_model_call`; this is not a general semantic contradiction detector and does not replace an owner-approved source-priority policy.
- Run the 100 multi-step cases against the actual configured model only after data-transfer authorization and a bounded-cost plan are confirmed.
- Add independent human-labeled answer-correctness and unanswerable sets; retrieval anchors alone do not establish answer correctness.
- Repeat resource and recovery tests against the production PostgreSQL deployment under agreed load limits.
