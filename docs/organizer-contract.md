# Verified organizer submission contract

The user-provided organizer bundle `drive-download-20261109T131859Z-1-001.zip`
was inspected without executing its contents. The archive SHA-256 is
`447557e7494caa184eee1a14c4901bd493a333c1376cc2561f4d3666664f9109`. Its nested
`sample_submission.zip` SHA-256 is
`39500d4d3299135e7e97e90280c034ddad862ca4c69cf74df02544ba999dd101`.

The nested sample README and `solution.py` establish the following contract:

- `DATA_DIR/questions.csv` has `id,question` columns. `train.csv` is an accepted
  alias. A row may contain a multi-turn transcript in its `question` value.
- `DATA_DIR/knowledge-base.zip` contains Markdown or TXT documents. The accepted
  archive aliases are `knowledge_base.zip` and `kb.zip`.
- The process writes `submission.csv` in its working directory with `id,answer`
  columns and preserves input row IDs. It must not print answers instead of
  writing the output file.
- The platform supplies an OpenAI-compatible base URL and key via
  `LLM_BASE_URL` / `BASE_URL` / `OPENAI_BASE_URL` and
  `LLM_API_KEY` / `OPENAI_API_KEY`. The sample calls `/chat/completions`,
  `/embeddings`, and `/rerank`.
- The sample's configured model identifiers are `qwen/qwen3.8-27b`,
  `baai/bge-m3`, and `qwen/qwen3-reranker-8b`. The existence of these identifiers
  in the sample does not prove that every endpoint is enabled for this account.
- The submission ZIP contains the entry point, its local `core` modules, and a
  root `requirements.txt`; it must not contain the KB, questions, API keys, or
  `.env` files.

The repository adapter implements these file and output contracts in
`solution.py`; `package_submission.py` creates the platform ZIP. The clean-room
packaging test checks archive contents, CSV quoting/IDs, transcript-safe
handling, and fail-closed behavior without API credentials.

## Still unverified

The provided sample does not specify runtime CPU/RAM/GPU limits, API quotas,
permitted network destinations, exact history serialization beyond the sample
CSV, or permission to transmit the supplied KB to an external model provider.
Confirm those against the organizer's current runtime rules before submission.
