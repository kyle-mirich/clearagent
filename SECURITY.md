# Security Policy

## Supported Versions

ClearAgent is currently an alpha project without a published release. Security
fixes are applied to `main`. After releases begin, fixes will target the latest
release and `main`; older releases will not receive separate backports unless a
release note says otherwise.

## Report A Vulnerability

Do not open a public issue for a suspected vulnerability or include secrets,
private traces, or exploit details in public discussions.

Use GitHub's private vulnerability reporting flow from the repository's
**Security** tab. Include:

- the affected version or commit
- impact and realistic attack scenario
- minimal reproduction steps
- any suggested mitigation

If private vulnerability reporting is unavailable, contact the maintainer
through a private channel listed on their GitHub profile. You should receive an
acknowledgement within seven days. Timelines for validation and a fix depend on
severity and reproducibility.

## Sensitive Runtime Data

ClearAgent redacts known credential fields before persisting provider request
snapshots, but local traces can still contain prompts, model outputs, and tool
results supplied by an application. Treat `.clearagent/` as sensitive local
data, review trace contents before sharing, and never commit `.env` or SQLite
runtime files.
