import os

import pytest
from dotenv import load_dotenv

from clearagent import create_agent
from clearagent.evals.runner import EvalRunner
from clearagent.evals.suite import EvalCase, EvalSuite

load_dotenv()


def _run_live_openrouter_eval() -> bool:
    return bool(os.environ.get("OPENROUTER_API_KEY")) and os.environ.get("CLEARAGENT_RUN_LIVE") == "1"


@pytest.mark.skipif(
    not _run_live_openrouter_eval(),
    reason="Set OPENROUTER_API_KEY and CLEARAGENT_RUN_LIVE=1 to run the live OpenRouter eval.",
)
def test_live_openrouter_eval_uses_openrouter_provider_and_trace_checks(tmp_path):
    agent = create_agent(
        name="openrouter_eval_smoke",
        model="openrouter:openai/gpt-4o-mini",
        system_prompt="Answer with exactly the word shipped.",
        trace_db_path=tmp_path / "traces.sqlite",
        temperature=0.0,
    )
    suite = EvalSuite(
        name="openrouter_live_smoke",
        type="output",
        cases=[
            EvalCase(
                name="openrouter trace",
                input="What is the order status?",
                checks=[
                    {"contains": "shipped"},
                    {"trace_provider": "openrouter"},
                    {"max_turns": 1},
                ],
            )
        ],
    )

    report = EvalRunner(agent).run_suite(suite)

    report.assert_passed()
