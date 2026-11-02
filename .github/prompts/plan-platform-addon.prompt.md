---
description: "Plan an Argo CD-managed platform add-on with explicit cluster scope, upstream prerequisites, validation, and rollback."
agent: plan
argument-hint: "Add-on, target cluster(s), and intended behavior"
---
# Plan a platform add-on

Use the [repository workflow](../copilot-instructions.md) and
[GitOps guide](../instructions/gitops.instructions.md).

Plan the add-on described by the user. Confirm target clusters, purpose,
behavioral choices and host/security prerequisites before finalizing the plan.
Inspect the existing entrypoints and supported upstream installation for the
chosen release; do not assume a controller or driver is already installed.

Propose the simplest supported declarative integration. Identify exact files,
source/image pins, AppProject permissions, namespaces, controller ownership and
CRD/child-health ordering. Include focused tests, non-target invariants, rollout
checks and rollback risks. Clearly mark unverified host/runtime requirements.

Return a scannable implementation plan and unresolved decisions. Do not edit
repository manifests, deploy, commit or push. Creating this plan is not
authorization to implement or deploy it.
