import runpy


def test_customer_support_example_runs_as_script(capsys):
    runpy.run_path("examples/customer_support/agent.py", run_name="__main__")

    output = capsys.readouterr().out

    assert "Order A123 has shipped" in output


def test_multinode_example_runs_as_script(capsys):
    runpy.run_path("examples/multinode/flow.py", run_name="__main__")

    output = capsys.readouterr().out

    assert "Here is the final response." in output
