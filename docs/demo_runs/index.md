# Bond Evidence Pack demos

Static packs generated with `data_mode=static` and no LLM key.
Open any `.html` file offline — no API key required.

| Pack | Question | Trust | Files |
| --- | --- | --- | --- |
| `demo-market-overview` | 当前样本收益率分布是什么样？ | 61/100 | [demo-market-overview.html](./demo-market-overview.html) · [demo-market-overview.json](./demo-market-overview.json) |
| `demo-bond-report` | 搜索23附息国债26并给出收益率分析 | 70/100 | [demo-bond-report.html](./demo-bond-report.html) · [demo-bond-report.json](./demo-bond-report.json) |
| `demo-yield-outliers` | 有没有收益率异常的债券？ | 61/100 | [demo-yield-outliers.html](./demo-yield-outliers.html) · [demo-yield-outliers.json](./demo-yield-outliers.json) |

## Optional LLM matrix evidence

When a local OpenAI-compatible provider is available, BondLens can narrate after guardrails pass.
A recorded matrix for `grok-4.5` via local new-api is kept here for README appendix / regression reference:

- summary: [llm_matrix_grok45.md](./llm_matrix_grok45.md)
- raw rows: [llm_matrix_grok45.json](./llm_matrix_grok45.json)

No API key is stored in these files.
