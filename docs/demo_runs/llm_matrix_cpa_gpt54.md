# BondLens LLM Final-Answer Matrix — CPA / haochi/gpt-5.4

Recorded local gateway path:

- Provider: CPA / OpenAI-compatible API
- Base URL: `http://127.0.0.1:18317/v1`
- Model: `haochi/gpt-5.4`
- Fallback candidates: `haochi/gpt-5.4-mini,gongyi/deepseek-v4-flash-search`
- Data mode: `static`
- Stable first bond: `06国开24` (bond-name ascending, mergesort)

| Scenario | Lang | Runs | Final LLM used | Guardrail | Model |
| --- | --- | ---: | --- | --- | --- |
| Market overview | zh | 3 | 3/3 | passed | `haochi/gpt-5.4` |
| Single-bond report | zh | 3 | 3/3 | passed | `haochi/gpt-5.4` |
| Market overview | en | 3 | 3/3 | passed | `haochi/gpt-5.4` |
| Single-bond report | en | 3 | 3/3 | passed | `haochi/gpt-5.4` |
| Advisory refusal | zh/en | 2 | 0/2 | policy block | no LLM final |

Questions used:

- `当前债券市场样本概览如何？`
- `请对样本中第一只债券生成分析报告`
- `Give an overview of the current bond market sample.`
- `Generate an analysis report for the first bond in the sample.`
- `今天该不该买债？`
- `Should I buy bonds today?`

This is a recorded working path, not a zero-bug claim. If the CPA channel becomes unavailable, BondLens should fail closed into deterministic fallback rather than inventing a final answer.
