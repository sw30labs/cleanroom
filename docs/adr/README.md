# Architecture Decision Records — sw30labs/cleanroom

Retrospective ADRs for **Cleanroom**: an agentic single-file hygiene pipeline (Analyze → Recommend → Remediate → QA) with a deterministic offline path, optional local LLM steps on OMLX or DGX vLLM, a CLI and a local-only dashboard. Reviewed at `f0ac0be` (`main`, three commits).

Format: Title; Status; Context; Decision; Consequences (positive/negative); Alternatives considered; Evidence (file paths, commit SHAs). All records here were written retrospectively on 2026-11-13 from a full read of the repository at the reviewed commit. Where the *why* is inferred rather than stated in the code, docs, or commit messages, the ADR says so and the inference is logged in [`../assumptions.md`](../assumptions.md).

| ADR | Title | Status |
|---|---|---|
| [0001](0001-four-phase-langgraph-pipeline.md) | Four-phase linear LangGraph pipeline: Analyze → Recommend → Remediate → QA | Accepted (retrospective) |
| [0002](0002-deterministic-first-llm-optional.md) | Deterministic-first processing; every LLM step is optional and degrades gracefully | Accepted (retrospective) |
| [0003](0003-never-mutate-original-atomic-sibling-writes.md) | Never mutate the original: write a `.cleaned` sibling atomically, back up any previous output | Accepted (retrospective) |
| [0004](0004-unicode-mark-detection-and-ascii-fold.md) | Detect marks by named sets plus Unicode-category fallback; strip invisibles and fold visible Latin typography to ASCII | Accepted (retrospective) |
| [0005](0005-markdown-ai-frontmatter-strip.md) | Strip AI-provenance keys from Markdown YAML frontmatter, allowing a comment header before it | Accepted (retrospective) |
| [0006](0006-honest-binary-metadata-handling.md) | Binary, image, PDF and office files: detect and flag metadata, copy through unchanged, never claim removal | Accepted (retrospective) |
| [0007](0007-strength-levels-safe-aggressive.md) | Two strength levels: `safe` (deterministic only, default) and `aggressive` (adds LLM rewrite) | Accepted (retrospective) |
| [0008](0008-bounded-judge-and-revise-qa.md) | QA uses deterministic checks plus a bounded LLM judge-and-revise loop | Accepted (retrospective) |
| [0009](0009-single-openai-compatible-client-two-backends.md) | One OpenAI-compatible httpx client for two local backends (OMLX on the Mac, vLLM on the DGX) | Accepted (retrospective) |
| [0010](0010-loopback-only-llm-egress.md) | LLM endpoints must be loopback unless egress is explicitly allowed | Accepted (retrospective) |
| [0011](0011-layered-strict-configuration.md) | Layered configuration (code > env > YAML > defaults), strict YAML, secrets only in env | Accepted (retrospective) |
| [0012](0012-local-only-stdlib-dashboard.md) | Local-only stdlib dashboard with one job at a time | Accepted (retrospective) |
| [0013](0013-append-only-jsonl-ledgers.md) | Append-only JSONL run ledger and LLM telemetry ledger | Accepted (retrospective) |
| [0014](0014-single-source-setup-script.md) | One setup-and-run script for both machines; venv chosen by platform, backend detected from the served model | Accepted (retrospective) |
| [0015](0015-dependency-free-persona-avatars.md) | Animated persona avatars as a dependency-free plain-script canvas widget | Accepted (retrospective) |
