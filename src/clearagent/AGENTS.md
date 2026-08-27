# Runtime Package Instructions

This directory contains the public engine implementation. Keep its interface
provider-neutral and independent of Studio product delivery.

- `agent.py` owns the LangGraph model/tool loop and trace lifecycle.
- `graph/` owns terminating graph topology and node order.
- `builds/` owns planning, eval generation, judging, GEPA, holdout admission,
  and export.
- `runtime/` owns messages, tools, schemas, provider contracts, and adapters.
- `storage/` owns redacted trace persistence; `store.py` owns build records.

Accept dependencies at seams, preserve credential redaction, and add observable
contract tests for runtime or persistence changes. Do not import private Studio
modules here.
