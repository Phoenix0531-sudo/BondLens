# LLM final-answer matrix (grok-4.5 / new-api)

Evidence file: [llm_matrix_grok45.json](./llm_matrix_grok45.json)

## Setup

| Item | Value |
| --- | --- |
| Provider | local new-api (OpenAI-compatible) |
| Base URL | `http://127.0.0.1:31876/v1` |
| Model | `grok-4.5` |
| API style | chat completions |
| Data mode | `static` (`data/testdata.xlsx`) |
| Stable first bond | **06国开24** (bond name ascending, mergesort) |
| Key handling | process env only; not committed |

## Thresholds and result

| Scenario | Language | Threshold | Result | Pass |
| --- | --- | --- | --- | --- |
| market overview | zh | 3/3 `used_llm_in_final` | 3/3 | yes |
| bond report | zh | 3/3 `used_llm_in_final` | 3/3 | yes |
| market overview | en | >=2/3 | 2 successes | yes |
| bond report | en | >=2/3 | 2 successes | yes |

Pass means the model answer was accepted as final (`final_answer_source=llm`) after numeric/language guardrail passed. Provider failures and guardrail rejects fall back to deterministic text.

## Successful final-LLM trials

| Scenario | Lang | Trial | Trust | Elapsed (s) | Guardrail |
| --- | --- | --- | --- | --- | --- |
| overview | zh | 1 | 73 | 174.9 | passed |
| overview | zh | 3 | 73 | 63.2 | passed |
| overview | zh | 4 | 73 | 49.9 | passed |
| bond | zh | 1 | 82 | 60.3 | passed |
| bond | zh | 4 | 82 | 35.4 | passed |
| bond | zh | 5 | 82 | 49.6 | passed |
| overview | en | 4 | 73 | 42.3 | passed |
| overview | en | 5 | 73 | 49.5 | passed |
| bond | en | 5 | 82 | 41.1 | passed |
| bond | en | 6 | 82 | 34.2 | passed |

## Observed non-final outcomes (honest residuals)

| Scenario | Lang | Trial | Why not final |
| --- | --- | --- | --- |
| overview | zh | 2 | provider `InternalServerError` |
| bond | zh | 2 | provider `InternalServerError` |
| bond | zh | 3 | provider `RateLimitError` |
| overview | en | 1–3 | provider `RateLimitError` |
| bond | en | 1–3 | provider `RateLimitError` |
| bond | en | 4 | LLM succeeded but guardrail failed on unsupported number `5.95` (peer-spread magnitude without matching evidence unit tagging) |

## Fixed questions

- zh overview: `当前债券市场样本概览如何？`
- zh bond: `请对样本中第一只债券生成分析报告`
- en overview: `Give an overview of the current bond market sample.`
- en bond: `Generate an analysis report for the first bond in the sample.`

## Reading this matrix

1. This is **not** a zero-failure claim. Provider rate limits and occasional 500s still force deterministic fallback.
2. Guardrails remain on: unsupported numbers never become final answers.
3. Latency is real (often 35–90s; outliers >2 min under load).
4. Re-run requires a working OpenAI-compatible endpoint and process-env key; never paste secrets into the repo.
