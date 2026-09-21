# Cluster Bootstrap Runbook

This runbook defines the minimal manual bootstrap after Argo CD is installed.
Everything after this seed step is managed by Argo CD from Git.

## Goal

- Seed project guardrails first (AppProjects)
- Seed one root Application
- Let root manage platform and workload apps

## Preconditions

- Argo CD is installed in namespace `argocd`
- You have cluster-admin access on the target cluster
- You are on the correct Kubernetes context

## Bootstrap (per cluster)

Run from the repository root.

1. Apply AppProjects (guardrails first):

   ```bash
   kubectl apply -k projects
   ```

2. Apply root application:

   ```bash
   kubectl apply -f bootstrap/root-app.yaml
   ```

## Verify

1. Projects are present:

   ```bash
   kubectl -n argocd get appproject
   ```

2. Root and child apps exist:

   ```bash
   kubectl -n argocd get applications
   ```

3. Reconciliation is healthy:

   ```bash
   argocd app list --refresh -o wide
   ```

## Ongoing Operations

- Do not `kubectl apply` app manifests directly after bootstrap
- Add or change applications via Git PRs only
- Keep `default` AppProject deny-all

## Dev/Test/Prod Usage

Apply the same two-step seed flow to each target cluster context:

- `dev`
- `test`
- `prod`

The same root app then reconciles environment-specific content from Git as your repo evolves.

For one-command onboarding, use:

```bash
./clusters/bootstrap-cluster.sh <kube-context>
```

Examples:

```bash
./clusters/bootstrap-cluster.sh dev
./clusters/bootstrap-cluster.sh test
./clusters/bootstrap-cluster.sh prod
```

For production-like contexts, the helper asks for explicit confirmation.
Use `--yes` for non-interactive automation when appropriate.