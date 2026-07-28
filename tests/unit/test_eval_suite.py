import pytest

from clearagent.evals.suite import EvalCase, EvalSuite


def test_programmatic_suite_requires_at_least_one_case():
    with pytest.raises(ValueError, match="at least 1 item"):
        EvalSuite(name="smoke", cases=[])


def test_programmatic_case_requires_at_least_one_check():
    with pytest.raises(ValueError, match="at least 1 item"):
        EvalCase(name="shipped order", input="Where is A123?", checks=[])


def test_valid_yaml_suite_parses(tmp_path):
    suite_path = tmp_path / "suite.yaml"
    suite_path.write_text(
        """
name: smoke
type: output
description: Smoke tests.
defaults:
  tags: [smoke]
cases:
  - name: shipped order
    input: Where is order A123?
    checks:
      - contains: shipped
""",
        encoding="utf-8",
    )

    suite = EvalSuite.from_yaml(suite_path)

    assert suite.name == "smoke"
    assert suite.cases[0].name == "shipped order"
    assert suite.cases[0].tags == ["smoke"]


def test_yaml_suite_parses_eval_matrix(tmp_path):
    suite_path = tmp_path / "suite.yaml"
    suite_path.write_text(
        """
name: matrix
type: output
matrix:
  models:
    - openai:gpt-4.1-mini
    - openrouter:openai/gpt-4o-mini
  temperatures: [0.0, 0.2]
cases:
  - name: shipped order
    input: Where is order A123?
    checks:
      - trace_provider: openrouter
""",
        encoding="utf-8",
    )

    suite = EvalSuite.from_yaml(suite_path)

    assert suite.matrix["models"] == ["openai:gpt-4.1-mini", "openrouter:openai/gpt-4o-mini"]
    assert suite.matrix["temperatures"] == [0.0, 0.2]


def test_invalid_yaml_suite_raises_clear_error(tmp_path):
    suite_path = tmp_path / "suite.yaml"
    suite_path.write_text("name: smoke\ncases: nope\n", encoding="utf-8")

    with pytest.raises(ValueError, match="cases"):
        EvalSuite.from_yaml(suite_path)


def test_yaml_suite_rejects_zero_cases(tmp_path):
    suite_path = tmp_path / "suite.yaml"
    suite_path.write_text("name: smoke\ncases: []\n", encoding="utf-8")

    with pytest.raises(ValueError, match="cases.*at least one case"):
        EvalSuite.from_yaml(suite_path)


def test_yaml_suite_rejects_case_with_zero_checks(tmp_path):
    suite_path = tmp_path / "suite.yaml"
    suite_path.write_text(
        """
name: smoke
cases:
  - name: shipped order
    input: Where is order A123?
    checks: []
""",
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="checks.*at least one check"):
        EvalSuite.from_yaml(suite_path)


def test_yaml_suite_rejects_non_list_checks(tmp_path):
    suite_path = tmp_path / "suite.yaml"
    suite_path.write_text(
        """
name: smoke
cases:
  - name: shipped order
    input: Where is order A123?
    checks: contains shipped
""",
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="checks.*must be a list"):
        EvalSuite.from_yaml(suite_path)


def test_yaml_suite_rejects_non_mapping_defaults(tmp_path):
    suite_path = tmp_path / "suite.yaml"
    suite_path.write_text(
        """
name: smoke
defaults: smoke
cases:
  - name: shipped order
    input: Where is order A123?
""",
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="Eval suite field 'defaults' must be a mapping"):
        EvalSuite.from_yaml(suite_path)


def test_yaml_suite_rejects_non_mapping_matrix(tmp_path):
    suite_path = tmp_path / "suite.yaml"
    suite_path.write_text(
        """
name: matrix
matrix: openai:gpt-4.1-mini
cases:
  - name: shipped order
    input: Where is order A123?
""",
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="Eval suite field 'matrix' must be a mapping"):
        EvalSuite.from_yaml(suite_path)


def test_yaml_suite_rejects_non_list_matrix_models(tmp_path):
    suite_path = tmp_path / "suite.yaml"
    suite_path.write_text(
        """
name: matrix
matrix:
  models: openai:gpt-4.1-mini
cases:
  - name: shipped order
    input: Where is order A123?
""",
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="Eval suite matrix field 'models' must be a list"):
        EvalSuite.from_yaml(suite_path)


def test_yaml_suite_rejects_non_list_matrix_temperatures(tmp_path):
    suite_path = tmp_path / "suite.yaml"
    suite_path.write_text(
        """
name: matrix
matrix:
  temperatures: 0.2
cases:
  - name: shipped order
    input: Where is order A123?
""",
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="Eval suite matrix field 'temperatures' must be a list"):
        EvalSuite.from_yaml(suite_path)


def test_yaml_suite_missing_name_raises_clear_error(tmp_path):
    suite_path = tmp_path / "suite.yaml"
    suite_path.write_text(
        """
cases:
  - name: shipped order
    input: Where is order A123?
""",
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="Eval suite field 'name' is required"):
        EvalSuite.from_yaml(suite_path)


def test_yaml_suite_case_missing_input_raises_clear_error(tmp_path):
    suite_path = tmp_path / "suite.yaml"
    suite_path.write_text(
        """
name: smoke
cases:
  - name: shipped order
""",
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="Eval case 'shipped order' field 'input' is required"):
        EvalSuite.from_yaml(suite_path)


def test_yaml_suite_rejects_duplicate_case_names(tmp_path):
    suite_path = tmp_path / "suite.yaml"
    suite_path.write_text(
        """
name: smoke
cases:
  - name: shipped order
    input: Where is order A123?
    checks:
      - contains: shipped
  - name: shipped order
    input: Where is order B456?
    checks:
      - contains: shipped
""",
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="Duplicate eval case name 'shipped order'"):
        EvalSuite.from_yaml(suite_path)
