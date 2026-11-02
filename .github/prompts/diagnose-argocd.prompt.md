---
description: "Diagnose Argo CD source permissions, sync, health, CRD ordering, or rollout errors using read-only evidence."
agent: agent
argument-hint: "Kubernetes context, Application name, and observed error"
---
# Diagnose an Argo CD Application

Use the [repository workflow](../copilot-instructions.md) and
[GitOps guide](../instructions/gitops.instructions.md).

Confirm the intended context and Application before live queries. This prompt
authorizes read-only diagnosis, not cluster writes or repository edits.

Trace the reported failure through the Application's source, project, conditions,
sync/operation status and affected child resources. Compare desired manifests
with live state, including AppProject source allowlists and actual bootstrap
ownership. Check CRD availability and nested Application health handling where
relevant. Gather only necessary events, logs and non-sensitive configuration;
do not dump Secrets, tokens or kubeconfigs.

Report the root cause with evidence, affected scope, smallest proposed fix and
exact verification needed. Distinguish permission, rendering, reconciliation and
workload failures; do not claim a later phase succeeded because an earlier error
cleared. If the user subsequently requests live repair, follow the shared
task-level authorization policy; the diagnostic prompt alone grants no writes.
