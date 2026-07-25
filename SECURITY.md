# Security Policy

## Supported Versions

ClearAgent is an alpha project. Security fixes are applied to `main` and, once a
release exists, the latest published alpha. Older alpha releases do not receive
separate backports unless a release note says otherwise.

## Report A Vulnerability

Do not open a public issue for a suspected vulnerability or include secrets,
private traces, or exploit details in public discussions.

GitHub private vulnerability reporting is not currently enabled for this
repository. Use the private email contact linked from the maintainer's
[public profile website](https://kyle-mirich.vercel.app/). Do not include a
vulnerability report in a public issue. Include:

- the affected version or commit
- impact and realistic attack scenario
- minimal reproduction steps
- any suggested mitigation

Reports are handled on a best-effort basis during the alpha. If no
acknowledgement arrives within seven days, send a follow-up through the same
private channel. Timelines for validation and a fix depend on severity and
reproducibility.

## Sensitive Runtime Data

ClearAgent redacts known credential fields before persisting provider request
snapshots, but local traces can still contain prompts, model outputs, and tool
results supplied by an application. Treat `.clearagent/` as sensitive local
data, review trace contents before sharing, and never commit `.env` or SQLite
runtime files.
