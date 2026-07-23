import json
from pathlib import Path
import re

import yaml

from clearagent.evals.suite import EvalSuite


def export_promptfoo_config(agent_path: str, suite: EvalSuite, out: str | Path) -> Path:
    _split_agent_path(agent_path)
    target_path = ".clearagent/promptfoo_target.py"
    tests = []
    for case in suite.cases:
        assertions = []
        for check in case.checks:
            name, value = next(iter(check.items()))
            if name == "contains":
                assertions.append({"type": "contains", "value": value})
            elif name == "not_contains":
                assertions.append({"type": "not-contains", "value": value})
            elif name == "equals":
                assertions.append({"type": "equals", "value": value})
            elif name == "contains_any":
                if not isinstance(value, list):
                    raise ValueError("contains_any must be a list for Promptfoo export.")
                assertions.append(
                    {
                        "type": "javascript",
                        "value": f"{json.dumps(value)}.some((value) => output.includes(value))",
                    }
                )
            elif name == "regex":
                try:
                    re.compile(value)
                except re.error as exc:
                    raise ValueError(f"invalid regex for Promptfoo export: {exc}") from exc
                assertions.append(
                    {
                        "type": "javascript",
                        "value": f"new RegExp({json.dumps(value)}).test(output)",
                    }
                )
            elif name == "refuses":
                if not isinstance(value, bool):
                    raise ValueError("refuses must be a boolean for Promptfoo export.")
                refusal_js = (
                    "['cannot', \"can't\", 'unable', 'sorry'].some((value) => output.toLowerCase().includes(value))"
                )
                assertions.append(
                    {
                        "type": "javascript",
                        "value": refusal_js if value else f"!({refusal_js})",
                    }
                )
            else:
                raise ValueError(f"Unsupported check {name!r} for Promptfoo export.")
        tests.append(
            {
                "description": case.name,
                "vars": {"input": case.input},
                "assert": assertions,
            }
        )
    config = {
        "description": f"ClearAgent {suite.name} suite",
        "providers": [{"id": f"file://{target_path}"}],
        "tests": tests,
    }
    out_path = Path(out)
    out_path.write_text(yaml.safe_dump(config, sort_keys=False), encoding="utf-8")
    return out_path


def write_promptfoo_target(agent_path: str, out: str | Path) -> Path:
    module_path, object_name = _split_agent_path(agent_path)
    text = f"""from {module_path} import {object_name} as agent


def call_api(prompt, options, context):
    result = agent.run(prompt)
    return {{
        "output": result.output,
        "metadata": {{
            "run_id": result.run_id,
            "trace_db": str(result.trace_db_path),
        }},
    }}
"""
    out_path = Path(out)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(text, encoding="utf-8")
    return out_path


def _split_agent_path(agent_path: str) -> tuple[str, str]:
    if ":" not in agent_path:
        raise ValueError("agent path must use module:object format.")
    module_path, object_name = agent_path.split(":", 1)
    if not module_path or not object_name:
        raise ValueError("agent path must use module:object format.")
    return module_path, object_name
