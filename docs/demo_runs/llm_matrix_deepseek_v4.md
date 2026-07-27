# BondLens LLM matrix — deepseek-v4-flash-search (new-api)

- provider: new-api `http://127.0.0.1:31876/v1`
- model: `deepseek-v4-flash-search`
- data: static; first bond: 06国开24 (mergesort 债券简称)
- key: sk-***...PjTt
- notes: temperature=0; one-shot numeric repair; focused_numbers; ratio/bp/trunc bridges
- ALL_PASS: **True**

| case | final | ok |
|---|---|---|
| overview_zh | 3/3 | True |
| bond_zh | 3/3 | True |
| overview_en | 2/3 | True |
| bond_en | 3/3 | True |

## Advisory

- advisory_zh: llm_status=disabled, error=advisory_policy_block, final=False
- advisory_en: llm_status=disabled, error=advisory_policy_block, final=False

## Trial detail

### overview_zh
- t1: final=True guard=passed unsup=[] repair=False 9.0s
- t2: final=True guard=passed unsup=[] repair=False 8.71s
- t3: final=True guard=passed unsup=[] repair=False 6.83s

### bond_zh
- t1: final=True guard=passed unsup=[] repair=False 11.55s
- t2: final=True guard=passed unsup=[] repair=False 13.42s
- t3: final=True guard=passed unsup=[] repair=False 12.44s

### overview_en
- t1: final=False guard=failed unsup=['5%'] repair=True 23.21s
- t2: final=True guard=passed unsup=[] repair=False 9.61s
- t3: final=True guard=passed unsup=[] repair=False 8.36s

### bond_en
- t1: final=True guard=passed unsup=[] repair=False 16.27s
- t2: final=True guard=passed unsup=[] repair=False 15.14s
- t3: final=True guard=passed unsup=[] repair=False 13.23s
