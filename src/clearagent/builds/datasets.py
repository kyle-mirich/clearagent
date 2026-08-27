from typing import Any


PROFILE_SIZES = {
    "quick": (30, 18, 6, 6),
    "standard": (60, 36, 12, 12),
    "deep": (120, 72, 24, 24),
}


def generate_synthetic_examples(
    *,
    profile: str,
    seed: int,
    task_spec: dict[str, Any],
    n: int | None = None,
) -> dict[str, Any]:
    total, train_count, validation_count, test_count = dataset_split_counts(profile, n)
    coverage = task_spec.get("synthetic_coverage_plan") or [
        "typical successful request",
        "missing essential context",
        "ambiguous request",
        "boundary or safety challenge",
        "out-of-scope request",
        "format and tone variation",
    ]
    required_behaviors = (task_spec.get("quality_contract") or {}).get(
        "required_behaviors", []
    )
    required_behavior_ids = [str(behavior["id"]) for behavior in required_behaviors]
    foundation_ids = [
        behavior_id
        for behavior_id in required_behavior_ids
        if behavior_id
        in {"document_grounding", "capability_honesty", "boundary_respect"}
    ]
    domain_behaviors = [
        behavior
        for behavior in required_behaviors
        if str(behavior["id"]) not in foundation_ids
    ]
    split_sizes = {
        "train": train_count,
        "validation": validation_count,
        "test": test_count,
    }
    split_positions = {"train": 0, "validation": 0, "test": 0}
    examples = []
    for index in range(total):
        category = str(coverage[(index + seed) % len(coverage)])
        split = (
            "train"
            if index < train_count
            else "validation"
            if index < train_count + validation_count
            else "test"
        )
        split_position = split_positions[split]
        split_positions[split] += 1
        domain_ids = [str(behavior["id"]) for behavior in domain_behaviors]
        if len(domain_ids) <= 2 * split_sizes[split]:
            distributed = [
                behavior_id
                for behavior_index, behavior_id in enumerate(domain_ids)
                if behavior_index % split_sizes[split] == split_position
            ]
        else:
            distributed = [
                behavior_id
                for behavior_index, behavior_id in enumerate(domain_ids)
                if behavior_index % total == index
            ]
        matching = [
            str(behavior["id"])
            for behavior in domain_behaviors
            if _behavior_category(behavior).lower() == category.lower()
        ]
        if matching:
            distributed = list(
                dict.fromkeys([matching[0], *distributed])
            )[:2]
        assigned_behaviors = list(dict.fromkeys([*foundation_ids, *distributed]))
        examples.append(
            {
                "id": f"ex_{seed}_{index:03d}",
                "input": {
                    "message": f"Synthetic {category} request {index + 1} for {task_spec.get('name', 'the agent')}."
                },
                "expected": _schema_example(
                    task_spec.get("output_schema", {}),
                    f"Satisfy the {category} case while following the task goal and constraints.",
                ),
                "reference_notes": f"Coverage target: {category}.",
                "required_behavior_ids": assigned_behaviors,
                "checks": [
                    {"not_contains": "system prompt"},
                    {"not_contains": "hidden instructions"},
                ],
                "category": category,
                "difficulty": "hard" if index % 3 == 0 else "medium",
                "cluster_id": f"{split}_cluster_{index // 2}",
                "source": "synthetic_layout",
                "split": split,
            }
        )
    foundation_set = set(foundation_ids)
    domain_set = {str(behavior["id"]) for behavior in domain_behaviors}
    for split, split_size in split_sizes.items():
        if len(domain_set) <= 2 * split_size:
            _fill_behavior_coverage(
                [example for example in examples if example["split"] == split],
                domain_set,
                foundation_set,
            )
    _fill_behavior_coverage(examples, domain_set, foundation_set)
    return {
        "source": "synthetic",
        "row_count": total,
        "split_counts": {
            "train": train_count,
            "validation": validation_count,
            "test": test_count,
        },
        "generation_metadata": {
            "seed": seed,
            "profile": profile,
            "requested_size": n,
            "split_strategy": "60/20/20" if n is not None else "profile_default",
            "template_version": "domain-neutral-v2",
            "task_name": task_spec.get("name"),
            "required_behavior_ids": required_behavior_ids,
            "foundation_behavior_ids": foundation_ids,
            "domain_behavior_ids": [str(behavior["id"]) for behavior in domain_behaviors],
        },
        "examples": examples,
    }


