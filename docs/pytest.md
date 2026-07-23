# Pytest

Use `assert_eval_suite_passes` in a normal pytest file:

```python
from clearagent.pytest_plugin import assert_eval_suite_passes
from examples.customer_support.agent import agent


def test_smoke_suite():
    assert_eval_suite_passes(agent, "examples/customer_support/evals/smoke.yaml")
```

Failures are normal `AssertionError`s and include the suite name, case name,
failed checks, run ID, and trace DB path.
