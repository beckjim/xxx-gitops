---
description: "Use when writing or running profile validation, Python unittest tests, mocked bootstrap tests, Kustomize rendering, or pinned Helm chart checks."
applyTo: "clusters/tests/**,clusters/validate-profile.py,clusters/requirements.txt"
---
# Validation

Follow the [repository workflow](../copilot-instructions.md).
Reuse the existing [profile tests](../../clusters/tests/test_profiles.py),
[setup tests](../../clusters/tests/test_setup.py) and
[profile validator](../../clusters/validate-profile.py).

- Use Python `unittest`, existing render helpers, mocks and temporary directories.
  Do not introduce a new testing stack for manifest changes.
- Start with relevant test methods/classes in one runner. Escalate to broader
  suites only as needed. Documentation-only changes need no deployment tests.
- Render real Kustomize overlays and pinned Helm charts with effective values.
  Assert actual kinds, scope, args, mounts, security settings and required counts,
  not just the presence of configuration text.
- Test target behavior and unchanged non-target environments. Check selectors
  against discovery rules and cover meaningful negative cases.
- Keep ordinary tests offline from the cluster. Chart-fetch tests are opt-in
  through `RUN_HELM_TESTS=1` and may need network access to pinned public sources.
  Rendering is not live validation and must not create workloads.
- Use existing dependencies. Install/restore them only after a dependency-manifest
  change or a missing-dependency failure; do not install tools speculatively.
- If a failure appears unrelated, reproduce it on an isolated starting revision.
  Report the baseline evidence rather than weakening assertions or fixing
  unrelated behavior. Do not reset the user's worktree.
- Clean temporary files, avoid bytecode noise and finish with `git diff --check`.

## Existing commands

Select relevant methods/classes first, using their current names in the tests.
Broader suite:

```bash
PYTHONDONTWRITEBYTECODE=1 python3 -m unittest discover -s clusters/tests -p 'test_*.py'
```

Opt-in pinned chart suite:

```bash
RUN_HELM_TESTS=1 PYTHONDONTWRITEBYTECODE=1 \
  python3 -m unittest discover -s clusters/tests -p 'test_profiles.py'
```

Profile validation, after confirming the intended cluster ID:

```bash
python3 clusters/validate-profile.py <cluster-id>
```

The validator renders configuration; it does not reserve LAN addresses, configure
DNS, check physical reachability or deploy the profile. See the
[profile runbook](../../clusters/PROFILES.md) for prerequisites.
