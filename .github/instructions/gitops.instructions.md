---
description: "Use when adding or changing Argo CD Applications, AppProjects, Helm values, Kustomize overlays, namespaces, or bootstrap manifests."
applyTo: "platform/**,platform-overlays/**,projects/**,bootstrap/**"
---
# GitOps changes

Follow the [repository workflow](../copilot-instructions.md) and
[cluster composition guide](../../clusters/PROFILES.md).

## Ownership and scope

- `platform/base` selects shared Applications; `platform/profiles` provides
  infrastructure-specific composition; `platform-overlays/<cluster-id>` supplies
  concrete cluster settings. Bootstrap sources select the exact cluster overlay.
  Inspect the actual entrypoint before adding a resource.
- Register a cluster-only add-on in its overlay, not the shared base. Preserve
  existing add-ons and settings in both target and non-target clusters.
- Reuse existing Application project, destination, finalizer, sync-policy and
  Kustomization conventions. Distinguish the Application namespace from the
  workload destination; keep cluster-scoped resources namespace-free.

## Sources and configuration

- Verify maintained upstream installation guidance and the pinned version's
  chart/schema before choosing values. Prefer the simplest supported deployment;
  do not install an operator unless the selected resources require one.
- Pin external chart/Git dependencies to explicit releases; use verified image
  digests where available. First-party Application revisions must follow the
  repository's intended branch/revision. Do not invent versions or digests.
- Inline Helm `values` are a string: a Kustomize patch replaces that string,
  not its nested keys. Preserve existing settings, account for Argo CD values
  precedence, and render the effective chart output.
- Allowlist the exact Application source in its AppProject. Keep `default`
  deny-all; never add a wildcard repository or broaden RBAC to hide a failure.

## Bootstrap and reconciliation

- AppProjects are bootstrap-managed. A Git allowlist change is not necessarily
  present in the live project; syncing a child Application does not apply it.
  Compare desired and live ownership/state when diagnosing permission errors.
- For an authorized live repair, inspect the proposed diff and update only the
  required bootstrap-owned project/resource. Do not rerun broad bootstrap scripts
  to repair one allowlist entry. See the [runbook](../../bootstrap/README.md).
- Order CRDs, controllers, custom resources and workloads deliberately. Inspect
  bootstrap-installed nested Application health handling and live configuration
  when relevant; waves alone do not guarantee readiness.
- Keep dry-run exceptions narrow. Avoid duplicate controller ownership,
  blanket policy bypasses, privileged-mode workarounds and global AppArmor or
  seccomp changes. Investigate the actual requirement or denial first.
- Document prerequisites, expected resource counts and functional verification,
  plus rollback and pruning/finalizer implications for CRDs and persistent data.
  Never treat zero scheduled Pods as successful device discovery.

Validate actual rendered resources and non-target preservation using the
[testing guide](testing.instructions.md). Do not deploy merely to validate
repository changes.
