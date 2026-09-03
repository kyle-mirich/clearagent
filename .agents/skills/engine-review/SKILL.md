---
name: clearagent-engine-review
description: Review ClearAgent Engine changes for public scope, LangGraph execution, and contract coverage.
---

# ClearAgent Engine Review

Use this skill when reviewing a change to the public engine.

1. Confirm the changed files stay within the public engine scope; reject
   product-specific frontend code, deployment routes, source ingestion, and
   credential material.
2. Trace the path from the public interface through `Agent`, `StateGraph`, the
   provider adapter, and trace persistence.
3. For build changes, trace goal -> task spec -> dataset -> seed evaluation ->
   GEPA -> holdout -> admission -> selected version.
4. Check that tests assert observable behavior and that failure paths are covered.
5. Run Ruff, mypy, pytest, and the package build before reporting completion.

Report the exact file and line for each finding, separating scope problems from
runtime correctness and test gaps.
