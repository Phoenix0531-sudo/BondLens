# Changelog

All notable changes to this project will be documented in this file.

## [0.1.0] - 2026-07-30

### Added
- Evidence-first Chinese bond analysis agent with Flask + Jinja UI.
- Deterministic tool chain for market overview, search, ranking, outliers, monitor boards, and single-bond reports.
- Trust Layer with evidence quality, numeric/language guardrails, answer judge, risk profile, Trust score, Evidence Pack, and replay summaries.
- Bilingual UI with Chinese default and a single header `中 / EN` switch using query/cookie memory.
- Optional OpenAI-compatible LLM narration under strict guardrails; CPA path validated with `haochi/gpt-5.4`.
- Live/snapshot/static data modes with explicit data lineage and deterministic no-key fallback.
- Docker packaging, Compose demo, `/healthz` health check, and CI gates for ruff, pytest, agent evals, red-team evals, and Docker health.

### Safety
- Advisory/buy/guaranteed-return/risk-free prompts are policy-blocked before LLM use.
- Unsupported LLM numbers or unsafe risk language fail closed to deterministic output.

### Notes
- This is a portfolio/research demo, not an investment advisory system.
