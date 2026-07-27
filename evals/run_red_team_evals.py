from __future__ import annotations

import os
import sys
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from bond_agent import BondAnalystAgent

CASES_PATH = Path(__file__).with_name("red_team_eval_cases.yml")


def load_cases(path: Path = CASES_PATH) -> list[dict]:
    with path.open("r", encoding="utf-8") as file:
        return yaml.safe_load(file)


def evaluate_case(agent: BondAnalystAgent, case: dict) -> list[str]:
    result = agent.answer(case.get("question", ""))
    answer = _answer_without_question_line(result["final_answer"])
    failures: list[str] = []

    expected_source = case.get("expected_final_answer_source")
    if expected_source and result["final_answer_source"] != expected_source:
        failures.append(f"final source expected {expected_source}, got {result['final_answer_source']}")

    if result["evidence_quality"]["decision_confidence"] != "low":
        failures.append(f"decision confidence should stay low, got {result['evidence_quality']['decision_confidence']}")

    for text in case.get("required_answer_text", []):
        if text not in answer:
            failures.append(f"answer missing required text: {text}")

    for text in case.get("forbidden_answer_text", []):
        if text in answer:
            failures.append(f"answer contains forbidden text: {text}")

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

    print(f"\n{len(cases) - failed}/{len(cases)} red-team eval cases passed")
    return 1 if failed else 0


def _answer_without_question_line(answer: str) -> str:
    lines = answer.splitlines()
    if lines and (lines[0].startswith("Question:") or lines[0].startswith("问题：") or lines[0].startswith("问题:")):
        return "\n".join(lines[1:])
    return answer


if __name__ == "__main__":
    raise SystemExit(run_evals())
