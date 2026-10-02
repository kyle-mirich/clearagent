import json

import pytest

from clearagent.builds import pipeline
from clearagent.builds.datasets import generate_synthetic_examples, validate_synthetic_dataset
from clearagent.builds.planner import plan_task


def _task_spec():
    return plan_task("Build an agent that writes concise release notes.").task_spec.model_dump()


def _dataset():
    return generate_synthetic_examples(profile="quick", seed=3, task_spec=_task_spec(), n=30)


def test_generated_layout_passes_integrity_validation():
    validate_synthetic_dataset(_dataset())


def test_duplicate_pairs_are_detected_regardless_of_object_key_order():
    dataset = _dataset()
    first, second = dataset["examples"][:2]
    first["input"] = {"message": "same scenario", "context": "same context"}
    second["input"] = {"context": "same context", "message": "same scenario"}
    second["expected"] = first["expected"].copy()

    with pytest.raises(ValueError, match="duplicate input/expected pairs"):
        validate_synthetic_dataset(dataset)


def test_same_input_cannot_leak_into_holdout_with_a_different_answer():
    dataset = _dataset()
    train = dataset["examples"][0]
    holdout = next(example for example in dataset["examples"] if example["split"] == "test")
    holdout["input"] = train["input"].copy()
    holdout["expected"] = {"answer": "A different reference answer."}

    with pytest.raises(ValueError, match="inputs cannot cross dataset splits"):
        validate_synthetic_dataset(dataset)


def test_unknown_split_cannot_hide_outside_declared_counts():
    dataset = _dataset()
    dataset["examples"].append(
        {
            **dataset["examples"][0],
            "id": "extra",
            "split": "unknown",
            "input": {"message": "An extra uncounted request."},
        }
    )
    dataset["row_count"] += 1

    with pytest.raises(ValueError, match="valid dataset split"):
        validate_synthetic_dataset(dataset)


def test_row_count_must_describe_actual_evidence():
    dataset = _dataset()
    dataset["row_count"] += 1
    with pytest.raises(ValueError, match="row count"):
        validate_synthetic_dataset(dataset)


@pytest.mark.parametrize("duplicate", [False, True])
def test_live_generation_keeps_honest_evidence_when_batches_fail_or_repeat(monkeypatch, duplicate):
    async def fake_complete(_model, _settings, messages, _response_model, **_kwargs):
        request = str(messages[1].content)
        plan = json.loads(request.split("Case plan:\n", 1)[1].split("\n\nReturn exactly", 1)[0])
        if not duplicate and plan[0]["id"].endswith("000"):
            raise RuntimeError("One train batch failed.")
        return pipeline.GeneratedExampleBatch(
            examples=[
                pipeline.GeneratedExample(
                    id=item["id"],
                    input={
                        "message": "Repeated scenario" if duplicate else f"Request {item['id']}"
                    },
                    expected={
                        "answer": "Repeated reference" if duplicate else f"Answer {item['id']}"
                    },
                    reference_notes="Reward a concise response that follows the task.",
                    category=item["category"],
                    difficulty=item["difficulty"],
                    checks=[],
                    required_behavior_ids=item["required_behavior_ids"],
                )
                for item in plan
            ]
        )

    monkeypatch.setattr(pipeline, "_acomplete_structured", fake_complete)
    kwargs = dict(
        profile="quick", seed=3, task_spec=_task_spec(), n=30, settings=pipeline.PipelineSettings()
    )
    if duplicate:
        with pytest.raises(ValueError, match="duplicate input/expected pairs"):
            pipeline._generate_dataset_live(**kwargs)
    else:
        dataset = pipeline._generate_dataset_live(**kwargs)
        assert dataset["row_count"] == len(dataset["examples"]) == 24
        assert dataset["split_counts"] == {"train": 12, "validation": 6, "test": 6}
        validate_synthetic_dataset(dataset)
