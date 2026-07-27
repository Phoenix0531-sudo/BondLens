from __future__ import annotations

import os
import sys
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from bond_agent import BondAnalystAgent

CASES_PATH = Path(__file__).with_name("agent_eval_cases.yml")


def load_cases(path: Path = CASES_PATH) -> list[dict]:
    with path.open("r", encoding="utf-8") as file:
        return yaml.safe_load(file)


def evaluate_case(agent: BondAnalystAgent, case: dict) -> list[str]:
    result = agent.answer(case.get("question", ""))
    failures: list[str] = []

    expected_intent = case["expected_intent"]
    if result["plan"]["intent"] != expected_intent:
        failures.append(f"intent expected {expected_intent}, got {result['plan']['intent']}")

    expected_tools = case.get("expected_tools", [])
    if case.get("allow_extra_tools", False):
        for tool in expected_tools:
            if tool not in result["tools_used"]:
                failures.append(f"missing expected tool {tool}")
    elif result["tools_used"] != expected_tools:
        failures.append(f"tools expected exactly {expected_tools}, got {result['tools_used']}")

    answer = result["final_answer"]
    for text in case.get("must_include", []):
        if text not in answer:
            failures.append(f"final_answer missing required text: {text}")

    for text in case.get("must_not_include", []):
        if text in answer:
            failures.append(f"final_answer contains forbidden text: {text}")

    return failures


def run_evals() -> int:
    for key in list(os.environ):
        if key.startswith("OPENAI_"):
            os.environ.pop(key, None)

    agent = BondAnalystAgent(data_mode="static")
    cases = load_cases()
    failed = 0

    for case in cases:
        failures = evaluate_case(agent, case)
        if failures:
            failed += 1
            print(f"FAIL {case['id']}")
            for failure in failures:
                print(f"  - {failure}")
        else:
            print(f"PASS {case['id']}")

    print(f"\n{len(cases) - failed}/{len(cases)} eval cases passed")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(run_evals())