def _schema_example(schema: dict[str, Any], text: str) -> dict[str, Any]:
    properties = schema.get("properties") if isinstance(schema, dict) else None
    required = schema.get("required") if isinstance(schema, dict) else None
    if not isinstance(properties, dict) or not isinstance(required, list):
        return {"answer": text}
    payload: dict[str, Any] = {}
    for field in required:
        field_schema = properties.get(field, {})
        value_type = field_schema.get("type") if isinstance(field_schema, dict) else None
        if value_type == "string":
            payload[field] = text
        elif value_type == "integer":
            payload[field] = 0
        elif value_type == "number":
            payload[field] = 0.0
        elif value_type == "boolean":
            payload[field] = False
        elif value_type == "array":
            payload[field] = []
        elif value_type == "object":
            payload[field] = {}
        else:
            payload[field] = text
    return payload or {"answer": text}


def dataset_split_counts(profile: str, n: int | None = None) -> tuple[int, int, int, int]:
    if n is None:
        return PROFILE_SIZES.get(profile, PROFILE_SIZES["standard"])
    validation_count = n // 5
    test_count = n // 5
    train_count = n - validation_count - test_count
    return n, train_count, validation_count, test_count


def validate_synthetic_dataset(dataset: dict[str, Any]) -> None:
    examples = dataset.get("examples") or []
    if not examples:
        raise ValueError("Synthetic dataset must contain examples.")
    ids = [str(example.get("id", "")) for example in examples]
    if any(not example_id for example_id in ids) or len(ids) != len(set(ids)):
        raise ValueError("Synthetic example IDs must be present and unique.")
    fingerprints = [
        repr((example.get("input"), example.get("expected"))) for example in examples
    ]
    if len(fingerprints) != len(set(fingerprints)):
        raise ValueError("Synthetic examples must not contain duplicate input/expected pairs.")
    actual_counts = {
        split: sum(example.get("split") == split for example in examples)
        for split in ("train", "validation", "test")
    }
    if actual_counts != dataset.get("split_counts"):
        raise ValueError("Synthetic dataset split counts do not match its examples.")
    if any(count <= 0 for count in actual_counts.values()):
        raise ValueError("Synthetic datasets require non-empty train, validation, and test splits.")
    cluster_splits: dict[str, set[str]] = {}
    for example in examples:
        cluster_id = str(example.get("cluster_id", "")).strip()
        if not cluster_id:
            raise ValueError("Synthetic examples require a cluster ID.")
        cluster_splits.setdefault(cluster_id, set()).add(str(example.get("split", "")))
    if any(len(splits) != 1 for splits in cluster_splits.values()):
        raise ValueError("Synthetic example clusters cannot cross dataset splits.")
    required_behavior_ids = set(
        dataset.get("generation_metadata", {}).get("required_behavior_ids", [])
    )
    foundation_ids = set(
        dataset.get("generation_metadata", {}).get("foundation_behavior_ids", [])
    )
    domain_ids = set(
        dataset.get("generation_metadata", {}).get("domain_behavior_ids", [])
    )
    all_covered = {
        behavior_id
        for example in examples
        for behavior_id in example.get("required_behavior_ids", [])
    }
    if all_covered != required_behavior_ids:
        raise ValueError("Synthetic cases do not cover every required behavior.")
    for split in ("train", "validation", "test"):
        covered = {
            behavior_id
            for example in examples
            if example.get("split") == split
            for behavior_id in example.get("required_behavior_ids", [])
        }
        expected = (
            required_behavior_ids
            if len(domain_ids) <= 2 * actual_counts[split]
            else foundation_ids
        )
        if not expected.issubset(covered):
            raise ValueError(
                f"Synthetic {split} cases do not meet required behavior coverage."
            )


def _behavior_category(behavior: dict[str, Any]) -> str:
    expectation = str(behavior.get("expectation", ""))
    detail = expectation.split(": ", 1)[-1]
    identifier = str(behavior.get("id", ""))
    if identifier.startswith("business_rule_"):
        return f"business rule: {detail}"
    if identifier.startswith("boundary_"):
        return f"boundary challenge: {detail}"
    return ""


def _fill_behavior_coverage(
    examples: list[dict[str, Any]],
    required_domain_ids: set[str],
    foundation_ids: set[str],
) -> None:
    covered = {
        behavior_id
        for example in examples
        for behavior_id in example["required_behavior_ids"]
        if behavior_id not in foundation_ids
    }
    for behavior_id in sorted(required_domain_ids - covered):
        target = next(
            (
                example
                for example in examples
                if len(set(example["required_behavior_ids"]) - foundation_ids) < 2
            ),
            None,
        )
        if target is None:
            target = min(
                examples,
                key=lambda example: len(set(example["required_behavior_ids"]) - foundation_ids),
            )
        target["required_behavior_ids"].append(behavior_id)
