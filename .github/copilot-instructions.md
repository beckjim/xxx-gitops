# Repository workflow

This repository manages Kubernetes platforms with Argo CD, Kustomize and Helm.
Start with the [repository overview](../README.md),
[bootstrap runbook](../bootstrap/README.md) and
[cluster profiles](../clusters/PROFILES.md). Use these runbooks for detailed
procedures rather than duplicating them in new guidance.

## Making changes

- Inspect the current worktree and relevant manifests before editing. Preserve
  unrelated local changes and do not revive unfinished work from earlier tasks.
- Honor plan-only requests: investigate and propose a plan without editing
  repository files until implementation is approved.
- Establish the target cluster(s), intended behavior and prerequisites. Keep
  cluster-specific settings out of shared components unless all consumers
  should inherit them.
- Follow existing declarative patterns and upstream-supported configuration.
  Update related tests and documentation; avoid unrelated fixes.
- Use the [GitOps instructions](instructions/gitops.instructions.md) for manifests
  and the [testing instructions](instructions/testing.instructions.md) for
  validation.

## Cluster access and authorization

- Read-only diagnostics are allowed when relevant to the task. Establish the
  intended target and pass `--context` explicitly to `kubectl`; do not change
  the user's current context. Target the intended instance explicitly when
  using other cluster tools.
- Repository implementation and plan approval do **not** authorize deployment.
  An explicit request to deploy or repair a live cluster authorizes necessary,
  narrowly scoped writes for that task, without separate confirmation for each
  routine write. Confirm ambiguous targets, scope expansion and unexpected
  destructive operations before acting.
- Git is the desired-state source. Keep authorized live repairs consistent with
  repository ownership; do not make unexplained configuration drift.
- Never print or commit credentials, tokens, private keys or Secret contents.
  Do not dump kubeconfigs. Request only the non-sensitive evidence needed.
- Do not commit or push unless requested.

## Verification and reporting

Run focused validation and inspect the final diff. Separate manifest/chart
rendering from live rollout and functional checks: `Synced` or `Healthy` alone
does not prove the requested behavior works. Report what changed, tests run,
failures or skipped checks, remaining prerequisites, and whether any live change
was actually made. Do not silently suppress errors or claim unverified success.
