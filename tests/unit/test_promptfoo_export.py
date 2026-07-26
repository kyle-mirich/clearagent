import yaml
import pytest

from clearagent.evals.promptfoo_export import export_promptfoo_config, write_promptfoo_target
from clearagent.evals.suite import EvalCase, EvalSuite


def test_promptfoo_export_creates_yaml_and_target(tmp_path):
    suite = EvalSuite(
        name="safety",
        type="output",
        cases=[
            EvalCase(
                name="safe answer",
                input="hello",
                checks=[{"contains": "hi"}, {"not_contains": "bye"}],
            )
        ],
    )
    config_path = export_promptfoo_config(
        "examples.customer_support.agent:agent",
        suite,
        tmp_path / "promptfooconfig.yaml",
    )
    target_path = write_promptfoo_target(
        "examples.customer_support.agent:agent",
        tmp_path / "target.py",
    )

    config = yaml.safe_load(config_path.read_text(encoding="utf-8"))
    assert config["tests"][0]["description"] == "safe answer"
    assert config["tests"][0]["vars"]["input"] == "hello"
    assert config["tests"][0]["assert"][0] == {"type": "contains", "value": "hi"}
    assert "from examples.customer_support.agent import agent" in target_path.read_text(
        encoding="utf-8"
    )


def test_promptfoo_target_requires_module_object_agent_path(tmp_path):
    with pytest.raises(ValueError, match="module:object"):
        write_promptfoo_target("examples.customer_support.agent", tmp_path / "target.py")


def test_promptfoo_export_requires_module_object_agent_path(tmp_path):
    suite = EvalSuite(
        name="safety",
        type="output",
        cases=[EvalCase(name="safe answer", input="hello", checks=[])],
    )

    with pytest.raises(ValueError, match="module:object"):
        export_promptfoo_config(
            "examples.customer_support.agent",
            suite,
            tmp_path / "promptfooconfig.yaml",
        )


def test_promptfoo_export_rejects_unsupported_checks(tmp_path):
    suite = EvalSuite(
        name="safety",
        type="output",
        cases=[
            EvalCase(
                name="safe answer",
                input="hello",
                checks=[{"json_schema": {"type": "object"}}],
            )
        ],
    )

    with pytest.raises(ValueError, match="Unsupported check 'json_schema'"):
        export_promptfoo_config(
            "examples.customer_support.agent:agent",
            suite,
            tmp_path / "promptfooconfig.yaml",
        )


def test_promptfoo_export_maps_contains_any_and_refuses_checks(tmp_path):
    suite = EvalSuite(
        name="safety",
        type="output",
        cases=[
            EvalCase(
                name="safe answer",
                input="hello",
                checks=[
                    {"contains_any": ["hi", "hello"]},
                    {"refuses": True},
                ],
            )
        ],
    )

    config_path = export_promptfoo_config(
        "examples.customer_support.agent:agent",
        suite,
        tmp_path / "promptfooconfig.yaml",
    )

    config = yaml.safe_load(config_path.read_text(encoding="utf-8"))
    assert config["tests"][0]["assert"] == [
        {
            "type": "javascript",
            "value": '["hi", "hello"].some((value) => output.includes(value))',
        },
        {
            "type": "javascript",
            "value": "['cannot', \"can't\", 'unable', 'sorry'].some((value) => output.toLowerCase().includes(value))",
        },
    ]


def test_promptfoo_export_maps_regex_checks(tmp_path):
    suite = EvalSuite(
        name="safety",
        type="output",
        cases=[
            EvalCase(
                name="safe answer",
                input="hello",
                checks=[{"regex": r"\\bhello\\b"}],
            )
        ],
    )

    config_path = export_promptfoo_config(
        "examples.customer_support.agent:agent",
        suite,
        tmp_path / "promptfooconfig.yaml",
    )

    config = yaml.safe_load(config_path.read_text(encoding="utf-8"))
    assert config["tests"][0]["assert"] == [
        {
            "type": "javascript",
            "value": 'new RegExp("\\\\\\\\bhello\\\\\\\\b").test(output)',
        }
    ]


def test_promptfoo_export_maps_equals_checks(tmp_path):
    suite = EvalSuite(
        name="safety",
        type="output",
        cases=[
            EvalCase(
                name="safe answer",
                input="hello",
                checks=[{"equals": "hello"}],
            )
        ],
    )

    config_path = export_promptfoo_config(
        "examples.customer_support.agent:agent",
        suite,
        tmp_path / "promptfooconfig.yaml",
    )

    config = yaml.safe_load(config_path.read_text(encoding="utf-8"))
    assert config["tests"][0]["assert"] == [
        {
            "type": "equals",
            "value": "hello",
        }
    ]


def test_promptfoo_export_rejects_invalid_contains_any_value(tmp_path):
    suite = EvalSuite(
        name="safety",
        type="output",
        cases=[
            EvalCase(
                name="safe answer",
                input="hello",
                checks=[{"contains_any": "hello"}],
            )
        ],
    )

    with pytest.raises(ValueError, match="contains_any must be a list"):
        export_promptfoo_config(
            "examples.customer_support.agent:agent",
            suite,
            tmp_path / "promptfooconfig.yaml",
        )


def test_promptfoo_export_rejects_invalid_refuses_value(tmp_path):
    suite = EvalSuite(
        name="safety",
        type="output",
        cases=[
            EvalCase(
                name="safe answer",
                input="hello",
                checks=[{"refuses": "yes"}],
            )
        ],
    )

    with pytest.raises(ValueError, match="refuses must be a boolean"):
        export_promptfoo_config(
            "examples.customer_support.agent:agent",
            suite,
            tmp_path / "promptfooconfig.yaml",
        )


def test_promptfoo_export_rejects_invalid_regex_pattern(tmp_path):
    suite = EvalSuite(
        name="safety",
        type="output",
        cases=[
            EvalCase(
                name="safe answer",
                input="hello",
                checks=[{"regex": "["}],
            )
        ],
    )

    with pytest.raises(ValueError, match="invalid regex"):
        export_promptfoo_config(
            "examples.customer_support.agent:agent",
            suite,
            tmp_path / "promptfooconfig.yaml",
        )
