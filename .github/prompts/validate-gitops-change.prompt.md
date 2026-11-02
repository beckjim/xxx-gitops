---
description: "Validate a GitOps diff offline with targeted unittest, Kustomize, and optional pinned Helm chart checks."
agent: agent
argument-hint: "Change, file paths, or revision range; defaults to the current diff"
---
# Validate a GitOps change

Use the [repository workflow](../copilot-instructions.md) and
[testing guide](../instructions/testing.instructions.md).

Inspect the supplied change or current diff. Identify affected entrypoints,
required behavior and non-target invariants. Choose and run the smallest
relevant existing tests/render checks; use pinned network-backed chart checks
when needed to verify actual output.

Check exact resource shapes, scope, effective values and required thresholds.
Compare non-target behavior to the starting revision where appropriate. Diagnose
failures and distinguish existing baseline problems without editing the change
or suppressing assertions.

Report commands, pass/fail/skipped checks, evidence and remaining rollout gates.
Do not query or mutate live clusters, create test Pods, automatically repair
files, commit or push. Fetching pinned public dependencies for offline rendering
is allowed. Static success is not a claim of live deployment success.
